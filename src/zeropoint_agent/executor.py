"""Reconciliation executor.

Implements Phases 0-5 of the reconciliation pipeline:
- Phase 0: Probe Reality (host state)
- Phase 1: Storage reconciliation (disks, mounts, paths)
- Phase 2: Vars materialization (resolve ${...} references)
- Phase 3: Modules reconciliation (terraform)
- Phase 4: Exposures reconciliation (networking)
- Phase 5: Final probe and convergence check

Each phase has exit conditions:
- Stop if reboot-gated change encountered
- Stop if phase fails with error
- Stop if executor reports missing prerequisites
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional, List, Dict, Any
from .state_store import StateStore


class ReconcileResult(str, Enum):
    """Result of reconciliation.
    
    - APPLIED: Edit successfully converged, merged to main
    - RUNNING: Executor actively reconciling (you just triggered this)
    - BLOCKED: Hit reboot barrier, waiting for reboot
    - WAITING: Edit queued, previous edit's command still running
    - FAILED: Unrecoverable error, stopped
    """
    APPLIED = "applied"
    RUNNING = "running"
    BLOCKED = "blocked"
    WAITING = "waiting"
    FAILED = "failed"


@dataclass
class BlockedReason(str, Enum):
    """Why reconciliation was blocked or waiting."""
    REBOOT_REQUIRED = "reboot_required"
    COMMAND_RUNNING = "command_running"
    MISSING_DEPENDENCY = "missing_dependency"


@dataclass
class ReconcileError:
    """Detailed error from failed reconciliation."""
    resource: str
    message: str


@dataclass
class ReconcileResponse:
    """Response from reconciliation attempt."""
    result: ReconcileResult
    reason: Optional[BlockedReason] = None
    error: Optional[ReconcileError] = None
    pending_resources: List[str] = None


class Executor:
    """Reconciliation engine.
    
    Drives the edit branch towards convergence with reality.
    Each call to reconcile() attempts to merge commits from edit to main
    while advancing Reality as close to Intent as possible.
    
    State machine:
    - IDLE: Ready to reconcile
    - RUNNING: Currently executing phases (set by reconcile() caller)
    - BLOCKED: Hit reboot barrier (returned to caller)
    - WAITING: Previous operation still running (returned to caller)
    - FAILED: Unrecoverable error (returned to caller)
    """

    def __init__(self, state_store: StateStore):
        self.store = state_store
        self.state = "IDLE"  # IDLE, RUNNING, BLOCKED
        self.reboot_required = False
        self.pending_resources: List[str] = []
        self.last_committed_sha: Optional[str] = None

    def is_running(self) -> bool:
        """Check if reconciliation is currently active."""
        return self.state == "RUNNING"

    def reconcile(self, intent_id: Optional[str] = None) -> ReconcileResponse:
        """Main reconciliation entry point.
        
        - If already running, return WAITING
        - Gets next unmerged commit from edit branch
        - Runs phases 0-5
        - If successful, merges to main
        - Returns result
        
        Args:
            intent_id: Optional SHA of the edit that triggered this.
                      If provided and already running, returns WAITING.
        """
        # Check if already running
        if self.is_running():
            return ReconcileResponse(
                result=ReconcileResult.WAITING,
                reason=BlockedReason.COMMAND_RUNNING,
            )

        # Mark as running
        self.state = "RUNNING"

        try:
            # Phase 0: Probe Reality
            actual_state = self._phase_0_probe()
            if not actual_state:
                return ReconcileResponse(
                    result=ReconcileResult.FAILED,
                    error=ReconcileError("probe", "Failed to probe system state")
                )

            # Get desired state from edit branch
            desired_state = self._get_desired_state()

            # Phase 1: Storage reconciliation
            storage_result = self._phase_1_storage(desired_state, actual_state)
            if storage_result.result != ReconcileResult.APPLIED:
                return storage_result

            # Phase 2: Vars materialization
            vars_result = self._phase_2_vars(desired_state)
            if vars_result.result != ReconcileResult.APPLIED:
                return vars_result

            # Phase 3: Modules (terraform)
            modules_result = self._phase_3_modules(desired_state)
            if modules_result.result != ReconcileResult.APPLIED:
                return modules_result

            # Phase 4: Exposures (networking)
            exposures_result = self._phase_4_exposures(desired_state)
            if exposures_result.result != ReconcileResult.APPLIED:
                return exposures_result

            # Phase 5: Final probe and convergence
            final_result = self._phase_5_convergence(actual_state, desired_state)
            
            # Mark as IDLE before returning
            self.state = "IDLE"
            return final_result

        except Exception as e:
            self.state = "IDLE"
            return ReconcileResponse(
                result=ReconcileResult.FAILED,
                error=ReconcileError("executor", str(e))
            )

    def _phase_0_probe(self) -> Optional[Dict[str, Any]]:
        """Phase 0: Probe Reality.
        
        Collects current system state:
        - Disks (lsblk)
        - Mounts (mount)
        - Running modules (docker ps, systemctl)
        - Active exposures (ss -tlnp)
        
        Returns dict with 'disks', 'mounts', 'modules', 'exposures' keys.
        """
        # TODO: Implement probes
        # For now, return a minimal structure
        return {
            "disks": self.store.get_actual_resources("disks"),
            "mounts": self.store.get_actual_resources("mounts"),
            "paths": self.store.get_actual_resources("paths"),
            "modules": self.store.get_actual_resources("modules"),
            "exposures": self.store.get_actual_resources("exposures"),
        }

    def _get_desired_state(self) -> Dict[str, Any]:
        """Get desired state from edit branch."""
        return {
            "disks": self.store.get_desired_resources("disks"),
            "mounts": self.store.get_desired_resources("mounts"),
            "paths": self.store.get_desired_resources("paths"),
            "vars": self.store.get_desired_resources("vars"),
            "modules": self.store.get_desired_resources("modules"),
            "exposures": self.store.get_desired_resources("exposures"),
        }

    def _phase_1_storage(
        self, desired: Dict[str, Any], actual: Dict[str, Any]
    ) -> ReconcileResponse:
        """Phase 1: Storage reconciliation.
        
        Reconciles disks, mounts, and paths in order:
        1. Disks: partition, format, create filesystems
        2. Mounts: mount/unmount
        3. Paths: create/remove directories, chmod
        
        Detects reboot-gated operations (partition changes).
        """
        # TODO: Implement storage reconciliation
        # For now, just check if desired matches actual
        
        desired_disks = {d["id"]: d for d in desired.get("disks", [])}
        actual_disks = {d["id"]: d for d in actual.get("disks", [])}

        if desired_disks != actual_disks:
            # Check if any changes would require reboot
            for did in desired_disks:
                if did not in actual_disks:
                    # New disk, might require reboot
                    self.state = "IDLE"
                    self.reboot_required = True
                    return ReconcileResponse(
                        result=ReconcileResult.BLOCKED,
                        reason=BlockedReason.REBOOT_REQUIRED,
                    )

        return ReconcileResponse(result=ReconcileResult.APPLIED)

    def _phase_2_vars(self, desired: Dict[str, Any]) -> ReconcileResponse:
        """Phase 2: Vars materialization.
        
        Resolves ${var_name} and ${path:id} references in var values.
        Validates that all references can be resolved.
        """
        # TODO: Implement var resolution
        return ReconcileResponse(result=ReconcileResult.APPLIED)

    def _phase_3_modules(self, desired: Dict[str, Any]) -> ReconcileResponse:
        """Phase 3: Modules reconciliation.
        
        Executes AddModule, EditModule, UninstallModule commands
        to bring actual module state in line with desired.
        """
        # TODO: Implement module reconciliation
        return ReconcileResponse(result=ReconcileResult.APPLIED)

    def _phase_4_exposures(self, desired: Dict[str, Any]) -> ReconcileResponse:
        """Phase 4: Exposures reconciliation.
        
        Sets up networking, envoy routes, tailscale config, etc.
        """
        # TODO: Implement exposures reconciliation
        return ReconcileResponse(result=ReconcileResult.APPLIED)

    def _phase_5_convergence(
        self, actual: Dict[str, Any], desired: Dict[str, Any]
    ) -> ReconcileResponse:
        """Phase 5: Final probe and convergence check.
        
        Re-probes actual state and verifies it matches desired.
        Merges successful commits to main.
        """
        # TODO: Implement final convergence check
        return ReconcileResponse(result=ReconcileResult.APPLIED)
