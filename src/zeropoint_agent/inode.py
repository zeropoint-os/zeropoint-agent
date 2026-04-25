"""INode — the fundamental unit of the graph-based language.

A node is a typed I → O expression. The output type O is the contract —
the shape of what this node produces. Edges are type-checked bindings:
parent.O must match child.I.

dag.add() is the compiler — validates types at construction time.
dag.resolve() is the runtime — propagates values through the graph.
"""

from abc import ABC, abstractmethod
from enum import Enum
from typing import Generic, TypeVar, Optional

I = TypeVar("I")  # Input type (parent's output contract)
O = TypeVar("O")  # Output type (this node's contract)


class ResolveMode(Enum):
    """How to execute the graph."""
    LIVE = "live"          # real verify + real resolve, side effects
    DRY_RUN = "dry_run"    # real verify, skip resolve, no side effects
    MOCK = "mock"          # simulated verify + resolve, no side effects


class INode(ABC, Generic[I, O]):
    """
    A typed transform in the graph: I → O.

    - resolve(): produce O from I (runtime, side effects in live mode)
    - mock_resolve(): produce plausible O without side effects
    - verify(): probe reality, does actual match desired?
    - mock_verify(): simulated verify for testing
    - remove(): tear down
    - systemd_unit(): emit a systemd unit file for deferred execution
    """

    @abstractmethod
    def resolve(self, input: I) -> O:
        """Produce output from input. Called in live mode. May have side effects."""
        ...

    @abstractmethod
    def verify(self) -> bool:
        """Probe reality — does actual state match desired? Called in live and dry_run modes."""
        ...

    @abstractmethod
    def mock_resolve(self, input: I) -> O:
        """Produce a plausible output without side effects. Called in mock mode."""
        ...

    def mock_verify(self) -> bool:
        """Simulated verify for mock mode. Default: False (not converged yet)."""
        return False

    @abstractmethod
    def remove(self) -> bool:
        """Tear down what this node represents."""
        ...

    def systemd_unit(self, node_id: str, parent_ids: list) -> Optional[str]:
        """
        Emit a systemd unit file for deferred execution.

        Called when a node goes PENDING_REBOOT — the node can't converge
        in this pass and needs work done on the next boot.

        Returns:
            Unit file contents as a string, or None if this node
            doesn't need a systemd unit (converges in-process).
        """
        return None
