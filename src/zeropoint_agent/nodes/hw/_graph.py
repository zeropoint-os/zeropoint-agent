"""Read a node's own last-persisted output.

`verify()` and `remove()` receive no `input` (only `resolve()` does), yet
a node sometimes needs the device it created last time — to re-probe it or
tear it down. Instead of walking sideways to a parent, a node reads its
*own* last output, which the runtime persists on the DAG entry after every
resolve (`dag.py:451`). Self-referential; no graph traversal. Tolerates
both live dataclass outputs and dicts rehydrated from the store.
"""

from __future__ import annotations

from typing import Any, Optional


def field(output: Any, name: str) -> Optional[Any]:
    """Read a field from an output that may be a dataclass or a dict."""
    if output is None:
        return None
    if isinstance(output, dict):
        return output.get(name)
    return getattr(output, name, None)


def own_output(node: Any) -> Optional[Any]:
    """This node's last-persisted output, or None if not yet resolved."""
    dag = getattr(node, "dag", None)
    nid = getattr(node, "id", "")
    if dag is None or not nid:
        return None
    entry = dag.nodes.get(nid)
    return entry.output if entry is not None else None

