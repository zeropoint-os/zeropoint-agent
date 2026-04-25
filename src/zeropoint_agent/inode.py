"""INode — typed interface for DAG nodes.

Each node type implements this interface with its own Desired/Result
dataclass pair. The executor calls verify() to check convergence,
add() to apply, and remove() to tear down.

Type validation happens in two phases:
  Phase 1 (edge creation): accepted_inputs ⊆ union(incoming result types)
  Phase 2 (execution): concrete values validated when flowing through edges
"""

from abc import ABC, abstractmethod
from typing import Generic, TypeVar, Set, Type, ClassVar

from zeropoint_agent.inputs import Inputs

D = TypeVar("D")  # Desired type
R = TypeVar("R")  # Result type


class INode(ABC, Generic[D, R]):
    """
    Interface for DAG nodes.

    Subclasses declare:
        accepted_inputs: Set of result types this node can consume
        desired_type: The dataclass type for desired state
        result_type: The dataclass type for produced results

    The executor provides an Inputs projection containing parent results.
    """

    accepted_inputs: ClassVar[Set[Type]] = set()

    @abstractmethod
    def verify(self, desired: D) -> bool:
        """
        Check if actual state matches desired state.

        Called on every reconcile pass. If True, node is converged (SUCCESS).
        Should probe real system state — not just return cached values.

        Args:
            desired: The desired state declaration

        Returns:
            True if actual matches desired (no action needed)
        """
        ...

    @abstractmethod
    def add(self, inputs: Inputs, desired: D) -> R:
        """
        Create or apply a resource to match desired state.

        Called when verify() returns False. Parent results are available
        via the typed Inputs projection.

        Args:
            inputs: Typed projection of parent node results
            desired: The desired state to achieve

        Returns:
            Result object (made available to child nodes)

        Raises:
            Exception on failure (node goes to ERROR status)
        """
        ...

    @abstractmethod
    def remove(self, desired: D) -> bool:
        """
        Remove/destroy a resource.

        Args:
            desired: The desired state (for identifying what to remove)

        Returns:
            True if successfully removed
        """
        ...

    def move(self, source: D, dest: D) -> bool:
        """Migrate resource. Override if supported."""
        raise NotImplementedError(
            f"move() not supported for {type(self).__name__}"
        )

    def copy(self, source: D, dest: D) -> bool:
        """Duplicate resource. Override if supported."""
        raise NotImplementedError(
            f"copy() not supported for {type(self).__name__}"
        )
