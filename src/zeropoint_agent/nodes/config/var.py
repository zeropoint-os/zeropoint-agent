"""Var — a named, typed value.

A Var produces a `VarResult[T](name, value)`. There are three
concrete shapes — each genuinely different, hence its own class:

  - **Var** (this file) — holds a literal `T`, OR forwards from a
    Var parent ("passthrough"). The user-editable case.

  - **NamespacedVar** (`namespaced.py`) — derives its value from its
    inherited namespace path via a template spec like "leaf",
    "full", or "zeropoint-module-{full-dashed}". Installer-created;
    not user-authored.

  - **OutputVar** (`output.py`) — reads its value from a parent's
    `outputs` dict. Installer-created (e.g. one per terraform
    output); not user-authored.

VarResult and Var are generic over `T` so the type system knows
what a Var produces. Pickers and consumers use the parameter to
filter compatible link targets.

The base Var runtime is two-mode: literal first, passthrough as
fallback. The mode is implicit in graph structure — a Var with a
Var parent and no literal forwards; one with a literal emits it.
No flags, no special cases beyond the single fallback.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Generic, Optional, TypeVar

from zeropoint_agent.inode import INode, ResolveMode, NodeResult

logger = logging.getLogger(__name__)


T = TypeVar("T")


@dataclass
class VarResult(Generic[T]):
    """Contract for a value-producing node. `value` is whatever T is."""
    name: str
    value: T


def _passthrough_value(input_val: Any) -> Optional[Any]:
    """Pull a value off the first VarResult in `input_val`."""
    if isinstance(input_val, VarResult):
        return input_val.value
    if isinstance(input_val, dict):
        for v in input_val.values():
            if isinstance(v, VarResult):
                return v.value
    return None


class Var(INode[Any, VarResult[T]], Generic[T]):
    """A named value of type T. Holds a literal OR forwards from a Var parent.

    The mode is implicit:
      - `value` is set    → literal mode; emit it.
      - `value` is None   → passthrough mode; emit the parent Var's value.

    Linking (picker UX) sets value to None and adds a parent edge; the
    runtime fallback then handles the value-walking.
    """

    def __init__(self, name: str, value: Optional[T] = None):
        self.name = name
        self.value = value

    def resolve(self, input: Any, mode: ResolveMode) -> NodeResult[VarResult[T]]:
        # Literal mode.
        if self.value is not None:
            return NodeResult.success(VarResult(name=self.name, value=self.value))

        # Passthrough mode (linked to another Var).
        pv = _passthrough_value(input)
        if pv is not None:
            return NodeResult.success(VarResult(name=self.name, value=pv))

        return NodeResult.failed(
            f"Var {self.name} has no value and no Var parent to forward from")

    def verify(self, mode: ResolveMode) -> NodeResult[VarResult[T]]:
        # Literal is always verified; anything else needs resolve.
        if self.value is not None:
            return NodeResult.success(VarResult(name=self.name, value=self.value))
        return NodeResult.pending_reboot(VarResult(name=self.name, value=None))  # type: ignore[arg-type]

    def remove(self, mode: ResolveMode) -> NodeResult[VarResult[T]]:
        return NodeResult.success()

