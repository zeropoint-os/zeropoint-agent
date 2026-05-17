"""Module installer — wires a terraform module into the DAG.

After this returns, calling `dag.resolve()` will run terraform.

Layout produced under `modules/<module_id>`:

    modules/<module_id>                          NamespaceNode
    ├── zp_module_id                             VarNode from_path="leaf"
    ├── zp_network_name                          VarNode from_path="zeropoint-module-{full-dashed}"
    ├── <each user var from variables.tf>        VarNode literal (or override)
    └── terraform                                TerraformNode

The TerraformNode depends on:
  - the namespace (provides path)
  - every VarNode child of the namespace (user vars + auto-derived system vars)
  - the system VarNodes living elsewhere (e.g. `global-settings/zp_module_storage`)

No literal magic strings are stored — system vars derive from path at
resolve time. This means renaming the namespace just works: zp_module_id
and zp_network_name automatically reflect the new path on the next resolve.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from zeropoint_agent.dag import DAG
from zeropoint_agent.nodes.config.namespace import NamespaceNode
from zeropoint_agent.nodes.config.var import VarNode
from zeropoint_agent.nodes.user.module import (
    TerraformNode, _parse_git_source, _git_clone_at_sha,
)

logger = logging.getLogger(__name__)


@dataclass
class TfVariable:
    name: str
    default: Optional[Any] = None
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
        return ""
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
                    description=_strip_hcl_str(desc) if isinstance(desc, str) else None,
                    type_str=ttype if isinstance(ttype, str) else None,
                )
    return list(variables.values())


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

# Per-module path-derived system VarNodes. These are auto-created
# under the module's namespace; their values come from the inherited path.
_PER_MODULE_SYSTEM_VARS: Dict[str, str] = {
    "zp_module_id":    "leaf",
    "zp_network_name": "zeropoint-module-{full-dashed}",
}

# System VarNodes that live globally (typically under `global-settings`).
# When a module's variables.tf declares one of these, we wire to the
# existing VarNode rather than auto-creating a per-module one.
_GLOBAL_SYSTEM_VARS = ("zp_module_storage", "zp_arch", "zp_gpu_vendor")

# Parent path under which all module namespaces live.
MODULES_NAMESPACE = "modules"


@dataclass
class AddModuleResult:
    module_id: str
    namespace_id: str
    terraform_id: str
    created_var_nodes: List[str] = field(default_factory=list)
    wired_existing_nodes: List[str] = field(default_factory=list)


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
    overrides = overrides or {}

    if parent_namespace not in dag.nodes:
        raise KeyError(
            f"parent namespace {parent_namespace!r} not found in graph")

    url, sha = _parse_git_source(source)
    inspection_dir = _shallow_clone_for_inspection(url, sha)
    try:
        tf_vars = parse_variables_tf(inspection_dir)
    finally:
        shutil.rmtree(inspection_dir.parent, ignore_errors=True)

    logger.info("module %s declares %d variables", module_id, len(tf_vars))

    # Namespace for this module: modules/<module_id>
    namespace_id = f"{parent_namespace}/{module_id}"
    dag.add(namespace_id, NamespaceNode(name=module_id), parents=[parent_namespace])

    created: List[str] = [namespace_id]
    wired_existing: List[str] = []
    var_parent_ids: List[str] = []  # all VarNodes that feed the TerraformNode

    # Auto-create per-module path-derived system vars under the namespace.
    for varname, from_path_spec in _PER_MODULE_SYSTEM_VARS.items():
        node_id = f"{namespace_id}/{varname}"
        dag.add(
            node_id,
            VarNode(name=varname, from_path=from_path_spec),
            parents=[namespace_id],
        )
        created.append(node_id)
        var_parent_ids.append(node_id)

    # Find globally-available system VarNodes (zp_module_storage, etc.)
    # by name and wire to them as TerraformNode parents.
    global_by_name: Dict[str, str] = {}
    for nid, entry in dag.nodes.items():
        if isinstance(entry.node, VarNode):
            global_by_name[entry.node.name] = nid

    # Walk each declared variable from variables.tf
    for var in tf_vars:
        if var.name in _PER_MODULE_SYSTEM_VARS:
            # Already auto-created above; just skip (already a parent).
            continue

        if var.name in _GLOBAL_SYSTEM_VARS:
            # Wire to the existing global system VarNode.
            gid = global_by_name.get(var.name)
            if not gid:
                raise RuntimeError(
                    f"module declares system var {var.name!r} but no "
                    f"VarNode with that name exists in the graph")
            if gid not in var_parent_ids:
                var_parent_ids.append(gid)
                wired_existing.append(gid)
            continue

        # User-defined var: create a VarNode under the namespace with the
        # module's default or an override.
        var_node_id = f"{namespace_id}/{var.name}"
        value = (overrides[var.name] if var.name in overrides
                 else _hcl_variable_to_str(var.default))
        dag.add(
            var_node_id,
            VarNode(name=var.name, value=value),
            parents=[namespace_id],
        )
        created.append(var_node_id)
        var_parent_ids.append(var_node_id)

    # Always include the globally-available system vars even if not
    # declared in variables.tf — terraform ignores unknown vars.
    for sysvar in _GLOBAL_SYSTEM_VARS:
        gid = global_by_name.get(sysvar)
        if gid and gid not in var_parent_ids:
            var_parent_ids.append(gid)
            wired_existing.append(gid)

    # The TerraformNode: lives under the namespace, depends on it (for
    # path/ordering) + all var parents.
    terraform_id = f"{namespace_id}/terraform"
    dag.add(
        terraform_id,
        TerraformNode(source=source),
        parents=[namespace_id, *var_parent_ids],
    )
    created.append(terraform_id)

    logger.info(
        "added module %s at %s (created=%d wired=%d)",
        module_id, namespace_id, len(created), len(wired_existing),
    )

    return AddModuleResult(
        module_id=module_id,
        namespace_id=namespace_id,
        terraform_id=terraform_id,
        created_var_nodes=created,
        wired_existing_nodes=wired_existing,
    )

