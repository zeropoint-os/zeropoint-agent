"""NamespacedVar — a Var whose value derives from its namespace path.

Installer-created. Each module gets two:
  - `zp_module_id`     spec="leaf"
  - `zp_network_name`  spec="zeropoint-module-{full-dashed}"

The user never authors these directly. Renaming a Namespace in
the user's tree causes every NamespacedVar under it to re-derive on
the next resolve — no literal strings to update.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from zeropoint_agent.inode import ResolveMode, NodeResult
from zeropoint_agent.nodes.config.namespace import NamespaceResult
from zeropoint_agent.nodes.config.var import Var, VarResult

logger = logging.getLogger(__name__)


def _derive(path: str, spec: str) -> str:
    """Apply a template `spec` to a namespace `path`.

    Specs:
      "leaf"          → just the last segment ("redis" for "modules/redis")
      "full"          → the whole path
      "full-dashed"   → the whole path with `/` replaced by `-`
      anything with `{leaf}`, `{full}`, or `{full-dashed}` is treated
      as a template; everything else is returned verbatim.
    """
    if not path:
        return ""
    leaf = path.rsplit("/", 1)[-1]
    full = path
    full_dashed = path.replace("/", "-")
    if spec == "leaf":
        return leaf
    if spec == "full":
        return full
    if spec == "full-dashed":
        return full_dashed
    if "{" in spec:
        return (spec
                .replace("{leaf}", leaf)
                .replace("{full-dashed}", full_dashed)
                .replace("{full}", full))
    logger.warning("NamespacedVar %r: unknown spec %r; emitting verbatim",
                   __name__, spec)
    return spec


def _inherited_path(input_val: Any) -> Optional[str]:
    """Pull `path` off the first NamespaceResult in `input_val`."""
    if input_val is None:
        return None
    if isinstance(input_val, NamespaceResult):
        return input_val.path
    if isinstance(input_val, dict):
        for v in input_val.values():
            if isinstance(v, NamespaceResult):
                return v.path
    return None


@dataclass
class NamespacedVar(Var[str]):
    """Var whose value is computed from its inherited namespace path."""

    spec: str = "leaf"

    def resolve(self, input: Any, mode: ResolveMode) -> NodeResult[VarResult[str]]:
        path = _inherited_path(input)
        if path is None:
            return NodeResult.failed(
                f"NamespacedVar {self.name} (spec={self.spec!r}) "
                f"needs a Namespace parent providing a path")
        return NodeResult.success(VarResult(name=self.name, value=_derive(path, self.spec)))
