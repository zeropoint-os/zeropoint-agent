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
    
    Singleton: Only one instance should exist per process.
    """

    # Singleton instance
    _instance: Optional['StateStore'] = None

    # Export file paths (relative to worktree root)
    EXPORTS_DIR = "exports"
    EXPORT_FILES = {
        "disks": "disks.json",
        "partitions": "partitions.json",
        "formats": "formats.json",
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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        
        CREATE TABLE IF NOT EXISTS partitions (
            disk_id TEXT NOT NULL,
            "index" INTEGER NOT NULL,
            size_mb INTEGER,
            type TEXT DEFAULT 'primary',
            label TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (disk_id, "index"),
            FOREIGN KEY (disk_id) REFERENCES disks(id)
        );
        
        CREATE TABLE IF NOT EXISTS formats (
            disk_id TEXT NOT NULL,
            partition_index INTEGER NOT NULL,
            filesystem TEXT DEFAULT 'ext4',
            confirm_wipe INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (disk_id, partition_index),
            FOREIGN KEY (disk_id, partition_index) REFERENCES partitions(disk_id, "index")
        );
        
        CREATE TABLE IF NOT EXISTS mounts (
            id TEXT PRIMARY KEY,
            disk_id TEXT NOT NULL,
            partition_index INTEGER NOT NULL,
            mountpoint TEXT NOT NULL,
            options TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (disk_id, partition_index) REFERENCES partitions(disk_id, "index")
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
        
        Note: Use StateStore.get_instance() for singleton access.
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
        
        # Always ensure schema exists (CREATE TABLE IF NOT EXISTS is safe)
        for statement in self.SCHEMA.split(";"):
            if statement.strip():
                self.db.execute(statement)
        self.db.commit()
        
        if rebuild:
            # Drop all tables and recreate
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
    @classmethod
    def get_instance(cls) -> 'StateStore':
        """Get the singleton StateStore instance.
        
        Returns the existing instance if available, or raises an error.
        The instance should be created during app startup.
        """
        if cls._instance is None:
            raise RuntimeError("StateStore not initialized. Call StateStore.set_instance() first.")
        return cls._instance

    @classmethod
    def set_instance(cls, instance: 'StateStore') -> None:
        """Set the singleton instance (called by app startup)."""
        cls._instance = instance

    def get_boot_drive_config(self) -> Optional[Dict[str, str]]:
        """Get boot drive configuration from DuckDB.
        
        Returns:
            {
                "disk_device": "/dev/nvme0n1",
                "partition_device": "/dev/nvme0n1p2"
            }
            or None if not configured.
        """
        try:
            if not self.db:
                return None
            
            cursor = self.db.cursor()
            
            # Look for the root mount in the mounts table, join with partitions to get device
            cursor.execute("""
                SELECT p.device FROM mounts m
                JOIN partitions p ON m.partition_id = p.id
                WHERE m.id = 'root' OR m.mountpoint = '/'
                LIMIT 1
            """)
            
            row = cursor.fetchone()
            if not row:
                logger.debug("No boot mount configured")
                return None
            
            partition_device = row[0]
            logger.debug(f"Boot partition device: {partition_device}")
            
            # Extract disk device by removing partition suffix
            # /dev/nvme0n1p2 -> /dev/nvme0n1, /dev/sda2 -> /dev/sda
            if "nvme" in partition_device:
                disk_device = partition_device.rsplit("p", 1)[0]
            else:
                disk_device = partition_device.rstrip("0123456789")
            
            logger.debug(f"Boot disk device: {disk_device}")
            
            return {
                "disk_device": disk_device,
                "partition_device": partition_device,
            }
        except Exception as e:
            logger.error(f"Error getting boot drive config: {e}", exc_info=True)
            return None

    # Managed Disk Methods
    def get_managed_disks(self) -> List[str]:
        """Get list of managed disk IDs (those defined in the disks table) from edit branch."""
        self._import_from_branch("edit")
        cursor = self.db.cursor()
        cursor.execute("SELECT id FROM disks ORDER BY id")
        return [row[0] for row in cursor.fetchall()]

    def add_managed_disk(self, disk_id: str) -> None:
        """Register a disk as managed in edit branch (just ensure it exists in disks table)."""
        self._import_from_branch("edit")
        
        # Check if disk exists in reality
        from .hw_probe import HWProbe
        if not HWProbe.get_disk(disk_id):
            raise ValueError(f"Disk not found: {disk_id}")
        
        disk = HWProbe.get_disk(disk_id)
        
        # Insert or update disk record
        self.db.execute(
            "INSERT OR REPLACE INTO disks (id, device) VALUES (?, ?)",
            (disk_id, disk.device)
        )
        self.db.commit()
        
        # Export disks to edit branch
        disks = self._get_table_data("disks")
        self.write_to_edit("disks", disks, f"Add managed disk: {disk_id}")

    def remove_managed_disk(self, disk_id: str) -> None:
        """Unregister a disk from management in edit branch."""
        self._import_from_branch("edit")
        
        # Remove disk and all related partitions, formats
        self.db.execute("DELETE FROM formats WHERE disk_id = ?", (disk_id,))
        self.db.execute("DELETE FROM partitions WHERE disk_id = ?", (disk_id,))
        self.db.execute("DELETE FROM disks WHERE id = ?", (disk_id,))
        self.db.commit()
        
        # Export updated tables to edit branch
        for table in ["disks", "partitions", "formats"]:
            data = self._get_table_data(table)
            self.write_to_edit(table, data, f"Remove managed disk: {disk_id}")

    # Partition Methods
    def get_partitions(self, disk_id: str) -> List[Dict[str, Any]]:
        """Get partitions for a disk from edit branch."""
        self._import_from_branch("edit")
        cursor = self.db.cursor()
        cursor.execute(
            "SELECT disk_id, \"index\", size_mb, type, label FROM partitions WHERE disk_id = ? ORDER BY \"index\"",
            (disk_id,)
        )
        return [dict(zip(["disk_id", "index", "size_mb", "type", "label"], row)) 
                for row in cursor.fetchall()]

    def write_partitions(self, disk_id: str, partitions: List[Dict[str, Any]]) -> None:
        """Write/update partition layout for a disk to edit branch."""
        self._import_from_branch("edit")
        
        # Clear existing partitions for this disk
        self.db.execute("DELETE FROM partitions WHERE disk_id = ?", (disk_id,))
        
        # Insert new partitions
        for part in partitions:
            self.db.execute(
                """INSERT INTO partitions (disk_id, "index", size_mb, type, label)
                   VALUES (?, ?, ?, ?, ?)""",
                (disk_id, part.get("index"), part.get("size_mb"), 
                 part.get("type", "primary"), part.get("label"))
            )
        
        self.db.commit()
        
        # Export to edit branch
        partitions_data = self._get_table_data("partitions")
        self.write_to_edit("partitions", partitions_data, 
                          f"Set partitions for {disk_id}: {len(partitions)} partition(s)")

    def update_partitions(self, disk_id: str, updates: Dict[int, Dict[str, Any]]) -> None:
        """Merge updates into existing partitions (PATCH operation)."""
        self._import_from_branch("edit")
        
        # Read current partitions
        current = self.get_partitions(disk_id)
        current_by_index = {p["index"]: p for p in current}
        
        # Apply updates
        for index, update_data in updates.items():
            if index in current_by_index:
                current_by_index[index].update(update_data)
        
        # Write back all partitions
        updated_partitions = list(current_by_index.values())
        self.write_partitions(disk_id, updated_partitions)

    # Format Methods
    def get_formats(self, disk_id: str) -> Dict[int, Dict[str, Any]]:
        """Get format configs for all partitions on a disk from edit branch."""
        self._import_from_branch("edit")
        cursor = self.db.cursor()
        cursor.execute(
            "SELECT partition_index, filesystem, confirm_wipe FROM formats WHERE disk_id = ? ORDER BY partition_index",
            (disk_id,)
        )
        return {row[0]: {"filesystem": row[1], "confirm_wipe": bool(row[2])}
                for row in cursor.fetchall()}

    def write_format(self, disk_id: str, partition_index: int, 
                    filesystem: str = "ext4", confirm_wipe: bool = False) -> None:
        """Write format config for a partition to edit branch."""
        self._import_from_branch("edit")
        
        # Verify partition exists
        cursor = self.db.cursor()
        cursor.execute(
            "SELECT 1 FROM partitions WHERE disk_id = ? AND \"index\" = ?",
            (disk_id, partition_index)
        )
        if not cursor.fetchone():
            raise ValueError(f"Partition {disk_id}:{partition_index} does not exist")
        
        # Insert or replace format
        self.db.execute(
            """INSERT OR REPLACE INTO formats (disk_id, partition_index, filesystem, confirm_wipe)
               VALUES (?, ?, ?, ?)""",
            (disk_id, partition_index, filesystem, int(confirm_wipe))
        )
        self.db.commit()
        
        # Export to edit branch
        formats_data = self._get_table_data("formats")
        self.write_to_edit("formats", formats_data,
                          f"Set format for {disk_id}:{partition_index} to {filesystem}")

    def get_state(self) -> Dict[str, Any]:
        """Get overall state comparing desired (edit) vs current (main).
        
        Returns structure: {
            "disks": {
                "disk_id": {
                    "action": "added|removed|edited|unchanged",
                    "state": "pending|current",
                    "desired": {...},
                    "current": {...}
                }
            },
            "partitions": {...},
            "formats": {...}
        }
        """
        # Load all exports from both branches
        desired = self._load_all_exports("edit")
        current = self._load_all_exports("main")
        
        result = {}
        
        # Compare each table
        for table in ["disks", "partitions", "formats", "mounts", "paths", "vars", "modules", "links", "exposures"]:
            result[table] = {}
            
            desired_by_key = {self._get_resource_key(table, r): r for r in desired.get(table, [])}
            current_by_key = {self._get_resource_key(table, r): r for r in current.get(table, [])}
            
            all_keys = set(desired_by_key.keys()) | set(current_by_key.keys())
            
            for key in all_keys:
                desired_resource = desired_by_key.get(key)
                current_resource = current_by_key.get(key)
                
                # Determine action and state
                if desired_resource and current_resource:
                    if desired_resource == current_resource:
                        action = "unchanged"
                    else:
                        action = "edited"
                    state = "current"
                elif desired_resource and not current_resource:
                    action = "added"
                    state = "pending"
                elif not desired_resource and current_resource:
                    action = "removed"
                    state = "pending"
                else:
                    continue  # Skip impossible case
                
                result[table][key] = {
                    "action": action,
                    "state": state,
                    "desired": desired_resource,
                    "current": current_resource
                }
        
        return result
    
    def _load_all_exports(self, branch: str) -> Dict[str, List[Dict[str, Any]]]:
        """Load all exports from a branch into a dict."""
        if branch == "main":
            branch_path = self.main_repo_path
        elif branch == "edit":
            branch_path = self.edit_repo_path
        else:
            raise ValueError(f"Unknown branch: {branch}")
        
        result = {}
        for table_name, export_file in self.EXPORT_FILES.items():
            export_path = branch_path / self.EXPORTS_DIR / export_file
            result[table_name] = []
            
            if not export_path.exists():
                continue
            
            try:
                with open(export_path) as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        result[table_name] = data
            except (json.JSONDecodeError, IOError):
                logger.warning(f"Failed to load {export_path}")
        
        return result
    
    def _get_resource_key(self, table: str, resource: Dict[str, Any]) -> str:
        """Get unique key for a resource in a table."""
        if table == "disks":
            return resource.get("id", "")
        elif table == "partitions":
            return f"{resource.get('disk_id', '')}:{resource.get('index', '')}"
        elif table == "formats":
            return f"{resource.get('disk_id', '')}:{resource.get('partition_index', '')}"
        elif table == "mounts":
            return resource.get("id", "")
        elif table == "paths":
            return resource.get("id", "")
        elif table == "vars":
            return resource.get("id", "")
        elif table == "modules":
            return resource.get("id", "")
        elif table == "links":
            return resource.get("id", "")
        elif table == "exposures":
            return resource.get("id", "")
        else:
            return str(resource)
    
    # Helper method
    def _get_table_data(self, table: str) -> List[Dict[str, Any]]:
        """Get all rows from a table as list of dicts."""
        cursor = self.db.cursor()
        cursor.execute(f"SELECT * FROM {table} ORDER BY rowid")
        
        # Get column names
        cursor.execute(f"PRAGMA table_info({table})")
        columns = [row[1] for row in cursor.fetchall()]
        
        # Fetch and convert rows
        cursor.execute(f"SELECT * FROM {table} ORDER BY rowid")
        rows = []
        for row in cursor.fetchall():
            row_dict = {col: val for col, val in zip(columns, row)}
            # Remove timestamps for export
            row_dict.pop("created_at", None)
            rows.append(row_dict)
        
        return rows