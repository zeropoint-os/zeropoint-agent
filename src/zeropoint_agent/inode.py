"""INode — the fundamental unit of the graph-based language.

A node is a typed I → O expression. The output type O is the contract —
the shape of what this node produces. Edges are type-checked bindings:
parent.O must match child.I.

dag.add() is the compiler — validates types at construction time.
dag.resolve() is the runtime — propagates values through the graph.
"""

from abc import ABC, abstractmethod
from typing import Generic, TypeVar

I = TypeVar("I")  # Input type (parent's output contract)
O = TypeVar("O")  # Output type (this node's contract)


class INode(ABC, Generic[I, O]):
    """
    A typed transform in the graph: I → O.

    - resolve(): produce O from I (runtime, may have side effects)
    - verify(): does the actual state match desired? (probe reality)
    - remove(): tear down what this node created
    """

    @abstractmethod
    def resolve(self, input: I) -> O:
        """
        Produce output from input.

        Called by the executor during dag.resolve(). The parent's
        resolved output flows in as `input`. This node's config
        (its own fields) is the desired state.

        Returns:
            The resolved output value (contract O filled with runtime values)
        """
        ...

    @abstractmethod
    def verify(self) -> bool:
        """
        Does actual system state match this node's desired state?

        Probes reality (disk exists? partition formatted? container running?)
        and compares against self. The node knows its own domain — the
        executor just asks "are you done?"

        Returns:
            True if actual matches desired (converged)
        """
        ...

    @abstractmethod
    def remove(self) -> bool:
        """
        Tear down what this node represents.

        Returns:
            True if successfully removed
        """
        ...
