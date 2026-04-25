"""Typed Inputs projection over parent results.

When a node executes, it receives an Inputs object containing
the results from all its parent nodes. The node queries by type
to get what it needs — like a LINQ projection.
"""

from typing import Type, TypeVar, List, Optional, Dict, Any

T = TypeVar("T")


class Inputs:
    """
    Typed projection over parent node results.

    A node doesn't care *which* parent produced a result — it queries
    by result type. One incoming edge means the projection is the same
    as the incoming type. Multiple edges means a union.

    Usage:
        path = inputs.get(PathResult)        # single result
        vars = inputs.get_all(VarResult)     # all VarResults
        has_mount = inputs.has(MountResult)  # check availability
    """

    EMPTY: "Inputs"  # class-level singleton, set below

    def __init__(self, results: Optional[List[Any]] = None):
        """
        Args:
            results: List of parent result objects (typed dataclass instances)
        """
        self._results = results or []
        self._by_type: Dict[type, List[Any]] = {}
        for r in self._results:
            t = type(r)
            if t not in self._by_type:
                self._by_type[t] = []
            self._by_type[t].append(r)

    def get(self, result_type: Type[T]) -> T:
        """
        Get a single result of the given type.

        Raises:
            KeyError: if no result of this type exists
            ValueError: if multiple results of this type exist
        """
        matches = self._by_type.get(result_type, [])
        if not matches:
            raise KeyError(
                f"No parent result of type {result_type.__name__}. "
                f"Available: {[t.__name__ for t in self._by_type]}"
            )
        if len(matches) > 1:
            raise ValueError(
                f"Multiple results of type {result_type.__name__} — "
                f"use get_all() instead"
            )
        return matches[0]

    def get_all(self, result_type: Type[T]) -> List[T]:
        """Get all results of the given type. Returns empty list if none."""
        return list(self._by_type.get(result_type, []))

    def has(self, result_type: Type[T]) -> bool:
        """Check if any parent produced a result of this type."""
        return result_type in self._by_type

    @property
    def types(self) -> set:
        """Set of all result types present in this projection."""
        return set(self._by_type.keys())

    def __repr__(self) -> str:
        type_counts = {t.__name__: len(v) for t, v in self._by_type.items()}
        return f"Inputs({type_counts})"


# Empty singleton for root nodes
Inputs.EMPTY = Inputs()
