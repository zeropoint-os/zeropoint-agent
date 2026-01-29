package queue

import (
	"context"
	"fmt"
	"log/slog"
)

// Path execution phases
const (
	pathPhaseWritePending = "write_pending" // Phase 1: Write [path_id] to paths.pending.ini
	pathPhaseCheckActive  = "check_active"  // Phase 2: Check if path moved to paths.ini
)

// PathExecutor handles create and delete path operations for mount-based paths
type PathExecutor struct {
	cmd       Command
	logger    *slog.Logger
	operation string // "create" or "delete"
}

// Execute handles the two-phase path operation lifecycle:
// Phase 1 (write_pending): Write marker to paths.pending.ini (returns StatusPending)
// Phase 2 (check_active): Check if entry moved to paths.ini (returns StatusCompleted or StatusPending)
func (e *PathExecutor) Execute(ctx context.Context, callback ProgressCallback, metadata map[string]interface{}) ExecutionResult {
	mount, ok := e.cmd.Args["mount"].(string)
	if !ok || mount == "" {
		return ExecutionResult{Status: StatusFailed, ErrorMsg: "mount is required"}
	}

	pathSuffix, ok := e.cmd.Args["path_suffix"].(string)
	if !ok || pathSuffix == "" {
		return ExecutionResult{Status: StatusFailed, ErrorMsg: "path_suffix is required"}
	}

	// Validate path_suffix (no directory traversal)
	if err := ValidatePathSuffix(pathSuffix); err != nil {
		return ExecutionResult{Status: StatusFailed, ErrorMsg: err.Error()}
	}

	pathID := sanitizePathID(mount, pathSuffix)

	// Initialize phase if not set (first execution)
	if metadata["phase"] == nil {
		metadata["phase"] = pathPhaseWritePending
	}

	currentPhase := metadata["phase"]
	prevPhase := metadata["prev_phase"]

	// Execute if phase changed (phase != prev_phase)
	if prevPhase != currentPhase {
		switch currentPhase {
		case pathPhaseWritePending:
			return e.executeWritePending(mount, pathSuffix, callback, metadata)
		case pathPhaseCheckActive:
			return e.executeCheckActive(mount, pathSuffix, callback, metadata)
		}
	}

	// Phase already executed - continue with current phase behavior
	switch currentPhase {
	case pathPhaseWritePending:
		// Already wrote pending, return pending status
		return ExecutionResult{
			Status: StatusPending,
			Result: map[string]interface{}{
				"message": fmt.Sprintf("Path %s/%s creation staged, awaiting boot", mount, pathSuffix),
				"path_id": pathID,
			},
			Metadata: metadata,
		}
	case pathPhaseCheckActive:
		// Keep checking active file
		return e.executeCheckActive(mount, pathSuffix, callback, metadata)
	default:
		return ExecutionResult{Status: StatusFailed, ErrorMsg: "unknown path phase"}
	}
}

// executeWritePending writes path config to paths.pending.ini (Phase 1)
func (e *PathExecutor) executeWritePending(mount, pathSuffix string, callback ProgressCallback, metadata map[string]interface{}) ExecutionResult {
	// Validate mount exists in mounts.ini
	activeMounts, err := readMountResultsINI()
	if err != nil || activeMounts == nil || len(activeMounts) == 0 {
		return ExecutionResult{
			Status:   StatusFailed,
			ErrorMsg: fmt.Sprintf("Mount %s must be active before creating paths", mount),
		}
	}

	if _, exists := activeMounts[mount]; !exists {
		return ExecutionResult{
			Status:   StatusFailed,
			ErrorMsg: fmt.Sprintf("Mount %s does not exist or is not active", mount),
		}
	}

	callback(ProgressUpdate{
		Status:  "in_progress",
		Message: fmt.Sprintf("Preparing to %s path %s/%s", e.operation, mount, pathSuffix),
	})

	pathID := sanitizePathID(mount, pathSuffix)

	// Write operation marker to pending.ini
	var writeErr error
	if e.operation == "create" {
		writeErr = writePathsPendingINI(pathID, e.cmd.Args)
	} else if e.operation == "delete" {
		writeErr = markPathForDeletion(pathID)
	}

	if writeErr != nil {
		e.logger.Error("failed to write paths marker", "error", writeErr, "operation", e.operation)
		callback(ProgressUpdate{
			Status: "failed",
			Error:  fmt.Sprintf("Failed to stage path %s: %v", e.operation, writeErr),
		})
		return ExecutionResult{
			Status:   StatusFailed,
			ErrorMsg: fmt.Sprintf("Failed to stage path %s: %v", e.operation, writeErr),
		}
	}

	callback(ProgressUpdate{
		Status:  "pending",
		Message: fmt.Sprintf("Path %s/%s %s staged in %s for boot-time execution", mount, pathSuffix, e.operation, PathsPendingConfigFile),
	})

	// Transition to phase 2
	metadata["prev_phase"] = pathPhaseWritePending
	metadata["phase"] = pathPhaseCheckActive

	return ExecutionResult{
		Status: StatusPending,
		Result: map[string]interface{}{
			"message": fmt.Sprintf("Path %s/%s %s staged, awaiting boot", mount, pathSuffix, e.operation),
			"path_id": pathID,
		},
		Metadata: metadata,
	}
}

// executeCheckActive checks if path has moved from pending.ini to active paths.ini (Phase 2)
func (e *PathExecutor) executeCheckActive(mount, pathSuffix string, callback ProgressCallback, metadata map[string]interface{}) ExecutionResult {
	pathID := sanitizePathID(mount, pathSuffix)

	activePaths, err := readPathsActivINI()
	if err == nil && activePaths != nil {
		if pathData, exists := activePaths[pathID]; exists {
			// Path exists in paths.ini - operation complete
			if status := pathData["status"]; status == "error" {
				return ExecutionResult{
					Status:   StatusFailed,
					ErrorMsg: pathData["message"],
				}
			}
			// Entry found in active file - success
			return ExecutionResult{
				Status: StatusCompleted,
				Result: map[string]interface{}{
					"message": fmt.Sprintf("Path %s/%s is now active", mount, pathSuffix),
					"status":  "success",
					"path_id": pathID,
				},
			}
		}
	}

	// Still pending, check again on next retry
	return ExecutionResult{
		Status: StatusPending,
		Result: map[string]interface{}{
			"message": fmt.Sprintf("Path %s/%s still pending boot completion", mount, pathSuffix),
		},
		Metadata: metadata,
	}
}
