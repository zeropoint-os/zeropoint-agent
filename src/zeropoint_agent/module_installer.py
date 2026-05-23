"""Module installer — wires a terraform module into the DAG.

After this returns, calling `dag.resolve()` will run terraform.

Layout produced under `modules/<module_id>`:

    modules/<module_id>                          NamespaceNode
    ├── zp_module_id                             VarNode from_path="leaf"
    ├── zp_network_name                          VarNode from_path="zeropoint-module-{full-dashed}"
    ├── zp_module_path                           PathVarNode  (agent's terraform cwd)
    ├── zp_storage_path                          PathVarNode  (module's isolated data root)
    ├── <each user var from variables.tf>        VarNode literal (or override)
    └── terraform                                TerraformNode

Two PathVarNodes carry the agent's two filesystem promises about a module:

  - `zp_module_path` — where the agent runs terraform (cloned source +
    .terraform/ + state). The user MAY edit this; on edit the directory
    is moved and terraform finds its state at the new location.

  - `zp_storage_path` — the module's isolated data root. The module
    bind-mounts user data under this path. On edit, the agent moves the
    data tree (atomic rename when on the same FS, rsync to a sibling
    .incoming + atomic swap when crossing filesystems — supporting the
    "I added an HDD, move my photos there" workflow).

The TerraformNode depends on:
  - the namespace (provides path)
  - every VarNode child of the namespace (user vars + auto-derived system vars)
  - any global system VarNodes living elsewhere (e.g. `settings/zp_arch`)

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
from zeropoint_agent.nodes.config.namespace import NamespaceNode
from zeropoint_agent.nodes.config.path import PathVarNode
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

# Per-module path-derived system VarNodes. These are auto-created
# under the module's namespace; their values come from the inherited path.
_PER_MODULE_SYSTEM_VARS: Dict[str, str] = {
    "zp_module_id":    "leaf",
    "zp_network_name": "zeropoint-module-{full-dashed}",
}

# Global system VarNodes that live elsewhere (typically under `settings`).
# When a module's variables.tf declares one of these, we wire to the
# existing VarNode rather than auto-creating a per-module one.
#
# Note: zp_module_storage is NOT in this list anymore. Storage location
# is now a per-module concern (`zp_storage_path`) — each module instance
# can live in a different place, including a different filesystem.
_GLOBAL_SYSTEM_VARS = ("zp_arch", "zp_gpu_vendor")

# Parent path under which all module namespaces live.
MODULES_NAMESPACE = "modules"


def _agent_state_root() -> Path:
    """Where the agent stores per-module terraform working dirs.

    Override via ZP_AGENT_STATE_ROOT; defaults to a `modules/`
    directory next to the graph.db data dir, which is itself rooted
    by ZEROPOINT_ROOT_PATH (defaults to '.'). This keeps state and
    graph collocated, which matters for backup/restore.
    """
    explicit = os.environ.get("ZP_AGENT_STATE_ROOT")
    if explicit:
        return Path(explicit).expanduser().resolve()
    root = Path(os.environ.get("ZEROPOINT_ROOT_PATH", ".")).expanduser().resolve()
    return root / "data" / "modules"


def _default_storage_root() -> Path:
    """Where module data dirs default to live, before the user edits them.

    Override via ZP_MODULE_STORAGE; defaults to /var/lib/zeropoint.
    Each module's `zp_storage_path` is initialized to
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

    # Inspect the module (variables.tf + outputs) by shallow-cloning to a
    # temp location and reading its .tf files. We always go through git;
    # local paths aren't supported (see TerraformNode._parse_git_source).
    url, sha = _parse_git_source(source)
    inspection_dir = _shallow_clone_for_inspection(url, sha)
    try:
        tf_vars = parse_variables_tf(inspection_dir)
        tf_outputs = parse_outputs_tf(inspection_dir)
    finally:
        shutil.rmtree(inspection_dir.parent, ignore_errors=True)

    logger.info("module %s declares %d variables, %d outputs",
                module_id, len(tf_vars), len(tf_outputs))

    # Namespace for this module: modules/<module_id>. The namespace itself
    # is fully manipulable by the user (rwd) — users can rename, edit, or
    # remove the whole module.
    namespace_id = f"{parent_namespace}/{module_id}"
    dag.add(namespace_id, NamespaceNode(name=module_id),
            parents=[parent_namespace], perms="rwd")

    created: List[str] = [namespace_id]
    wired_existing: List[str] = []
    var_parent_ids: List[str] = []  # all VarNodes that feed the TerraformNode

    # Auto-create per-module path-derived system vars under the namespace.
    # These are system-managed: their value is derived from the namespace
    # path, so editing makes no sense (r--). They live for the lifetime of
    # the namespace and shouldn't be removed independently.
    for varname, from_path_spec in _PER_MODULE_SYSTEM_VARS.items():
        node_id = f"{namespace_id}/{varname}"
        dag.add(
            node_id,
            VarNode(name=varname, from_path=from_path_spec),
            parents=[namespace_id],
            perms="r--",
        )
        created.append(node_id)
        var_parent_ids.append(node_id)

    # The agent's two filesystem promises: the module's working dir
    # (zp_module_path, where terraform runs) and its data root
    # (zp_storage_path, where the module bind-mounts user data). Both
    # are PathVarNodes — editable by the user; the agent moves the
    # directory before persisting the new path.
    module_path_default = str(_agent_state_root() / module_id)
    storage_path_default = str(_default_storage_root() / module_id)
    for varname, default_path in (
        ("zp_module_path",  module_path_default),
        ("zp_storage_path", storage_path_default),
    ):
        node_id = f"{namespace_id}/{varname}"
        dag.add(
            node_id,
            PathVarNode(name=varname, value=default_path),
            parents=[namespace_id],
            perms="rw-",
        )
        created.append(node_id)
        var_parent_ids.append(node_id)

    # Find globally-available system VarNodes (zp_arch, zp_gpu_vendor)
    # by name and wire to them as TerraformNode parents. We restrict the
    # search to nodes outside this module's namespace — per-module vars
    # under modules/<id>/ are local and shouldn't be wired as globals.
    global_by_name: Dict[str, str] = {}
    for nid, entry in dag.nodes.items():
        if not isinstance(entry.node, VarNode):
            continue
        if nid.startswith(f"{namespace_id}/"):
            continue
        global_by_name[entry.node.name] = nid

    # Per-module system vars that the installer already injected above
    # (path-derived ones + the two PathVarNodes). Any tf variable with
    # one of these names is automatically wired and should be skipped
    # in the loop below.
    auto_injected = set(_PER_MODULE_SYSTEM_VARS) | {"zp_module_path", "zp_storage_path"}

    # Walk each declared variable from variables.tf
    for var in tf_vars:
        if var.name in auto_injected:
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
        # module's default or an override. User vars are freely editable
        # (defer to namespace context).
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
    # Type default r-d (w vetoed by class) is sufficient; no instance override.
    terraform_id = f"{namespace_id}/terraform"
    dag.add(
        terraform_id,
        TerraformNode(source=source),
        parents=[namespace_id, *var_parent_ids],
    )
    created.append(terraform_id)

    # Output VarNodes — one per declared terraform output. Each is r--
    # (system-managed; its value comes from terraform's output, not the
    # user) and lives under the module's namespace, with the TerraformNode
    # as a data-flow parent so the value flows through at resolve time.
    for out_name in tf_outputs:
        out_id = f"{namespace_id}/{out_name}"
        if out_id in dag.nodes:
            # Collision with a user var or a system var sharing the name.
            # Skip; the user can rename one or the other.
            logger.warning(
                "module %s output %s collides with existing node %s; "
                "skipping the output VarNode",
                module_id, out_name, out_id)
            continue
        dag.add(
            out_id,
            VarNode(name=out_name, from_output=out_name),
            parents=[namespace_id, terraform_id],
            perms="r--",
        )
        created.append(out_id)

    logger.info(
        "added module %s at %s (created=%d wired=%d outputs=%d)",
        module_id, namespace_id, len(created), len(wired_existing),
        len(tf_outputs),
    )

    return AddModuleResult(
        module_id=module_id,
        namespace_id=namespace_id,
        terraform_id=terraform_id,
        created_var_nodes=created,
        wired_existing_nodes=wired_existing,
    )

