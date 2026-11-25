"""Namespace — a structural / addressing node.

Namespaces contribute path segments. When a Namespace resolves,
its output carries the accumulated path (inherited + own name). Other
nodes that have a Namespace parent inherit its path verbatim.

The DAG executor handles the actual propagation. Namespace's only
job is to declare what segment it adds.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from zeropoint_agent.inode import INode, NodeResult, ResolveMode

logger = logging.getLogger(__name__)


@dataclass
class NamespaceResult:
    """Contract for a Namespace.

    Carries the accumulated path so children can inherit it. The
    `name` field is just the leaf segment; `path` is the full address
    used for display and addressing.
    """
    name: str
    path: str


@dataclass
class Namespace(INode[Any, NamespaceResult]):
    """A structural node that contributes a path segment.

    Children of a Namespace inherit the namespace's full path.
    Nesting Namespaces composes:

        modules (path="modules")
        └── redis (path="modules/redis")
            └── zp_module_id (inherits "modules/redis")

    Namespace does no other work. Its presence in the graph defines
    addressing; its resolution just emits the path it represents.
    """

    name: str

    def resolve(self, input: Any, mode: ResolveMode) -> NodeResult[NamespaceResult]:
        inherited = _inherited_path(input)
        full = f"{inherited}/{self.name}" if inherited else self.name
        return NodeResult.success(NamespaceResult(name=self.name, path=full))

    def verify(self, mode: ResolveMode) -> NodeResult[NamespaceResult]:
        # verify() can't compute the inherited path (no access to parents),
        # so always defer to resolve() which the executor calls with the
        # full parent-output input.
        return NodeResult.pending_reboot(NamespaceResult(name=self.name, path=self.name))

    def remove(self, mode: ResolveMode) -> NodeResult[NamespaceResult]:
        return NodeResult.success()


def _inherited_path(input: Any) -> str:
    """Pull the inherited path from whatever input shape the executor passed."""
    if input is None:
        return ""
    # Single parent: input is its NodeResult.output
    if isinstance(input, NamespaceResult):
        return input.path
    # Multi-parent: input is dict {parent_id: parent_output}
    if isinstance(input, dict):
        for v in input.values():
            if isinstance(v, NamespaceResult):
                return v.path
    return ""
