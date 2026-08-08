"""Graph query engine — glob-style addressing for the DAG.

Supports:
    disk-sda         → single node by exact ID
    disk-*           → wildcard match on node IDs
    disk-sda/*       → direct children
    disk-sda/**      → entire subtree (recursive)
    **/ollama        → find anywhere in the graph
    **               → all nodes
    
Filters via query params:
    ?status=error
    ?type=Terraform
"""

import fnmatch
import logging
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)


def query_dag(dag, pattern: str, status: Optional[str] = None,
              node_type: Optional[str] = None) -> List[str]:
    """
    Query the DAG with a glob-style path pattern.

    Args:
        dag: DAG instance
        pattern: Glob path (e.g. "disk-sda/**", "**/ollama", "disk-*")
        status: Optional status filter
        node_type: Optional type name filter

    Returns:
        List of matching node IDs
    """
    # Fast path: pattern is an exact node id (common case now that ids
    # are namespace paths like "modules/redis/terraform").
    if pattern in dag.nodes:
        matched = [pattern]
    else:
        segments = [s for s in pattern.strip("/").split("/") if s]
        if not segments:
            return []
        matched = _resolve_segments(dag, segments)

    # Apply filters
    if status:
        matched = [nid for nid in matched
                   if dag.get(nid).status.value == status]
    if node_type:
        matched = [nid for nid in matched
                   if type(dag.get(nid).node).__name__ == node_type]

    return matched


def _resolve_segments(dag, segments: List[str]) -> List[str]:
    """Resolve a list of path segments against the DAG."""
    if len(segments) == 1:
        seg = segments[0]
        if seg == "**":
            return list(dag.nodes.keys())
        # Simple glob match on all node IDs
        return [nid for nid in dag.nodes if fnmatch.fnmatch(nid, seg)]

    first = segments[0]
    rest = segments[1:]

    if first == "**":
        # ** at start: find the next segment anywhere in the graph
        return _find_anywhere(dag, rest)

    # Match the first segment
    roots = [nid for nid in dag.nodes if fnmatch.fnmatch(nid, first)]

    if not rest:
        return roots

    results = []
    for root_id in roots:
        results.extend(_walk_from(dag, root_id, rest))

    return list(dict.fromkeys(results))  # dedupe preserving order


def _walk_from(dag, node_id: str, segments: List[str]) -> List[str]:
    """Walk from a node following the remaining path segments."""
    if not segments:
        return [node_id]

    seg = segments[0]
    rest = segments[1:]

    if seg == "*":
        # Direct children only
        children = _get_children(dag, node_id)
        if not rest:
            return children
        results = []
        for child_id in children:
            results.extend(_walk_from(dag, child_id, rest))
        return results

    elif seg == "**":
        # Recursive: this node + all descendants
        descendants = _get_all_descendants(dag, node_id)
        all_nodes = [node_id] + descendants

        if not rest:
            return all_nodes

        # ** followed by more segments: find matches in subtree
        results = []
        for nid in all_nodes:
            if rest and fnmatch.fnmatch(nid, rest[0]):
                results.extend(_walk_from(dag, nid, rest[1:]) if len(rest) > 1 else [nid])
        return results

    else:
        # Exact or glob match on children
        children = _get_children(dag, node_id)
        matched = [c for c in children if fnmatch.fnmatch(c, seg)]
        if not rest:
            return matched
        results = []
        for child_id in matched:
            results.extend(_walk_from(dag, child_id, rest))
        return results


def _find_anywhere(dag, segments: List[str]) -> List[str]:
    """Find nodes matching segments anywhere in the graph."""
    if not segments:
        return list(dag.nodes.keys())

    target = segments[0]
    rest = segments[1:]

    matched = [nid for nid in dag.nodes if fnmatch.fnmatch(nid, target)]

    if not rest:
        return matched

    results = []
    for nid in matched:
        results.extend(_walk_from(dag, nid, rest))
    return list(dict.fromkeys(results))


def _get_children(dag, node_id: str) -> List[str]:
    """Get direct children of a node (nodes that have this as a parent)."""
    children = []
    for nid, entry in dag.nodes.items():
        if node_id in entry.parents:
            children.append(nid)
    return children


def _get_all_descendants(dag, node_id: str) -> List[str]:
    """Get all descendants via BFS."""
    visited = []
    queue = _get_children(dag, node_id)
    seen = set()
    while queue:
        child = queue.pop(0)
        if child in seen:
            continue
        seen.add(child)
        visited.append(child)
        queue.extend(_get_children(dag, child))
    return visited


def subtree_closure(dag, removing: Set[str]) -> List[str]:
    """Nodes contained under any of ``removing``, by path.

    Node ids *are* namespace paths (``modules/echo/terraform``), so
    containment is carried by the id prefix rather than by parent edges.
    That distinction matters because parent edges conflate two different
    relationships:

      - containment: ``modules/echo`` -> ``modules/echo/greeting``
      - dependency:  ``system/docker`` -> ``modules/echo/terraform``

    Deleting a namespace should take everything it contains — the
    directory model users already expect — without following dependency
    edges out into unrelated subtrees.

    Returns the *additional* ids, parents before children.
    """
    doomed = set(removing)
    added = [
        nid for nid in dag.nodes
        if nid not in doomed
        and any(nid.startswith(f"{root}/") for root in removing)
    ]
    return sorted(added, key=lambda nid: nid.count("/"))


def orphan_closure(dag, removing: Set[str]) -> List[str]:
    """Nodes that would be orphaned by removing ``removing``.

    Removing a node strips it from its children's parent lists. A child
    with other parents survives — it just loses one edge. A child left
    with *no* parents at all is an orphan: it was reachable only through
    the node being deleted, and nothing can resolve it again.

    A node that already has no parents is a root (``settings``,
    ``modules``, ``system``), not an orphan — those are only removed
    when named directly.

    Expands to a fixpoint, since orphaning a node can in turn orphan its
    own children. Returns the *additional* ids in discovery order.

    This deliberately does NOT cascade along every dependency edge.
    ``modules/echo/terraform`` lists ``system/docker`` among its parents
    alongside its own module vars, so a full descendant-cascade on
    ``system/docker`` would take every module in the graph with it.
    """
    doomed = set(removing)
    added: List[str] = []

    changed = True
    while changed:
        changed = False
        for nid, entry in dag.nodes.items():
            if nid in doomed or not entry.parents:
                continue
            if all(pid in doomed for pid in entry.parents):
                doomed.add(nid)
                added.append(nid)
                changed = True

    return added


def delete_closure(dag, removing: Set[str]) -> List[str]:
    """Everything that must go when ``removing`` is deleted.

    Contained subtrees first (the directory model), then anything left
    parentless by the removal (dependency-only children that nothing
    else holds up). Returns the *additional* ids beyond ``removing``.
    """
    contained = subtree_closure(dag, removing)
    orphaned = orphan_closure(dag, set(removing) | set(contained))
    return contained + orphaned
