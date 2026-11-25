"""Helpers for reading a hardware node's last persisted output."""

from __future__ import annotations

from typing import Any, Optional


def field(output: Any, name: str) -> Optional[Any]:
    if output is None:
        return None
    if isinstance(output, dict):
        return output.get(name)
    return getattr(output, name, None)


def own_output(node: Any) -> Optional[Any]:
    dag = getattr(node, "dag", None)
    node_id = getattr(node, "id", "")
    if dag is None or not node_id:
        return None
    entry = dag.nodes.get(node_id)
    return entry.output if entry is not None else None
