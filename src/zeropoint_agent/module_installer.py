"""Module installer — wires a terraform module into the DAG.

After this returns, calling `dag.resolve()` will run terraform.

Layout produced under `modules/<module_id>`:

    modules/<module_id>                          Namespace
    ├── zp_module_id                             NamespacedVar  spec="leaf"
    ├── zp_network_name                          NamespacedVar  spec="zeropoint-module-{full-dashed}"
    ├── zp_module_dir                            DirectoryVar   (agent's terraform cwd)
    ├── zp_storage_dir                           DirectoryVar   (module's isolated data root)
    ├── <each user var from variables.tf>        Var        literal (or override)
    ├── <each terraform output>                  OutputVar      reads parent.outputs[<name>]
    └── terraform                                Terraform

Two DirectoryVars carry the agent's two filesystem promises about a module:

  - `zp_module_dir` — where the agent runs terraform (cloned source +
    .terraform/ + state). The user MAY edit this; on edit the directory
    is moved and terraform finds its state at the new location.

  - `zp_storage_dir` — the module's isolated data root. The module
    bind-mounts user data under this path. On edit, the agent moves the
    data tree (atomic rename when on the same FS, rsync to a sibling
    .incoming + atomic swap when crossing filesystems — supporting the
    "I added an HDD, move my photos there" workflow).

The Terraform depends on:
  - the namespace (provides path)
  - every Var-like child of the namespace (user vars + system vars)
  - any global system Vars living elsewhere (e.g. `settings/zp_arch`)

No literal magic strings are stored — `zp_module_id` and
`zp_network_name` derive from the namespace path at resolve time. This
means renaming the namespace just works.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from zeropoint_agent.dag import DAG
from zeropoint_agent.inode import ProducedNode
from zeropoint_agent.nodes.config.namespace import Namespace
from zeropoint_agent.nodes.config.namespaced import NamespacedVar
from zeropoint_agent.nodes.config.output import OutputVar
from zeropoint_agent.nodes.config.directory import DirectoryVar
from zeropoint_agent.nodes.config.var import Var
from zeropoint_agent.nodes.user.module import (
    Terraform, _parse_git_source, _git_clone_at_sha,
)

logger = logging.getLogger(__name__)


@dataclass
class TfVariable:
    name: str
    default: Optional[Any] = None
    has_default: bool = False
    description: Optional[str] = None
    type_str: Optional[str] = None


def _strip_hcl_str(s: Any) -> Any:
    if isinstance(s, str) and len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        return s[1:-1]
    if isinstance(s, list):
        return [_strip_hcl_str(x) for x in s]
    return s


def _hcl_variable_to_str(default: Any) -> str:
    default = _strip_hcl_str(default)
    if default is None:
        return "null"
    if isinstance(default, bool):
        return "true" if default else "false"
    if isinstance(default, (int, float)):
        return str(default)
    if isinstance(default, (list, dict)):
        import json
        return json.dumps(default)
    return str(default)


def parse_variables_tf(module_dir: Path) -> List[TfVariable]:
    """Parse `variable "name" { … }` blocks from every *.tf file in module_dir."""
    import hcl2  # type: ignore

    variables: Dict[str, TfVariable] = {}
    for tf_file in sorted(module_dir.glob("*.tf")):
        try:
            with tf_file.open("r") as fh:
                parsed = hcl2.load(fh)
        except Exception as e:
            logger.debug("failed to parse %s: %s", tf_file, e)
            continue
        for entry in parsed.get("variable", []) or []:
            for raw_name, body in entry.items():
                name = _strip_hcl_str(raw_name)
                if name in variables:
                    continue
                body = body or {}
                default = body.get("default")
                if (isinstance(default, list) and len(default) == 1
                        and not isinstance(default[0], (list, dict))):
                    default = default[0]
                desc = body.get("description")
                if isinstance(desc, list) and desc:
                    desc = desc[0]
                ttype = body.get("type")
                if isinstance(ttype, list) and ttype:
                    ttype = ttype[0]
                variables[name] = TfVariable(
                    name=name,
                    default=_strip_hcl_str(default),
                    has_default="default" in body,
                    description=_strip_hcl_str(desc) if isinstance(desc, str) else None,
                    type_str=ttype if isinstance(ttype, str) else None,
                )
    return list(variables.values())


def parse_outputs_tf(module_dir: Path) -> List[str]:
    """Return the names of every `output "name" { … }` block in module_dir."""
    import hcl2  # type: ignore

    names: List[str] = []
    seen: set = set()
    for tf_file in sorted(module_dir.glob("*.tf")):
        try:
            with tf_file.open("r") as fh:
                parsed = hcl2.load(fh)
        except Exception as e:
            logger.debug("failed to parse %s: %s", tf_file, e)
            continue
        for entry in parsed.get("output", []) or []:
            for raw_name in entry.keys():
                name = _strip_hcl_str(raw_name)
                if name in seen:
                    continue
                seen.add(name)
                names.append(name)
    return names


def _shallow_clone_for_inspection(url: str, sha: str) -> Path:
    """Clone into a temp dir so we can read variables.tf; caller deletes it."""
    tmp = Path(tempfile.mkdtemp(prefix="zp-modinspect-"))
    target = tmp / "module"
    try:
        _git_clone_at_sha(url, sha, target)
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return target


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

# Per-module path-derived system Vars. These are auto-created
# under the module's namespace; their values come from the inherited path.
_PER_MODULE_SYSTEM_VARS: Dict[str, str] = {
    "zp_module_id":    "leaf",
    "zp_network_name": "zeropoint-module-{full-dashed}",
}

# Global system Vars that live elsewhere (typically under `settings`).
# When a module's variables.tf declares one of these, we wire to the
# existing Var rather than auto-creating a per-module one.
#
# Note: zp_module_storage is NOT in this list anymore. Storage location
# is now a per-module concern (`zp_storage_dir`) — each module instance
# can live in a different place, including a different filesystem.
_GLOBAL_SYSTEM_VARS = ("zp_arch", "zp_gpu_vendor")

# Parent path under which all module namespaces live.
MODULES_NAMESPACE = "modules"


def _agent_state_root() -> Path:
    """Where the agent stores per-module terraform working dirs.

    Override via ZP_AGENT_STATE_ROOT; defaults to a `modules/`
    directory next to the SQLite state database, which is itself rooted
    by ZEROPOINT_ROOT_PATH (defaults to '.'). This keeps state and the
    graph ledger collocated, which matters for backup/restore.
    """
    explicit = os.environ.get("ZP_AGENT_STATE_ROOT")
    if explicit:
        return Path(explicit).expanduser().resolve()
    root = Path(os.environ.get("ZEROPOINT_ROOT_PATH", ".")).expanduser().resolve()
    return root / "data" / "modules"


def _default_storage_root() -> Path:
    """Where module data dirs default to live, before the user edits them.

    Override via ZP_MODULE_STORAGE; defaults to /var/lib/zeropoint.
    Each module's `zp_storage_dir` is initialized to
    `<storage_root>/<module_id>/` but is then independently editable.
    """
    return Path(
        os.environ.get("ZP_MODULE_STORAGE", "/var/lib/zeropoint")
    ).expanduser().resolve()


@dataclass
class AddModuleResult:
    module_id: str
    namespace_id: str
    terraform_id: str
    created_var_nodes: List[str] = field(default_factory=list)
    wired_existing_nodes: List[str] = field(default_factory=list)


@dataclass
class ModuleChildPlan:
    specs: List[ProducedNode]
    wired_existing_nodes: List[str]


def build_module_child_plan(
    module_id: str,
    source: str,
    *,
    overrides: Optional[Dict[str, str]] = None,
    parent_namespace: str = MODULES_NAMESPACE,
    global_by_name: Optional[Dict[str, str]] = None,
) -> ModuleChildPlan:
    """Inspect one pinned module and return its complete generated subtree."""
    overrides = overrides or {}
    global_by_name = global_by_name or {}
    namespace_id = f"{parent_namespace}/{module_id}"

    url, sha = _parse_git_source(source)
    inspection_dir = _shallow_clone_for_inspection(url, sha)
    try:
        tf_vars = parse_variables_tf(inspection_dir)
        tf_outputs = parse_outputs_tf(inspection_dir)
    finally:
        shutil.rmtree(inspection_dir.parent, ignore_errors=True)

    logger.info(
        "module %s declares %d variables, %d outputs",
        module_id,
        len(tf_vars),
        len(tf_outputs),
    )

    specs: List[ProducedNode] = []
    wired_existing: List[str] = []
    var_parent_ids: List[str] = []

    for varname, spec in _PER_MODULE_SYSTEM_VARS.items():
        node_id = f"{namespace_id}/{varname}"
        specs.append(ProducedNode.from_node(
            node_id,
            NamespacedVar(name=varname, spec=spec),
            parents=[namespace_id],
            perms="r--",
            tags=["input"],
        ))
        var_parent_ids.append(node_id)

    for varname, default_path in (
        ("zp_module_dir", str(_agent_state_root() / module_id)),
        ("zp_storage_dir", str(_default_storage_root() / module_id)),
    ):
        node_id = f"{namespace_id}/{varname}"
        specs.append(ProducedNode.from_node(
            node_id,
            DirectoryVar(name=varname, value=default_path),
            parents=[namespace_id],
            perms="rw-",
            tags=["input"],
        ))
        var_parent_ids.append(node_id)

    auto_injected = set(_PER_MODULE_SYSTEM_VARS) | {
        "zp_module_dir",
        "zp_storage_dir",
    }
    for var in tf_vars:
        if var.name in auto_injected:
            continue
        if (
            var.has_default
            and var.default is None
            and var.name not in overrides
        ):
            # An explicit Terraform default of null means "do not pass this
            # variable". Passing an empty string or the text "null" changes
            # its meaning and breaks expressions that test `var.x != null`.
            continue
        if var.name in _GLOBAL_SYSTEM_VARS:
            global_id = global_by_name.get(var.name)
            if not global_id:
                raise RuntimeError(
                    f"module declares system var {var.name!r} but no "
                    f"Var with that name exists in the graph"
                )
            if global_id not in var_parent_ids:
                var_parent_ids.append(global_id)
                wired_existing.append(global_id)
            continue

        node_id = f"{namespace_id}/{var.name}"
        value = (
            overrides[var.name]
            if var.name in overrides
            else _hcl_variable_to_str(var.default)
            if var.has_default
            else None
        )
        specs.append(ProducedNode.from_node(
            node_id,
            Var(name=var.name, value=value),
            parents=[namespace_id],
            tags=["input"],
        ))
        var_parent_ids.append(node_id)

    for sysvar in _GLOBAL_SYSTEM_VARS:
        global_id = global_by_name.get(sysvar)
        if global_id and global_id not in var_parent_ids:
            var_parent_ids.append(global_id)
            wired_existing.append(global_id)

    terraform_id = f"{namespace_id}/terraform"
    specs.append(ProducedNode.from_node(
        terraform_id,
        Terraform(source=source),
        parents=[namespace_id, *var_parent_ids],
        tags=["terraform"],
    ))

    existing_ids = {spec.id for spec in specs}
    for output_name in tf_outputs:
        output_id = f"{namespace_id}/{output_name}"
        if output_id in existing_ids:
            logger.warning(
                "module %s output %s collides with generated input %s; skipping",
                module_id,
                output_name,
                output_id,
            )
            continue
        specs.append(ProducedNode.from_node(
            output_id,
            OutputVar(name=output_name, key=output_name),
            parents=[namespace_id, terraform_id],
            perms="r--",
            tags=["output"],
        ))
        existing_ids.add(output_id)

    return ModuleChildPlan(
        specs=specs,
        wired_existing_nodes=sorted(set(wired_existing)),
    )


def add_module(
    dag: DAG,
    module_id: str,
    source: str,
    *,
    overrides: Optional[Dict[str, str]] = None,
    parent_namespace: str = MODULES_NAMESPACE,
) -> AddModuleResult:
    """Install a module into the DAG.

    Args:
        dag:               the DAG to mutate
        module_id:         module name (becomes the namespace name)
        source:            git URL with @<40-char-sha> ref
        overrides:         optional {varname: value} overrides for user vars
        parent_namespace:  id of the namespace this module lives under
                           (default: "modules"; for bundles, set to the
                           bundle's namespace id)

    Returns an AddModuleResult describing the nodes created and wired.
    """
    if parent_namespace not in dag.nodes:
        raise KeyError(
            f"parent namespace {parent_namespace!r} not found in graph")

    namespace_id = f"{parent_namespace}/{module_id}"
    dag.add(namespace_id, Namespace(name=module_id),
            parents=[parent_namespace], perms="rwd")

    global_by_name: Dict[str, str] = {}
    for nid, entry in dag.nodes.items():
        if not isinstance(entry.node, Var):
            continue
        if nid.startswith(f"{namespace_id}/"):
            continue
        global_by_name[entry.node.name] = nid

    plan = build_module_child_plan(
        module_id,
        source,
        overrides=overrides,
        parent_namespace=parent_namespace,
        global_by_name=global_by_name,
    )
    created: List[str] = [namespace_id]
    for spec in plan.specs:
        dag.add(
            spec.id,
            DAG._instantiate_produced(spec),
            parents=list(spec.parents),
            perms=spec.perms,
            tags=spec.tags,
        )
        created.append(spec.id)

    terraform_id = f"{namespace_id}/terraform"

    logger.info(
        "added module %s at %s (created=%d wired=%d)",
        module_id,
        namespace_id,
        len(created),
        len(plan.wired_existing_nodes),
    )

    return AddModuleResult(
        module_id=module_id,
        namespace_id=namespace_id,
        terraform_id=terraform_id,
        created_var_nodes=created,
        wired_existing_nodes=plan.wired_existing_nodes,
    )
