"""VarNode — a named value.

A VarNode produces a `VarResult(name, value)`. Four modes for how the
value is determined, in priority order:

  - **Literal**: `VarNode(name="model", value="llama3")`
    Value is the constructor literal.

  - **Path-derived**: `VarNode(name="zp_module_id", from_path="leaf")`
    Value is computed from the inherited namespace path.

  - **Output-derived**: `VarNode(name="redis_url", from_output="redis_url")`
    Value is pulled from a parent's outputs dict (typically a
    TerraformResult); useful for surfacing module outputs as VarNodes.

  - **Passthrough**: `VarNode(name="zp_arch_override")` (no value, parent
    is another VarNode)
    Value is the parent VarNode's value.

The mode is chosen by which constructor args are set, in priority:
  from_path > from_output > value > (otherwise passthrough)
"""

import json
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
    logger.warning("Unknown from_path spec %r; using literal", spec)
    return spec


def _inherited_path(input_val: Any) -> Optional[str]:
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
    if isinstance(input_val, VarResult):
        return input_val.value
    if isinstance(input_val, dict):
        for v in input_val.values():
            if isinstance(v, VarResult):
                return v.value
    return None


def _output_value(input_val: Any, output_name: str) -> Optional[str]:
    """Pull a named entry out of a parent's ``outputs`` dict.

    Looks for any parent whose output is a dataclass-like with an
    ``outputs`` attribute (e.g., TerraformResult). Returns the
    JSON-encoded value if the field is complex, or str(value) otherwise.
    """
    def _from_outputs(holder: Any) -> Optional[str]:
        outputs = getattr(holder, "outputs", None)
        if isinstance(outputs, dict) and output_name in outputs:
            val = outputs[output_name]
            if isinstance(val, (dict, list)):
                return json.dumps(val)
            if val is None:
                return ""
            return str(val)
        return None

    direct = _from_outputs(input_val)
    if direct is not None:
        return direct
    if isinstance(input_val, dict):
        for v in input_val.values():
            got = _from_outputs(v)
            if got is not None:
                return got
    return None


class VarNode(INode[Any, VarResult]):
    """A named value: literal, passthrough, path-derived, or output-derived.

    See module docstring for mode selection rules.
    """

    def __init__(self, name: str,
                 value: Optional[str] = None,
                 from_path: Optional[str] = None,
                 from_output: Optional[str] = None):
        self.name = name
        self.value = value
        self.from_path = from_path
        self.from_output = from_output

    def resolve(self, input: Any, mode: ResolveMode) -> NodeResult[VarResult]:
        # Path-derived
        if self.from_path is not None:
            path = _inherited_path(input)
            if path is None:
                return NodeResult.failed(
                    f"VarNode {self.name} has from_path={self.from_path!r} "
                    f"but no NamespaceNode parent provided a path")
            value = _derive_from_path(path, self.from_path)
            return NodeResult.success(VarResult(name=self.name, value=value))

        # Output-derived
        if self.from_output is not None:
            val = _output_value(input, self.from_output)
            if val is None:
                return NodeResult.failed(
                    f"VarNode {self.name} has from_output={self.from_output!r} "
                    f"but no parent provided an 'outputs' dict with that key")
            return NodeResult.success(VarResult(name=self.name, value=val))

        # Literal
        if self.value is not None:
            return NodeResult.success(VarResult(name=self.name, value=self.value))

        # Passthrough
        pv = _passthrough_value(input)
        if pv is not None:
            return NodeResult.success(VarResult(name=self.name, value=pv))

        return NodeResult.failed(
            f"VarNode {self.name} has no value, from_path, from_output, "
            f"or VarResult parent")

    def verify(self, mode: ResolveMode) -> NodeResult[VarResult]:
        # Literal: always verified.
        if (self.value is not None
                and self.from_path is None
                and self.from_output is None):
            return NodeResult.success(VarResult(name=self.name, value=self.value))
        # Anything else: must run resolve() to compute the value.
        return NodeResult.pending_reboot(VarResult(name=self.name, value=""))

    def remove(self, mode: ResolveMode) -> NodeResult[VarResult]:
        return NodeResult.success()

