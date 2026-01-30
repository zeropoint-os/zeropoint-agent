from pathlib import Path
from typing import Optional, List, Dict, Any
import json
import os
import sqlite3
import logging

from git import Repo, InvalidGitRepositoryError

from .json_utils import atomic_write_json

logger = logging.getLogger(__name__)


class StateStore:
    """Manage Intent, Reality, and Reconciliation state.
    
    Uses:
    - Git worktrees: .worktree-main (main branch) and .worktree-edit (edit branch)
    - DuckDB: schema for disks, mounts, paths, vars, modules, etc.
    - JSON exports: stable snapshots per resource table
    
    Key invariant: exports/ are the single source of truth for Git history.
    DuckDB is derived and can be rebuilt from exports.
    """

    # Export file paths (relative to worktree root)
    EXPORTS_DIR = "exports"
    EXPORT_FILES = {
        "disks": "disks.json",
        "mounts": "mounts.json",
        "paths": "paths.json",
        "vars": "vars.json",
        "modules": "modules.json",
        "links": "links.json",
        "exposures": "exposures.json",
    }

    # DuckDB schema
    SCHEMA = """
        CREATE TABLE IF NOT EXISTS disks (
            id TEXT PRIMARY KEY,
            device TEXT NOT NULL,
            partition TEXT,
            filesystem TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        
        CREATE TABLE IF NOT EXISTS mounts (
            id TEXT PRIMARY KEY,
            disk_id TEXT NOT NULL,
            mountpoint TEXT NOT NULL,
            options TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (disk_id) REFERENCES disks(id)
        );
        
        CREATE TABLE IF NOT EXISTS paths (
            id TEXT PRIMARY KEY,
            mount_id TEXT NOT NULL,
            path TEXT NOT NULL,
            mode TEXT DEFAULT '0755',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (mount_id) REFERENCES mounts(id)
        );
        
        CREATE TABLE IF NOT EXISTS vars (
            id TEXT PRIMARY KEY,
            value TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        
        CREATE TABLE IF NOT EXISTS modules (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            enabled INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        
        CREATE TABLE IF NOT EXISTS links (
            id TEXT PRIMARY KEY,
            from_module TEXT NOT NULL,
            to_module TEXT NOT NULL,
            bindings TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        
        CREATE TABLE IF NOT EXISTS exposures (
            id TEXT PRIMARY KEY,
            module TEXT NOT NULL,
            protocol TEXT,
            port INTEGER,
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """

    def __init__(self, path: Optional[str] = None, db_path: Optional[str] = None):
        """Initialize StateStore with git worktrees and DuckDB.
        
        Args:
            path: Path to repo root (default: ./state)
            db_path: Path to DuckDB file (default: {path}/.zeropoint.db)
        """
        self.path = Path(path or "state").resolve()
        self.db_path = Path(db_path or str(self.path / ".zeropoint.db"))
        
        logger.info(f"Initializing StateStore at {self.path}")
        
        # Worktree paths
        self.main_repo_path = self.path / ".worktree-main"
        self.edit_repo_path = self.path / ".worktree-edit"
        
        # Repo objects (initialized in _init_repos)
        self.main_repo: Optional[Repo] = None
        self.edit_repo: Optional[Repo] = None
        
        # DuckDB connection
        self.db: Optional[sqlite3.Connection] = None
        
        # Load defaults file path
        self.defaults_file = Path(
            os.environ.get("ZEROPOINT_STORE_DEFAULTS_FILE", "./data/defaults.json")
        ).resolve()
        logger.debug(f"Defaults file: {self.defaults_file}")
        
        self._init_repos()
        self._init_db()
        logger.info("StateStore initialization complete")

    def _init_repos(self) -> None:
        """Initialize git repo and worktrees if missing."""
        self.path.mkdir(parents=True, exist_ok=True)
        logger.debug(f"Initializing git repos at {self.path}")
        
        # Prune stale worktrees
        try:
            existing_repo = Repo(self.path)
            existing_repo.git.worktree("prune")
            logger.debug("Pruned stale worktrees")
        except (InvalidGitRepositoryError, Exception):
            pass
        
        try:
            repo = Repo(self.path)
            logger.debug(f"Found existing repo at {self.path}")
        except InvalidGitRepositoryError:
            # Initialize new repo
            repo = Repo.init(self.path)
            
            # Create .gitignore
            (self.path / ".gitignore").write_text("*.db\n.worktree-*\n")
            
            # Create exports directory with baseline exports (temporary)
            exports_dir = self.path / self.EXPORTS_DIR
            exports_dir.mkdir(exist_ok=True)
            self._write_default_exports(exports_dir)
            
            # Initial commit (this creates master/main depending on git config)
            repo.index.add([".gitignore", self.EXPORTS_DIR])
            repo.index.commit("Initialize state repo with defaults")
            
            # Remove the exports dir from repo root (it was only for initial commit)
            # We'll use worktree exports only
            import shutil
            shutil.rmtree(exports_dir)
        
        # Ensure main branch exists (rename master to main if needed)
        if "main" not in repo.heads:
            if "master" in repo.heads:
                repo.heads.master.rename("main")
        
        # Ensure edit branch exists
        if "edit" not in repo.heads:
            repo.create_head("edit", "main")
        
        # Detach HEAD in repo root (critical: so we can use worktrees)
        try:
            repo.git.checkout("--detach", "main")
        except Exception as e:
            # If detach fails, try a different approach
            try:
                repo.git.symbolic_ref("--delete", "HEAD")
            except Exception:
                pass
        
        # Clean up and recreate worktrees
        for wt_path in [self.main_repo_path, self.edit_repo_path]:
            if wt_path.exists():
                try:
                    repo.git.worktree("remove", "--force", str(wt_path))
                except Exception:
                    pass
        
        # Add worktrees
        repo.git.worktree("add", str(self.main_repo_path), "main")
        repo.git.worktree("add", str(self.edit_repo_path), "edit")
        
        self.main_repo = Repo(self.main_repo_path)
        self.edit_repo = Repo(self.edit_repo_path)

    def _write_default_exports(self, exports_dir: Path) -> None:
        """Write baseline exports from defaults file or fail.
        
        Reads from ZEROPOINT_STORE_DEFAULTS_FILE (default: ./data/defaults.json).
        Fails if file doesn't exist.
        """
        if not self.defaults_file.exists():
            raise FileNotFoundError(
                f"Store not found and defaults file missing.\n"
                f"Expected: {self.defaults_file}\n"
                f"Set ZEROPOINT_STORE_DEFAULTS_FILE=/path/to/defaults.json or "
                f"create {self.defaults_file}"
            )
        
        with open(self.defaults_file) as f:
            defaults = json.load(f)
        
        exports_dir.mkdir(exist_ok=True)
        
        for name, data in defaults.items():
            export_file = exports_dir / self.EXPORT_FILES[name]
            export_file.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(export_file, data)

    def _init_db(self) -> None:
        """Initialize or rebuild DuckDB from exports."""
        # Check if DB exists and is valid
        rebuild = False
        if not self.db_path.exists():
            rebuild = True
        else:
            try:
                conn = sqlite3.connect(str(self.db_path))
                conn.execute("SELECT 1 FROM disks LIMIT 1")
                conn.close()
            except Exception:
                rebuild = True
        
        self.db = sqlite3.connect(str(self.db_path))
        self.db.row_factory = sqlite3.Row
        
        if rebuild:
            # Drop all tables
            cursor = self.db.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = cursor.fetchall()
            for table in tables:
                cursor.execute(f"DROP TABLE IF EXISTS {table[0]}")
            self.db.commit()
            
            # Create schema
            for statement in self.SCHEMA.split(";"):
                if statement.strip():
                    self.db.execute(statement)
            self.db.commit()
            
            # Import from main branch exports
            self._import_from_branch("main")

    def _import_from_branch(self, branch: str) -> None:
        """Import exports from a branch into DuckDB."""
        if branch == "main":
            branch_path = self.main_repo_path
        elif branch == "edit":
            branch_path = self.edit_repo_path
        else:
            raise ValueError(f"Unknown branch: {branch}")
        
        for table_name, export_file in self.EXPORT_FILES.items():
            export_path = branch_path / self.EXPORTS_DIR / export_file
            if not export_path.exists():
                continue
            
            with open(export_path) as f:
                rows = json.load(f)
            
            if not rows:
                continue
            
            # Clear table
            self.db.execute(f"DELETE FROM {table_name}")
            
            # Insert rows
            columns = rows[0].keys()
            placeholders = ", ".join(["?"] * len(columns))
            for row in rows:
                values = [row.get(col) for col in columns]
                self.db.execute(
                    f"INSERT INTO {table_name} ({', '.join(columns)}) VALUES ({placeholders})",
                    values
                )
            
            self.db.commit()

    def get_desired_resources(self, table: str) -> List[Dict[str, Any]]:
        """Get resources from EDIT branch (desired state)."""
        # Switch to edit in DB context
        self._import_from_branch("edit")
        cursor = self.db.execute(f"SELECT * FROM {table} ORDER BY id")
        return [dict(row) for row in cursor.fetchall()]

    def get_actual_resources(self, table: str) -> List[Dict[str, Any]]:
        """Get resources from MAIN branch (applied state)."""
        # Switch to main in DB context
        self._import_from_branch("main")
        cursor = self.db.execute(f"SELECT * FROM {table} ORDER BY id")
        return [dict(row) for row in cursor.fetchall()]

    def write_to_edit(self, table: str, rows: List[Dict[str, Any]], message: str) -> str:
        """Write rows to EDIT branch and commit.
        
        Returns: short commit SHA (intent_id)
        """
        export_file = self.edit_repo_path / self.EXPORTS_DIR / self.EXPORT_FILES[table]
        export_file.parent.mkdir(parents=True, exist_ok=True)
        
        # Write atomically
        atomic_write_json(export_file, rows)
        
        # Stage and commit
        rel_path = os.path.relpath(str(export_file), str(self.edit_repo_path))
        self.edit_repo.index.add([rel_path])
        commit = self.edit_repo.index.commit(message)
        
        # Return short SHA
        return commit.hexsha[:10]

    def merge_to_main(self, commit_sha: str) -> None:
        """Fast-forward merge a commit from edit to main."""
        try:
            self.main_repo.git.merge(commit_sha, ff_only=True)
        except Exception as e:
            raise RuntimeError(f"Failed to merge {commit_sha} to main: {e}")
        
        # Reimport to update DB
        self._import_from_branch("main")

    def get_edit_status(self) -> Dict[str, Any]:
        """Get status of edit branch vs main."""
        try:
            ahead = list(self.edit_repo.iter_commits("edit", "^main"))
            return {
                "ahead_count": len(ahead),
                "latest_commit": ahead[0].hexsha[:10] if ahead else None,
                "latest_message": ahead[0].message.strip() if ahead else None,
            }
        except Exception as e:
            return {"error": str(e)}
