"""ModuleNode — installs a Terraform-managed containerized module.

Port of internal/modules/installer.go.

Configuration shape:
  - Stored on the node: module_id, source (git URL with @SHA), module_dir
  - Everything else comes from VarNode parents at resolve time.

The DAG executor delivers parents as:
  - None (no parents)             — error
  - VarResult (one parent)        — single var (rare for modules)
  - dict[parent_id, VarResult]    — many vars (typical)

ModuleNode flattens all VarResult parents into a single tfvars dict keyed
by VarResult.name, then adds intrinsic zp_* vars derived from itself.
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
class ModuleResult:
    """Contract for a module node."""
    module_id: str
    source: str
    module_dir: str = ""
    network_name: str = ""
    variables: Dict[str, str] = field(default_factory=dict)
    main: Optional[str] = None
    containers: Dict[str, ContainerInfo] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _parse_git_source(source: str) -> tuple[str, str]:
    """Split 'https://…/repo.git@<sha>' into (url, sha). Raises on invalid."""
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
    """Convert executor input into {var_name: var_value} dict."""
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
        # Fallback to CLI if docker SDK isn't usable
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
    """Return docker inspect output for a container, or {} if missing."""
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
# ModuleNode
# ---------------------------------------------------------------------------

class ModuleNode(INode[Any, ModuleResult]):
    """Terraform-managed module installation.

    Inputs (from VarNode parents) become terraform variables. Required
    parents are the system VarNodes (``zp_module_storage``, ``zp_arch``,
    ``zp_gpu_vendor``, ``zp_module_id``, ``zp_network_name``) plus any
    user-defined VarNodes for module-specific config.

    Required outputs from the module:
      - ``main``                — name of the primary container
      - ``<container>_ports``   — port mapping for each container (at least one)

    Optional outputs:
      - ``<container>_mounts``  — volume mappings
    """

    # I/O contract: accept anything (dict of VarResults, or single VarResult),
    # produce ModuleResult.

    def __init__(self, module_id: str, source: str):
        self.module_id = module_id
        self.source = source

    # ---- helpers --------------------------------------------------------

    @property
    def network_name(self) -> str:
        """Fallback network name if no zp_network_name parent VarNode is wired."""
        return f"zeropoint-module-{self.module_id}"

    def _module_dir(self, tfvars: Dict[str, str]) -> Path:
        storage = tfvars.get("zp_module_storage")
        if not storage:
            raise RuntimeError(
                "module storage path not provided "
                "(expected VarNode named 'zp_module_storage' as parent)")
        return Path(storage) / self.module_id

    def _build_tfvars(self, input_val: Any) -> Dict[str, str]:
        """All terraform variables come from VarNode parents — no injection."""
        return _flatten_inputs(input_val)

    def _build_result(self, tfvars: Dict[str, str],
                      outputs: Dict[str, dict],
                      network_name: str) -> ModuleResult:
        module_dir = str(self._module_dir(tfvars))
        result = ModuleResult(
            module_id=self.module_id,
            source=self.source,
            module_dir=module_dir,
            network_name=network_name,
            variables=dict(tfvars),
        )

        # Required: 'main'
        main_out = outputs.get("main", {})
        if main_out and "value" in main_out:
            result.main = str(main_out["value"])

        # Collect container metadata from {name}_ports / {name}_mounts.
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

        # Probe docker for live container state.
        for cname, ci in containers.items():
            inspect = _docker_inspect(cname)
            if inspect:
                ci.ip = _extract_container_ip(inspect)
                ci.state = (inspect.get("State") or {}).get("Status")

        result.containers = containers
        return result

    # ---- INode API ------------------------------------------------------

    def resolve(self, input: Any, mode: ResolveMode) -> NodeResult[ModuleResult]:
        if mode == ResolveMode.MOCK:
            mock = ModuleResult(
                module_id=self.module_id,
                source=self.source,
                module_dir=f"/mock/{self.module_id}",
                network_name=self.network_name,
                variables=_flatten_inputs(input),
                main=f"{self.module_id}-main",
                containers={
                    f"{self.module_id}-main": ContainerInfo(
                        name=f"{self.module_id}-main",
                        ports={"http": 8080},
                        ip="172.17.0.42",
                        state="running",
                    )
                },
            )
            return NodeResult.success(mock)

        try:
            url, sha = _parse_git_source(self.source)
            tfvars = self._build_tfvars(input)
            module_dir = self._module_dir(tfvars)

            network_name = tfvars.get("zp_network_name") or self.network_name

            # Clone if missing or stale (no .terraform dir means we haven't init'd).
            if not module_dir.exists():
                logger.info("cloning %s @ %s -> %s", url, sha, module_dir)
                _git_clone_at_sha(url, sha, module_dir)

            # Docker network used by all of the module's containers.
            _ensure_network(network_name)

            # tf init + apply
            tf = TerraformExecutor(module_dir)
            tf.init()
            tf.apply(tfvars)

            outputs = tf.output()
            if "main" not in outputs:
                return NodeResult.failed(
                    "module is missing required terraform output 'main'")

            # Need at least one *_ports output
            has_ports = any(k.endswith("_ports") for k in outputs)
            if not has_ports:
                return NodeResult.failed(
                    "module must declare at least one '<container>_ports' output")

            result = self._build_result(tfvars, outputs, network_name)
            return NodeResult.success(result)

        except (TerraformError, ValueError) as e:
            return NodeResult.failed(str(e))
        except subprocess.CalledProcessError as e:
            return NodeResult.failed(
                f"command failed: {e}\nstderr: {e.stderr}")
        except Exception as e:
            logger.exception("ModuleNode resolve failed")
            return NodeResult.failed(str(e))

    def verify(self, mode: ResolveMode) -> NodeResult[ModuleResult]:
        if mode == ResolveMode.MOCK:
            return NodeResult.success(ModuleResult(
                module_id=self.module_id, source=self.source))

        # verify() has no access to parents, so we can't compute the
        # module dir. Always defer to resolve() — terraform itself will
        # tell us whether we're converged (no-op apply if so).
        return NodeResult.pending_reboot(ModuleResult(
            module_id=self.module_id, source=self.source))

    def remove(self, mode: ResolveMode) -> NodeResult[ModuleResult]:
        if mode == ResolveMode.MOCK:
            return NodeResult.success()
        # remove() also has no access to parents. Without the var values
        # that were used during apply, terraform destroy may be incomplete.
        # The DAG executor should ideally pass input to remove() too;
        # until then, this is best-effort using whatever we can infer.
        try:
            _remove_network(self.network_name)
            return NodeResult.success()
        except Exception as e:
            logger.exception("ModuleNode remove failed")
            return NodeResult.failed(str(e))
