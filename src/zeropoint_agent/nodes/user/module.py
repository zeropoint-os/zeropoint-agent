"""TerraformNode — installs a Terraform-managed containerized module.

Port of internal/modules/installer.go.

This node is purely the *terraform runner*. It has no constructor-level
config beyond the git source URL. Everything else (module_id, network
name, storage path, user vars) flows in from VarNode parents.

The DAG executor delivers parents as a dict {parent_id: parent_output}
because TerraformNode always has multiple parents:
  - its enclosing NamespaceNode (provides path)
  - the per-module/system VarNodes (provide tfvars)

TerraformNode flattens VarResult parents into a single tfvars dict
keyed by VarResult.name. NamespaceResult parents are ignored at the
tfvars level (they're only there for path/structure).
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from zeropoint_agent.inode import INode, NodeResult, ResolveMode
from zeropoint_agent.nodes.config.var import VarResult
from zeropoint_agent.nodes.config.namespace import NamespaceResult
from zeropoint_agent.terraform import TerraformError, TerraformExecutor

logger = logging.getLogger(__name__)

# 40-char hex commit SHA. No branches, tags, or HEAD allowed.
_COMMIT_SHA = re.compile(r"^[a-fA-F0-9]{40}$")


@dataclass
class ContainerInfo:
    """A container produced by the module's terraform."""
    name: str
    ports: Dict[str, Any] = field(default_factory=dict)
    mounts: Dict[str, Any] = field(default_factory=dict)
    ip: Optional[str] = None
    state: Optional[str] = None


@dataclass
class TerraformResult:
    """Contract for a TerraformNode."""
    source: str
    module_id: str = ""
    module_dir: str = ""
    network_name: str = ""
    variables: Dict[str, str] = field(default_factory=dict)
    main: Optional[str] = None
    containers: Dict[str, ContainerInfo] = field(default_factory=dict)
    # Raw terraform outputs as {name: value}. Complex values (dicts, lists)
    # are preserved as-is. VarNodes with from_output=<name> read this dict.
    outputs: Dict[str, Any] = field(default_factory=dict)


# Back-compat alias for the old name; remove once nothing imports it.
ModuleResult = TerraformResult


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _is_local_source(source: str) -> bool:
    """True if source refers to a local filesystem path, not a git URL."""
    if source.startswith("file://"):
        return True
    if source.startswith(("/", "./", "../", "~/")):
        return True
    return False


def _local_source_path(source: str) -> Path:
    """Resolve a local source string to an absolute Path."""
    s = source[len("file://"):] if source.startswith("file://") else source
    p = Path(s).expanduser().resolve()
    if not p.exists():
        raise ValueError(f"local module source does not exist: {p}")
    if not p.is_dir():
        raise ValueError(f"local module source is not a directory: {p}")
    return p


def _parse_git_source(source: str) -> tuple[str, str]:
    """Split 'https://…/repo.git@<sha>' into (url, sha). Raises on invalid.

    For local paths use `_is_local_source` + `_local_source_path` instead.
    """
    if "@" not in source:
        raise ValueError(
            f"module source must include @<commit-sha>: {source!r}")
    url, _, ref = source.rpartition("@")
    if not _COMMIT_SHA.match(ref):
        raise ValueError(
            f"module source ref must be a 40-character commit SHA "
            f"(got {ref!r}); branches/tags/HEAD are not allowed")
    return url, ref


def _flatten_inputs(input_val: Any) -> Dict[str, str]:
    """Convert executor input into {var_name: var_value} dict.

    NamespaceResult entries are ignored (they're for path, not tfvars).
    """
    if input_val is None:
        return {}
    if isinstance(input_val, VarResult):
        return {input_val.name: input_val.value}
    if isinstance(input_val, dict):
        out: Dict[str, str] = {}
        for v in input_val.values():
            if isinstance(v, VarResult):
                out[v.name] = v.value
        return out
    return {}


def _ensure_network(name: str) -> None:
    """Create a docker bridge network if it doesn't exist."""
    try:
        import docker  # type: ignore
        client = docker.from_env()
        existing = {n.name for n in client.networks.list()}
        if name not in existing:
            client.networks.create(name, driver="bridge")
            logger.info("created docker network %s", name)
    except Exception as e:
        check = subprocess.run(
            ["docker", "network", "inspect", name],
            capture_output=True, text=True)
        if check.returncode == 0:
            return
        create = subprocess.run(
            ["docker", "network", "create", name],
            capture_output=True, text=True)
        if create.returncode != 0:
            raise RuntimeError(
                f"failed to create docker network {name}: {create.stderr}"
            ) from e


def _remove_network(name: str) -> None:
    try:
        subprocess.run(
            ["docker", "network", "rm", name],
            capture_output=True, text=True, timeout=10)
    except Exception:
        pass


def _git_clone_at_sha(url: str, sha: str, target: Path) -> None:
    """Clone the repo and check out the exact commit SHA."""
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", "--quiet", url, str(target)],
        check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "checkout", "--quiet", sha],
        cwd=str(target), check=True, capture_output=True, text=True)
    shutil.rmtree(target / ".git", ignore_errors=True)


def _docker_inspect(name: str) -> Dict[str, Any]:
    try:
        import docker  # type: ignore
        c = docker.from_env().containers.get(name)
        return c.attrs or {}
    except Exception:
        proc = subprocess.run(
            ["docker", "inspect", name],
            capture_output=True, text=True)
        if proc.returncode != 0:
            return {}
        import json
        try:
            data = json.loads(proc.stdout)
            return data[0] if data else {}
        except Exception:
            return {}


def _extract_container_ip(inspect: Dict[str, Any]) -> Optional[str]:
    nets = (inspect.get("NetworkSettings") or {}).get("Networks") or {}
    for net in nets.values():
        ip = net.get("IPAddress")
        if ip:
            return ip
    return None


# ---------------------------------------------------------------------------
# TerraformNode
# ---------------------------------------------------------------------------

class TerraformNode(INode[Any, TerraformResult]):
    """A terraform-managed module install.

    Carries only the git source URL. Everything else is read from
    VarNode parents at resolve time.
    """

    # No editable fields on this class — config flows from VarNode parents.
    # Hence type-level veto on `w`. Still deletable (calls remove()).
    default_perms = "r-d"

    def __init__(self, source: str):
        self.source = source

    def _required(self, tfvars: Dict[str, str], key: str) -> str:
        v = tfvars.get(key)
        if not v:
            raise RuntimeError(
                f"required system var {key!r} not provided "
                f"(expected a VarNode parent with this name)")
        return v

    def _module_dir(self, tfvars: Dict[str, str]) -> Path:
        storage = self._required(tfvars, "zp_module_storage")
        module_id = self._required(tfvars, "zp_module_id")
        return Path(storage) / module_id

    def _build_result(self, tfvars: Dict[str, str],
                      outputs: Dict[str, dict]) -> TerraformResult:
        result = TerraformResult(
            source=self.source,
            module_id=self._required(tfvars, "zp_module_id"),
            module_dir=str(self._module_dir(tfvars)),
            network_name=self._required(tfvars, "zp_network_name"),
            variables=dict(tfvars),
            # Flatten {name: {sensitive, type, value}} → {name: value} so
            # VarNodes downstream can read outputs directly.
            outputs={k: (v.get("value") if isinstance(v, dict) else v)
                     for k, v in outputs.items()},
        )

        main_out = outputs.get("main", {})
        if main_out and "value" in main_out:
            result.main = str(main_out["value"])

        containers: Dict[str, ContainerInfo] = {}
        for key, meta in outputs.items():
            val = meta.get("value")
            if key.endswith("_ports"):
                cname = key[: -len("_ports")]
                ci = containers.setdefault(cname, ContainerInfo(name=cname))
                if isinstance(val, dict):
                    ci.ports = val
            elif key.endswith("_mounts"):
                cname = key[: -len("_mounts")]
                ci = containers.setdefault(cname, ContainerInfo(name=cname))
                if isinstance(val, dict):
                    ci.mounts = val

        for cname, ci in containers.items():
            inspect = _docker_inspect(cname)
            if inspect:
                ci.ip = _extract_container_ip(inspect)
                ci.state = (inspect.get("State") or {}).get("Status")

        result.containers = containers
        return result

    def resolve(self, input: Any, mode: ResolveMode) -> NodeResult[TerraformResult]:
        tfvars = _flatten_inputs(input)

        if mode == ResolveMode.MOCK:
            module_id = tfvars.get("zp_module_id", "mock-module")
            mock = TerraformResult(
                source=self.source,
                module_id=module_id,
                module_dir=f"/mock/{module_id}",
                network_name=tfvars.get("zp_network_name", f"zeropoint-module-{module_id}"),
                variables=tfvars,
                main=f"{module_id}-main",
                containers={
                    f"{module_id}-main": ContainerInfo(
                        name=f"{module_id}-main",
                        ports={"http": 8080},
                        ip="172.17.0.42",
                        state="running",
                    )
                },
            )
            return NodeResult.success(mock)

        try:
            module_dir = self._module_dir(tfvars)
            network_name = self._required(tfvars, "zp_network_name")

            if _is_local_source(self.source):
                # Local module: terraform runs from the source path directly
                # (no clone, no copy). Edits to the local tree are picked up
                # on the next resolve. State (.terraform/, *.tfstate) lives
                # under the local path too — module_storage isn't used for
                # local sources beyond providing zp_module_storage to the
                # module's variables.
                tf_cwd = _local_source_path(self.source)
                logger.info("using local module at %s", tf_cwd)
            else:
                url, sha = _parse_git_source(self.source)
                if not module_dir.exists():
                    logger.info("cloning %s @ %s -> %s", url, sha, module_dir)
                    _git_clone_at_sha(url, sha, module_dir)
                tf_cwd = module_dir

            _ensure_network(network_name)

            tf = TerraformExecutor(tf_cwd)
            tf.init()
            tf.apply(tfvars)

            outputs = tf.output()
            if "main" not in outputs:
                return NodeResult.failed(
                    "module is missing required terraform output 'main'")

            if not any(k.endswith("_ports") for k in outputs):
                return NodeResult.failed(
                    "module must declare at least one '<container>_ports' output")

            return NodeResult.success(self._build_result(tfvars, outputs))

        except (TerraformError, ValueError) as e:
            return NodeResult.failed(str(e))
        except subprocess.CalledProcessError as e:
            return NodeResult.failed(
                f"command failed: {e}\nstderr: {e.stderr}")
        except Exception as e:
            logger.exception("TerraformNode resolve failed")
            return NodeResult.failed(str(e))

    def verify(self, mode: ResolveMode) -> NodeResult[TerraformResult]:
        if mode == ResolveMode.MOCK:
            return NodeResult.success(TerraformResult(source=self.source))
        # verify() can't compute paths from inputs (no parent access),
        # so always defer to resolve(); terraform itself no-ops if already
        # converged.
        return NodeResult.pending_reboot(TerraformResult(source=self.source))

    def remove(self, mode: ResolveMode) -> NodeResult[TerraformResult]:
        if mode == ResolveMode.MOCK:
            return NodeResult.success()
        # remove() doesn't have parent access. A remove orchestrator
        # walking the namespace can clean up the module dir + network
        # after destroy. (TODO: thread input into remove() too.)
        return NodeResult.success()


# Back-compat alias.
ModuleNode = TerraformNode
