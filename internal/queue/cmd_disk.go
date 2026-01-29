package queue

import (
	"context"
	"fmt"
	"log/slog"
	"os"

	"gopkg.in/ini.v1"
)

// Disk execution phases
const (
	diskPhaseWritePending = "write_pending" // Phase 1: Write [id] to disks.pending.ini
	diskPhaseCheckActive  = "check_active"  // Phase 2: Check if disk moved to disks.ini
)

// DiskExecutor handles both manage and release disk operations
type DiskExecutor struct {
	cmd       Command
	logger    *slog.Logger
	operation string // "manage" or "release"
}

// Execute handles the two-phase disk operation lifecycle:
// Phase 1 (write_pending): Write marker to disks.pending.ini (returns StatusPending)
// Phase 2 (check_active): Check if entry moved to disks.ini (returns StatusCompleted or StatusPending)
func (e *DiskExecutor) Execute(ctx context.Context, callback ProgressCallback, metadata map[string]interface{}) ExecutionResult {
	diskID, ok := e.cmd.Args["id"].(string)
	if !ok || diskID == "" {
		return ExecutionResult{Status: StatusFailed, ErrorMsg: "disk id is required"}
	}

	// Initialize phase if not set (first execution)
	if metadata["phase"] == nil {
		metadata["phase"] = diskPhaseWritePending
	}

	currentPhase := metadata["phase"]
	prevPhase := metadata["prev_phase"]

	// Execute if phase changed (phase != prev_phase)
	if prevPhase != currentPhase {
		switch currentPhase {
		case diskPhaseWritePending:
			return e.executeWritePending(diskID, callback, metadata)
		case diskPhaseCheckActive:
			return e.executeCheckActive(diskID, callback, metadata)
		}
	}

	// Phase already executed - continue with current phase behavior
	switch currentPhase {
	case diskPhaseWritePending:
		// Already wrote pending, return pending status
		return ExecutionResult{
			Status: StatusPending,
			Result: map[string]interface{}{
				"message": fmt.Sprintf("Disk %s %s staged, awaiting boot", diskID, e.operation),
			},
			Metadata: metadata,
		}
	case diskPhaseCheckActive:
		// Keep checking active file
		return e.executeCheckActive(diskID, callback, metadata)
	default:
		return ExecutionResult{Status: StatusFailed, ErrorMsg: "unknown disk phase"}
	}
}

// executeWritePending writes disk operation marker to disks.pending.ini (Phase 1)
func (e *DiskExecutor) executeWritePending(diskID string, callback ProgressCallback, metadata map[string]interface{}) ExecutionResult {
	callback(ProgressUpdate{
		Status:  "in_progress",
		Message: fmt.Sprintf("Preparing to %s disk %s", e.operation, diskID),
	})

	// Write operation marker to pending.ini
	var writeErr error
	if e.operation == "manage" {
		writeErr = writeStoragePendingINI(diskID, e.cmd.Args)
	} else if e.operation == "release" {
		writeErr = writeStorageDeletionMarker(diskID)
	}

	if writeErr != nil {
		e.logger.Error("failed to write disk marker", "error", writeErr, "operation", e.operation)
		callback(ProgressUpdate{
			Status: "failed",
			Error:  fmt.Sprintf("Failed to %s disk: %v", e.operation, writeErr),
		})
		return ExecutionResult{
			Status:   StatusFailed,
			ErrorMsg: fmt.Sprintf("Failed to %s disk: %v", e.operation, writeErr),
		}
	}

	callback(ProgressUpdate{
		Status:  "pending",
		Message: fmt.Sprintf("Disk %s %s staged in %s for boot-time execution", diskID, e.operation, DisksPendingConfigFile),
	})

	// Transition to phase 2
	metadata["prev_phase"] = diskPhaseWritePending
	metadata["phase"] = diskPhaseCheckActive

	return ExecutionResult{
		Status: StatusPending,
		Result: map[string]interface{}{
			"message": fmt.Sprintf("Disk %s %s staged, will be executed at boot", diskID, e.operation),
		},
		Metadata: metadata,
	}
}

// executeCheckActive checks if disk has moved from pending.ini to active disks.ini (Phase 2)
func (e *DiskExecutor) executeCheckActive(diskID string, callback ProgressCallback, metadata map[string]interface{}) ExecutionResult {
	managedDisks, err := readDisksINI()
	if err == nil && managedDisks != nil {
		if e.operation == "manage" {
			// For manage: check if disk is now in disks.ini
			if _, isManaged := managedDisks[diskID]; isManaged {
				// Disk is in managed list - operation completed
				return ExecutionResult{
					Status: StatusCompleted,
					Result: map[string]interface{}{
						"message": "Disk management completed",
						"status":  "success",
					},
				}
			}
		} else if e.operation == "release" {
			// For release: check if disk is no longer in disks.ini
			if _, stillManaged := managedDisks[diskID]; !stillManaged {
				// Disk is no longer in managed list - operation completed
				return ExecutionResult{
					Status: StatusCompleted,
					Result: map[string]interface{}{
						"message": "Disk release completed",
						"status":  "success",
					},
				}
			}
		}
	}

	// Still pending, check again on next retry
	return ExecutionResult{
		Status: StatusPending,
		Result: map[string]interface{}{
			"message": fmt.Sprintf("Disk %s still pending boot completion", diskID),
		},
		Metadata: metadata,
	}
}

// writeStoragePendingINI writes disk management spec to /etc/zeropoint/disks.pending.ini
// Args should contain: id (required), auto_partition, wipefs, filesystem, label, luks, lvm
func writeStoragePendingINI(diskID string, args map[string]interface{}) error {
	configDir := "/etc/zeropoint"
	configFile := DisksPendingConfigFile

	// Create config directory if it doesn't exist
	if err := os.MkdirAll(configDir, 0755); err != nil {
		return fmt.Errorf("failed to create config directory: %w", err)
	}

	// Load existing INI or create new
	cfg := ini.Empty()
	if _, err := os.Stat(configFile); err == nil {
		var loadErr error
		cfg, loadErr = ini.Load(configFile)
		if loadErr != nil {
			cfg = ini.Empty()
		}
	}

	// Create or update section for this disk
	section, err := cfg.NewSection(diskID)
	if err != nil {
		section, _ = cfg.GetSection(diskID)
	}

	// Store management configuration parameters from args
	for key, value := range args {
		// Skip the id field (that's the section name)
		if key == "id" {
			continue
		}
		section.Key(key).SetValue(fmt.Sprintf("%v", value))
	}

	// Write file with restricted permissions
	if err := cfg.SaveTo(configFile); err != nil {
		return fmt.Errorf("failed to write disks.pending.ini: %w", err)
	}

	// Ensure proper file permissions (root-readable only)
	if err := os.Chmod(configFile, 0600); err != nil {
		return fmt.Errorf("failed to set file permissions: %w", err)
	}

	return nil
}

// writeStorageDeletionMarker marks a disk for deletion in disks.pending.ini with [!diskid] section
func writeStorageDeletionMarker(diskID string) error {
	configDir := "/etc/zeropoint"
	configFile := DisksPendingConfigFile

	// Create config directory if it doesn't exist
	if err := os.MkdirAll(configDir, 0755); err != nil {
		return fmt.Errorf("failed to create config directory: %w", err)
	}

	// Load existing INI or create new
	cfg := ini.Empty()
	if _, err := os.Stat(configFile); err == nil {
		var loadErr error
		cfg, loadErr = ini.Load(configFile)
		if loadErr != nil {
			cfg = ini.Empty()
		}
	}

	// Create deletion marker section [!diskid]
	sectionName := "!" + diskID
	section, err := cfg.NewSection(sectionName)
	if err != nil {
		section, _ = cfg.GetSection(sectionName)
	}

	// Empty section indicates deletion - just ensure it exists
	_ = section

	// Write file
	if err := cfg.SaveTo(configFile); err != nil {
		return fmt.Errorf("failed to write disks.pending.ini: %w", err)
	}

	// Ensure proper file permissions
	if err := os.Chmod(configFile, 0600); err != nil {
		return fmt.Errorf("failed to set file permissions: %w", err)
	}

	return nil
}
