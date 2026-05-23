"""OutputVar — a Var whose value comes from a parent's `outputs` dict.

Installer-created. For every terraform output, the module installer
creates one `OutputVar` parented to the Terraform, so each output
becomes a first-class `Var[T]` in the graph — pickable, linkable,
inspectable.

Not specific to terraform; any node whose result has an `outputs`
dict (or whose rehydrated dict form has an "outputs" key) is a
legitimate parent for an OutputVar.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Optional, TypeVar

from zeropoint_agent.inode import ResolveMode, NodeResult
from zeropoint_agent.nodes.config.var import Var, VarResult

logger = logging.getLogger(__name__)


T = TypeVar("T")


def _read_output(input_val: Any, key: str) -> Optional[Any]:
    """Pull a single named entry out of a parent's `outputs` dict.

    Handles both runtime dataclass form (attr access) and rehydrated
    dict form. Complex values (dict/list) are JSON-encoded so they
    can flow as strings through tfvars; primitive values are returned
    as-is (caller stringifies when needed).
    """
    def _from_outputs(holder: Any) -> Optional[Any]:
        # Dataclass form (live runtime).
        outputs = getattr(holder, "outputs", None)
        # Dict form (rehydrated from store via asdict()).
        if outputs is None and isinstance(holder, dict):
            outputs = holder.get("outputs")
        if isinstance(outputs, dict) and key in outputs:
            val = outputs[key]
            if isinstance(val, (dict, list)):
                return json.dumps(val)
            if val is None:
                return ""
            return val
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


@dataclass
class OutputVar(Var[T]):
    """Var whose value is read from a parent's outputs dict."""

    key: str = ""

    def resolve(self, input: Any, mode: ResolveMode) -> NodeResult[VarResult[T]]:
        val = _read_output(input, self.key)
        if val is None:
            return NodeResult.failed(
                f"OutputVar {self.name} (key={self.key!r}) needs a parent "
                f"providing an 'outputs' dict containing that key")
        # Strings are still the wire form into terraform; primitive cast
        # happens here so the VarResult is honest.
        if not isinstance(val, str):
            val = str(val)
        return NodeResult.success(VarResult(name=self.name, value=val))  # type: ignore[arg-type]
