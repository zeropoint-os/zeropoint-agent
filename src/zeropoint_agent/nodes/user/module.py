"""Terraform — installs a Terraform-managed containerized module.

Port of internal/modules/installer.go.

This node is purely the *terraform runner*. It has no constructor-level
config beyond the git source URL. Everything else (module_id, network
name, storage path, user vars) flows in from Var parents.

The DAG executor delivers parents as a dict {parent_id: parent_output}
because Terraform always has multiple parents:
  - its enclosing Namespace (provides path)
  - the per-module/system Vars (provide tfvars)

Terraform flattens VarResult parents into a single tfvars dict
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
from typing import Any, ClassVar, Dict, Optional

from zeropoint_agent.inode import INode, NodeResult, ResolveMode, readonly
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
    """Contract for a Terraform."""
    source: str
    module_id: str = ""
    module_dir: str = ""
    network_name: str = ""
    variables: Dict[str, str] = field(default_factory=dict)
    main: Optional[str] = None
    containers: Dict[str, ContainerInfo] = field(default_factory=dict)
    # Raw terraform outputs as {name: value}. Complex values (dicts, lists)
    # are preserved as-is. Vars with from_output=<name> read this dict.
    outputs: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _parse_git_source(source: str) -> tuple[str, str]:
    """Split 'https://…/repo.git@<sha>' into (url, sha). Raises on invalid.

    Every module source MUST be a git URL pinned to a 40-character commit
    SHA. Local paths, branches, tags and HEAD are not allowed: they would
    make terraform state non-reproducible and the on-disk module dir
    non-canonical. The agent owns the working directory; the source URL
    is purely a pointer to immutable upstream content.
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
# Terraform
# ---------------------------------------------------------------------------

@dataclass
class Terraform(INode[Any, TerraformResult]):
    """A terraform-managed module install.

    Carries only the git source URL. Everything else is read from
    Var parents at resolve time.
    """

    # No editable fields on this class — config flows from Var parents.
    # Hence type-level veto on `w`. Still deletable (calls remove()).
    default_perms: ClassVar[str] = "r-d"

    source: str = readonly()

    def _required(self, tfvars: Dict[str, str], key: str) -> str:
        v = tfvars.get(key)
        if not v:
            raise RuntimeError(
                f"required system var {key!r} not provided "
                f"(expected a Var parent with this name)")
        return v

    def _module_dir(self, tfvars: Dict[str, str]) -> Path:
        """The agent's working dir for this module — terraform state lives here.

        Comes from the `zp_module_dir` DirectoryVar injected by the
        module installer. If the user edits that DirectoryVar, the
        directory has already been moved by the DirectoryVar's
        on_config_changed hook by the time we get here; we just see
        the new path.
        """
        return Path(self._required(tfvars, "zp_module_dir"))

    def _build_result(self, tfvars: Dict[str, str],
                      outputs: Dict[str, dict]) -> TerraformResult:
        result = TerraformResult(
            source=self.source,
            module_id=self._required(tfvars, "zp_module_id"),
            module_dir=str(self._module_dir(tfvars)),
            network_name=self._required(tfvars, "zp_network_name"),
            variables=dict(tfvars),
            # Flatten {name: {sensitive, type, value}} → {name: value} so
            # Vars downstream can read outputs directly.
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
            url, sha = _parse_git_source(self.source)

            # zp_module_dir's DirectoryVar handles any directory moves on
            # edit, so by the time we get here the dir is at module_dir
            # (or doesn't exist yet, on first install).
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
            logger.exception("Terraform resolve failed")
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
