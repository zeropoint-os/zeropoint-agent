"""INode — the fundamental unit of the graph-based language.

A node is a typed I → O expression. The output type O is the contract.
Edges are type-checked bindings: parent.O must match child.I.

dag.add() is the compiler — validates types at construction time.
dag.resolve() is the runtime — propagates values through the graph.

All node methods return NodeResult[O], which bundles:
  - output (the O value)
  - status (SUCCESS, PENDING_REBOOT, ERROR, etc.)
  - systemd_units (deferred work for next boot)
  - error message (if any)
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Generic, TypeVar, Optional, List

I = TypeVar("I")  # Input type (parent's output contract)
O = TypeVar("O")  # Output type (this node's contract)


class ResolveMode(Enum):
    """How to execute the graph."""
    LIVE = "live"
    DRY_RUN = "dry_run"
    MOCK = "mock"


class NodeStatus(Enum):
    """Status of a node in the graph."""
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    SUCCESS_SKIP = "success_skip"   # succeeded, children can skip
    ERROR = "error"
    BLOCKED = "blocked"
    SKIPPED = "skipped"
    PENDING_REBOOT = "pending_reboot"


@dataclass
class SystemdUnit:
    """A systemd unit to be written for deferred execution."""
    name: str                    # e.g. "zeropoint-part-sda1"
    description: str
    exec_start: str
    after: List[str] = field(default_factory=list)   # systemd After= deps
    requires: List[str] = field(default_factory=list) # systemd Requires= deps
    condition_path_exists: Optional[str] = None       # skip if exists
    condition_path_not_exists: Optional[str] = None   # skip if not exists
    timeout_sec: int = 300
    remain_after_exit: bool = True

    def render(self) -> str:
        """Render as a systemd unit file string."""
        after = ""
        if self.after:
            after = "After=" + " ".join(self.after) + "\n"
        requires = ""
        if self.requires:
            requires = "Requires=" + " ".join(self.requires) + "\n"
        conditions = ""
        if self.condition_path_exists:
            conditions += f"ConditionPathExists={self.condition_path_exists}\n"
        if self.condition_path_not_exists:
            conditions += f"ConditionPathExists=!{self.condition_path_not_exists}\n"

        return f"""[Unit]
Description=ZeroPoint: {self.description}
{after}{requires}{conditions}DefaultDependencies=no

[Service]
Type=oneshot
RemainAfterExit={'yes' if self.remain_after_exit else 'no'}
ExecStart={self.exec_start}
TimeoutSec={self.timeout_sec}
StandardOutput=journal+console
StandardError=journal+console

[Install]
WantedBy=multi-user.target
"""


class NodeResult(Generic[O]):
    """
    Result of a node operation (resolve, verify, remove).

    Bundles output, status, deferred work, and errors into one object.
    Chainable builders for clean node implementations.
    """

    def __init__(self):
        self.output: Optional[O] = None
        self.status: NodeStatus = NodeStatus.PENDING
        self.systemd_units: List[SystemdUnit] = []
        self.error: Optional[str] = None

    # --- Chainable builders ---

    @staticmethod
    def success(output: O = None) -> "NodeResult[O]":
        r = NodeResult()
        r.output = output
        r.status = NodeStatus.SUCCESS
        return r

    @staticmethod
    def success_skip(output: O = None) -> "NodeResult[O]":
        """Succeeded, but children can skip — the goal is already met."""
        r = NodeResult()
        r.output = output
        r.status = NodeStatus.SUCCESS_SKIP
        return r

    @staticmethod
    def skipped(reason: str = "") -> "NodeResult[O]":
        """Node is not relevant — children will be skipped too."""
        r = NodeResult()
        r.status = NodeStatus.SKIPPED
        r.error = reason or None
        return r

    @staticmethod
    def pending_reboot(output: O = None) -> "NodeResult[O]":
        r = NodeResult()
        r.output = output
        r.status = NodeStatus.PENDING_REBOOT
        return r

    @staticmethod
    def failed(error: str, output: O = None) -> "NodeResult[O]":
        r = NodeResult()
        r.output = output
        r.status = NodeStatus.ERROR
        r.error = error
        return r

    def add_unit(self, unit: SystemdUnit) -> "NodeResult[O]":
        self.systemd_units.append(unit)
        return self

    def __repr__(self) -> str:
        units = f", {len(self.systemd_units)} units" if self.systemd_units else ""
        err = f", error={self.error!r}" if self.error else ""
        return f"NodeResult({self.status.value}{units}{err})"


class INode(ABC, Generic[I, O]):
    """
    A typed transform in the graph: I → O.

    All methods take a ResolveMode so the node can adapt its behavior:
      LIVE     — real operations, real side effects
      DRY_RUN  — real verify, skip execution
      MOCK     — simulated everything

    All methods return NodeResult[O]:
      - output: the O value (contract filled with runtime data)
      - status: SUCCESS, PENDING_REBOOT, ERROR
      - systemd_units: deferred work for next boot
      - error: message if something went wrong
    """

    @abstractmethod
    def resolve(self, input: I, mode: ResolveMode) -> NodeResult[O]:
        """
        Produce output from input.

        In LIVE mode, perform real operations (sfdisk, mkfs, terraform, etc.)
        In MOCK mode, return plausible output without side effects.
        In DRY_RUN mode, return what would happen without executing.
        """
        ...

    @abstractmethod
    def verify(self, mode: ResolveMode) -> NodeResult[O]:
        """
        Check if actual state matches desired state.

        In LIVE/DRY_RUN, probe real system state.
        In MOCK, simulate verification.

        Returns SUCCESS if converged, PENDING if not.
        """
        ...

    @abstractmethod
    def remove(self, mode: ResolveMode) -> NodeResult[O]:
        """
        Tear down what this node represents.

        May return PENDING_REBOOT with systemd units for deferred teardown.
        """
        ...
