"""VarNode — a named value.

A VarNode produces a `VarResult(name, value)`. Three modes for how the
value is determined:

  - **Literal**: `VarNode(name="model", value="llama3")`
    Value is the constructor literal.

  - **Passthrough**: `VarNode(name="zp_arch_override")` (no value, parent
    is another VarNode)
    Value is the parent VarNode's value.

  - **Path-derived**: `VarNode(name="zp_module_id", from_path="leaf")`
    Value is computed from the inherited namespace path. The VarNode's
    NamespaceNode parent provides the path; the `from_path` spec says
    how to derive a string from it.

The mode is chosen by which constructor args are set, in priority:
  from_path > value > (otherwise passthrough)

`from_path` accepts:
  - `"leaf"`         → last path segment (e.g. "redis" from "modules/redis")
  - `"full-dashed"`  → full path with "/" replaced by "-"
                       (e.g. "modules-redis")
  - `"full"`         → raw path (e.g. "modules/redis")
  - `"<template>"`   → arbitrary string with `{leaf}`, `{full-dashed}`,
                       `{full}` placeholders interpolated. Any literal
                       containing `{` is treated as a template.
                       e.g. "zeropoint-module-{full-dashed}" →
                            "zeropoint-module-modules-redis"
"""

import logging
from dataclasses import dataclass
from typing import Any, Optional

from zeropoint_agent.inode import INode, ResolveMode, NodeResult
from zeropoint_agent.nodes.config.namespace import NamespaceResult

logger = logging.getLogger(__name__)


@dataclass
class VarResult:
    """Contract for a variable node."""
    name: str
    value: str


def _derive_from_path(path: str, spec: str) -> str:
    """Apply a `from_path` spec to the inherited path string."""
    if not path:
        # No inherited path; produce empty for path-derived vars.
        # Literal/passthrough modes don't reach this function.
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
    # Template mode — any spec containing `{` is treated as a format string
    if "{" in spec:
        return (spec
                .replace("{leaf}", leaf)
                .replace("{full-dashed}", full_dashed)
                .replace("{full}", full))
    # Unknown spec — return as-is (caller probably typoed)
    logger.warning("Unknown from_path spec %r; using literal", spec)
    return spec


def _inherited_path(input_val: Any) -> Optional[str]:
    """Pull the inherited path from whatever input shape the executor passed.

    Returns the path string or None if no NamespaceResult parent was found.
    """
    if input_val is None:
        return None
    if isinstance(input_val, NamespaceResult):
        return input_val.path
    if isinstance(input_val, dict):
        for v in input_val.values():
            if isinstance(v, NamespaceResult):
                return v.path
    return None


def _passthrough_value(input_val: Any) -> Optional[str]:
    """Pull a value from a VarResult parent (for passthrough mode)."""
    if isinstance(input_val, VarResult):
        return input_val.value
    if isinstance(input_val, dict):
        for v in input_val.values():
            if isinstance(v, VarResult):
                return v.value
    return None


class VarNode(INode[Any, VarResult]):
    """A named value: literal, passthrough, or path-derived.

    See module docstring for mode selection rules.
    """

    def __init__(self, name: str,
                 value: Optional[str] = None,
                 from_path: Optional[str] = None):
        self.name = name
        self.value = value
        self.from_path = from_path

    def resolve(self, input: Any, mode: ResolveMode) -> NodeResult[VarResult]:
        # Path-derived takes precedence
        if self.from_path is not None:
            path = _inherited_path(input)
            if path is None:
                return NodeResult.failed(
                    f"VarNode {self.name} has from_path={self.from_path!r} "
                    f"but no NamespaceNode parent provided a path")
            value = _derive_from_path(path, self.from_path)
            return NodeResult.success(VarResult(name=self.name, value=value))

        # Literal
        if self.value is not None:
            return NodeResult.success(VarResult(name=self.name, value=self.value))

        # Passthrough
        pv = _passthrough_value(input)
        if pv is not None:
            return NodeResult.success(VarResult(name=self.name, value=pv))

        return NodeResult.failed(
            f"VarNode {self.name} has no value, from_path, or VarResult parent")

    def verify(self, mode: ResolveMode) -> NodeResult[VarResult]:
        # Literal: always verified
        if self.value is not None and self.from_path is None:
            return NodeResult.success(VarResult(name=self.name, value=self.value))
        # Path-derived or passthrough: needs resolve to compute the value
        return NodeResult.pending_reboot(VarResult(name=self.name, value=""))

    def remove(self, mode: ResolveMode) -> NodeResult[VarResult]:
        return NodeResult.success()
