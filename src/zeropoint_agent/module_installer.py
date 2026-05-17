"""Module installer — wires a terraform module into the DAG.

Given a git source (URL@SHA) and a module_id:
1. Shallow-clones the module to a temp dir to inspect variables.tf
2. For each declared variable:
   - If a VarNode with that name already exists in the DAG, wire to it
   - Otherwise create a per-module VarNode `<module_id>.<varname>` carrying
     the module's default value
3. Adds the ModuleNode with all VarNodes (plus the system
   `module-storage` VarNode) as parents.

After this returns, calling `dag.resolve()` will run terraform.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from zeropoint_agent.dag import DAG
from zeropoint_agent.nodes.config.var import VarNode
from zeropoint_agent.nodes.user.module import (
    ModuleNode, _parse_git_source, _git_clone_at_sha,
)

logger = logging.getLogger(__name__)


@dataclass
class TfVariable:
    name: str
    default: Optional[Any] = None
    description: Optional[str] = None
    type_str: Optional[str] = None


def _strip_hcl_str(s: Any) -> Any:
    """hcl2 sometimes wraps identifiers/strings in literal quotes."""
    if isinstance(s, str) and len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        return s[1:-1]
    if isinstance(s, list):
        return [_strip_hcl_str(x) for x in s]
    return s


def _hcl_variable_to_str(default: Any) -> str:
    """Render an HCL default value as a terraform-cli-friendly string."""
    default = _strip_hcl_str(default)
    if default is None:
        return ""
    if isinstance(default, bool):
        return "true" if default else "false"
    if isinstance(default, (int, float)):
        return str(default)
    if isinstance(default, (list, dict)):
        # Terraform accepts JSON for complex types via -var
        import json
        return json.dumps(default)
    return str(default)


def parse_variables_tf(module_dir: Path) -> List[TfVariable]:
    """Parse all `variable "name" { … }` blocks from any *.tf file in module_dir."""
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
            # hcl2 returns each `variable "name" {}` block as a dict {name: {…}}.
            for raw_name, body in entry.items():
                name = _strip_hcl_str(raw_name)
                if name in variables:
                    continue
                body = body or {}
                default = body.get("default")
                if isinstance(default, list) and len(default) == 1 and not isinstance(default[0], (list, dict)):
                    # hcl2 wraps single-value attrs in a 1-element list
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
    """Clone into a temp dir so we can read variables.tf, then return the path.

    Caller is responsible for deleting the directory.
    """
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

# Per-module system vars whose values are derived from the module itself.
# These are auto-created as `{module_id}.<varname>` VarNodes by module-add;
# the bootstrap-level zp_* (zp_arch, zp_gpu_vendor, zp_module_storage) are
# resolved via the same name-lookup path as user vars.
def _per_module_system_vars(module_id: str) -> Dict[str, str]:
    return {
        "zp_module_id": module_id,
        "zp_network_name": f"zeropoint-module-{module_id}",
    }


@dataclass
class AddModuleResult:
    module_id: str
    module_node_id: str
    created_var_nodes: List[str]
    wired_existing_nodes: List[str]


def add_module(
    dag: DAG,
    module_id: str,
    source: str,
    *,
    overrides: Optional[Dict[str, str]] = None,
) -> AddModuleResult:
    """Install a module into the DAG.

    Args:
        dag:        the DAG to mutate
        module_id:  unique id (used for node ids and module dir)
        source:     git URL with @<40-char-sha> ref
        overrides:  optional per-var default overrides for newly created VarNodes
                    ({varname: value}). Existing VarNodes are never modified.
    """
    overrides = overrides or {}

    url, sha = _parse_git_source(source)
    inspection_dir = _shallow_clone_for_inspection(url, sha)
    try:
        tf_vars = parse_variables_tf(inspection_dir)
    finally:
        shutil.rmtree(inspection_dir.parent, ignore_errors=True)

    logger.info("module %s declares %d variables", module_id, len(tf_vars))

    created: List[str] = []
    wired_existing: List[str] = []
    parent_ids: List[str] = []

    # Build name -> node_id lookup from current VarNodes in the DAG.
    existing_by_name: Dict[str, str] = {}
    for nid, entry in dag.nodes.items():
        node = entry.node
        if isinstance(node, VarNode):
            existing_by_name[node.name] = nid

    # Auto-create per-module system VarNodes (zp_module_id, zp_network_name).
    # These derive their values from the module itself; they live alongside
    # the bootstrap-level zp_* system vars (zp_arch, zp_gpu_vendor,
    # zp_module_storage), which already exist in the graph.
    per_module = _per_module_system_vars(module_id)
    for varname, value in per_module.items():
        if varname in existing_by_name:
            # Already wired (e.g., re-running add on same module)
            continue
        node_id = f"{module_id}.{varname}"
        dag.add(node_id, VarNode(name=varname, value=value))
        existing_by_name[varname] = node_id
        created.append(node_id)

    for var in tf_vars:
        # Wire to an existing VarNode by name (system or user-defined).
        if var.name in existing_by_name:
            parent_id = existing_by_name[var.name]
            parent_ids.append(parent_id)
            # Only count as "wired_existing" if we didn't just create it.
            if parent_id not in created:
                wired_existing.append(parent_id)
            continue

        # Otherwise create a new VarNode {module_id}.{varname}
        var_node_id = f"{module_id}.{var.name}"
        if overrides and var.name in overrides:
            value = overrides[var.name]
        else:
            value = _hcl_variable_to_str(var.default)
        dag.add(var_node_id, VarNode(name=var.name, value=value))
        parent_ids.append(var_node_id)
        existing_by_name[var.name] = var_node_id
        created.append(var_node_id)

    # Always include the per-module zp_module_id / zp_network_name as
    # parents even if the module's variables.tf doesn't declare them.
    for varname in per_module:
        nid = existing_by_name.get(varname)
        if nid and nid not in parent_ids:
            parent_ids.append(nid)

    module_node_id = module_id
    dag.add(
        module_node_id,
        ModuleNode(module_id=module_id, source=source),
        parents=parent_ids,
    )

    logger.info(
        "added module %s (parents=%d created=%d wired=%d)",
        module_id, len(parent_ids), len(created), len(wired_existing),
    )

    return AddModuleResult(
        module_id=module_id,
        module_node_id=module_node_id,
        created_var_nodes=created,
        wired_existing_nodes=wired_existing,
    )
