package queue

import (
	"context"
	"fmt"
	"log/slog"
	"os"

	"gopkg.in/ini.v1"
)

// Mount execution phases
const (
	mountPhaseWritePending = "write_pending" // Phase 1: Write [mnt_*] to mounts.pending.ini
	mountPhaseCheckActive  = "check_active"  // Phase 2: Check if mount moved to mounts.ini
)

// MountExecutor handles both create and delete mount operations
type MountExecutor struct {
	cmd       Command
	logger    *slog.Logger
	operation string // "create" or "delete"
}

// Execute handles the two-phase mount operation lifecycle:
// Phase 1: Write marker to mounts.pending.ini (returns StatusPending)
// Phase 2: Check if entry moved to mounts.ini (returns StatusCompleted or StatusPending)
func (e *MountExecutor) Execute(ctx context.Context, callback ProgressCallback, metadata map[string]interface{}) ExecutionResult {
	mountPoint, ok := e.cmd.Args["mount_point"].(string)
	if !ok || mountPoint == "" {
		return ExecutionResult{Status: StatusFailed, ErrorMsg: "mount_point is required"}
	}

	// Initialize phase if not set (first execution)
	if metadata["phase"] == nil {
		metadata["phase"] = mountPhaseWritePending
	}

	currentPhase := metadata["phase"]
	prevPhase := metadata["prev_phase"]

	// Execute if phase changed (phase != prev_phase)
	if prevPhase != currentPhase {
		switch currentPhase {
		case mountPhaseWritePending:
			return e.executeWritePending(mountPoint, callback, metadata)
		case mountPhaseCheckActive:
			return e.executeCheckActive(mountPoint, callback, metadata)
		}
	}

	// Phase already executed - continue with current phase behavior
	switch currentPhase {
	case mountPhaseWritePending:
		// Already wrote pending, return pending status
		return ExecutionResult{
			Status: StatusPending,
			Result: map[string]interface{}{
				"message": fmt.Sprintf("Mount %s %s staged, awaiting boot", mountPoint, e.operation),
			},
			Metadata: metadata,
		}
	case mountPhaseCheckActive:
		// Keep checking active file
		return e.executeCheckActive(mountPoint, callback, metadata)
	default:
		return ExecutionResult{Status: StatusFailed, ErrorMsg: "unknown mount phase"}
	}
}

// executeWritePending writes mount config to mounts.pending.ini (Phase 1)
func (e *MountExecutor) executeWritePending(mountPoint string, callback ProgressCallback, metadata map[string]interface{}) ExecutionResult {
	disk, ok := e.cmd.Args["disk"].(string)
	if !ok || disk == "" {
		return ExecutionResult{Status: StatusFailed, ErrorMsg: "disk is required"}
	}

	// Validate partition exists and is numeric
	switch e.cmd.Args["partition"].(type) {
	case int, float64:
		// OK
	default:
		return ExecutionResult{Status: StatusFailed, ErrorMsg: "partition must be a number"}
	}

	// Prevent root mount modification
	if mountPoint == "/" {
		return ExecutionResult{Status: StatusFailed, ErrorMsg: "cannot create or modify root mount point"}
	}

	// Notify progress
	callback(ProgressUpdate{
		Status:  "in_progress",
		Message: fmt.Sprintf("Preparing to %s mount %s", e.operation, mountPoint),
	})

	// Sanitize the mount point for INI section ID
	id := sanitizeMountID(mountPoint)

	// Write to mounts.pending.ini
	var writeErr error
	if e.operation == "create" {
		writeErr = writeMountPendingINI(id, e.cmd.Args)
	} else if e.operation == "delete" {
		writeErr = markMountForDeletion(id)
	}

	if writeErr != nil {
		e.logger.Error("failed to write mount marker", "error", writeErr, "operation", e.operation)
		callback(ProgressUpdate{
			Status: "failed",
			Error:  fmt.Sprintf("Failed to %s mount: %v", e.operation, writeErr),
		})
		return ExecutionResult{
			Status:   StatusFailed,
			ErrorMsg: fmt.Sprintf("Failed to %s mount: %v", e.operation, writeErr),
		}
	}

	callback(ProgressUpdate{
		Status:  "pending",
		Message: fmt.Sprintf("Mount %s %s staged in %s for boot-time execution", mountPoint, e.operation, MountsPendingConfigFile),
	})

	// Transition to phase 2
	metadata["prev_phase"] = mountPhaseWritePending
	metadata["phase"] = mountPhaseCheckActive

	return ExecutionResult{
		Status: StatusPending,
		Result: map[string]interface{}{
			"message": fmt.Sprintf("Mount %s %s staged, will be executed at boot", mountPoint, e.operation),
		},
		Metadata: metadata,
	}
}

// executeCheckActive checks if mount has moved from pending.ini to active mounts.ini (Phase 2)
func (e *MountExecutor) executeCheckActive(mountPoint string, callback ProgressCallback, metadata map[string]interface{}) ExecutionResult {
	id := sanitizeMountID(mountPoint)

	activeMounts, err := readMountResultsINI()
	if err == nil && activeMounts != nil {
		if mountData, exists := activeMounts[id]; exists {
			// Mount exists in mounts.ini - operation complete
			if status := mountData["status"]; status == "error" {
				return ExecutionResult{
					Status:   StatusFailed,
					ErrorMsg: mountData["message"],
				}
			}
			// Entry found in active file - success
			return ExecutionResult{
				Status: StatusCompleted,
				Result: map[string]interface{}{
					"message": fmt.Sprintf("Mount %s is now active", mountPoint),
					"status":  "success",
				},
			}
		}
	}

	// Still pending, check again on next retry
	return ExecutionResult{
		Status: StatusPending,
		Result: map[string]interface{}{
			"message": fmt.Sprintf("Mount %s still pending boot completion", mountPoint),
		},
		Metadata: metadata,
	}
}

// writeMountPendingINI writes mount config to /etc/zeropoint/mounts.pending.ini
// Args should contain: mount_point (required), disk, partition
func writeMountPendingINI(id string, args map[string]interface{}) error {
	configDir := "/etc/zeropoint"
	configFile := MountsPendingConfigFile

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

	// Create or update section
	section, err := cfg.NewSection(id)
	if err != nil {
		section, _ = cfg.GetSection(id)
	}

	// Write all args as strings (like cmd_disk.go does)
	for key, value := range args {
		if key != "depends_on" { // Skip depends_on, it's for job manager
			section.Key(key).SetValue(fmt.Sprintf("%v", value))
		}
	}

	// Write file
	if err := cfg.SaveTo(configFile); err != nil {
		return fmt.Errorf("failed to write mounts.pending.ini: %w", err)
	}

	// Ensure proper file permissions
	if err := os.Chmod(configFile, 0600); err != nil {
		return fmt.Errorf("failed to set file permissions: %w", err)
	}

	return nil
}

// markMountForDeletion marks a mount entry for deletion in mounts.pending.ini
func markMountForDeletion(id string) error {
	configDir := "/etc/zeropoint"
	configFile := MountsPendingConfigFile

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

	// Create deletion marker section [!id]
	sectionName := "!" + id
	section, err := cfg.NewSection(sectionName)
	if err != nil {
		section, _ = cfg.GetSection(sectionName)
	}

	// Empty section indicates deletion
	_ = section

	// Write file
	if err := cfg.SaveTo(configFile); err != nil {
		return fmt.Errorf("failed to write mounts.pending.ini: %w", err)
	}

	// Ensure proper file permissions
	if err := os.Chmod(configFile, 0600); err != nil {
		return fmt.Errorf("failed to set file permissions: %w", err)
	}

	return nil
}

// sanitizeMountID converts a mount point path to a valid INI section ID
func sanitizeMountID(mountPoint string) string {
	// Root becomes "root", everything else becomes "mnt_<sanitized>"
	if mountPoint == "/" {
		return "root"
	}

	// Remove leading/trailing slashes and replace internal slashes with underscores
	id := mountPoint
	if id[0] == '/' {
		id = id[1:]
	}
	if id[len(id)-1] == '/' && len(id) > 1 {
		id = id[:len(id)-1]
	}

	// Replace slashes with underscores
	result := ""
	for _, c := range id {
		if c == '/' {
			result += "_"
		} else {
			result += string(c)
		}
	}

	return "mnt_" + result
}
