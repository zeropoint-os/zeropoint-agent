package queue

import (
	"context"
	"fmt"
	"log/slog"
	"os"

	"gopkg.in/ini.v1"
)

// MountExecutor handles both create and delete mount operations
type MountExecutor struct {
	cmd       Command
	logger    *slog.Logger
	operation string // "create" or "delete"
}

// Execute stages the mount operation for boot-time execution
// Retryable: if the operation was already completed by the boot service, returns StatusCompleted
func (e *MountExecutor) Execute(ctx context.Context, callback ProgressCallback, metadata map[string]interface{}) ExecutionResult {
	mountPoint, ok := e.cmd.Args["mount_point"].(string)
	if !ok || mountPoint == "" {
		return ExecutionResult{Status: StatusFailed, ErrorMsg: "mount_point is required"}
	}

	// Check if this operation already completed in mounts.ini
	existingResults, err := readMountResultsINI()
	if err == nil {
		id := sanitizeMountID(mountPoint)
		for _, result := range existingResults {
			if sanitizeMountID(result.MountPoint) == id {
				// Operation already completed by boot service
				if result.Status == "success" {
					return ExecutionResult{
						Status: StatusCompleted,
						Result: map[string]interface{}{
							"message": result.Message,
							"status":  "success",
						},
					}
				} else if result.Status == "error" {
					return ExecutionResult{
						Status:   StatusFailed,
						ErrorMsg: result.Message,
					}
				}
			}
		}
	}

	// Operation not yet complete - execute based on operation type
	switch e.operation {
	case "create":
		return e.executeCreate(mountPoint, callback, metadata)
	case "delete":
		return e.executeDelete(mountPoint, callback, metadata)
	default:
		return ExecutionResult{Status: StatusFailed, ErrorMsg: "unknown mount operation: " + e.operation}
	}
}

// executeCreate writes mount config to mounts.pending.ini
func (e *MountExecutor) executeCreate(mountPoint string, callback ProgressCallback, metadata map[string]interface{}) ExecutionResult {
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
		Message: fmt.Sprintf("Preparing to create mount %s", mountPoint),
	})

	// Sanitize the mount point for INI section ID
	id := sanitizeMountID(mountPoint)

	// Write to mounts.pending.ini
	if err := writeMountPendingINI(id, e.cmd.Args); err != nil {
		e.logger.Error("failed to write mounts.pending.ini", "error", err)
		callback(ProgressUpdate{
			Status: "failed",
			Error:  fmt.Sprintf("Failed to stage mount: %v", err),
		})
		return ExecutionResult{
			Status:   StatusFailed,
			ErrorMsg: fmt.Sprintf("Failed to stage mount: %v", err),
		}
	}

	callback(ProgressUpdate{
		Status:  "pending",
		Message: fmt.Sprintf("Mount %s staged in %s for boot-time execution", mountPoint, MountsPendingConfigFile),
	})

	return ExecutionResult{
		Status: StatusPending,
		Result: map[string]interface{}{
			"message": fmt.Sprintf("Mount %s created in pending file, will be executed at boot", mountPoint),
		},
	}
}

// executeDelete marks a mount for deletion in mounts.pending.ini
func (e *MountExecutor) executeDelete(mountPoint string, callback ProgressCallback, metadata map[string]interface{}) ExecutionResult {
	// Prevent root mount deletion
	if mountPoint == "/" {
		return ExecutionResult{Status: StatusFailed, ErrorMsg: "cannot delete root mount point"}
	}

	// Notify progress
	callback(ProgressUpdate{
		Status:  "in_progress",
		Message: fmt.Sprintf("Preparing to delete mount %s", mountPoint),
	})

	// Sanitize the mount point for INI section ID
	id := sanitizeMountID(mountPoint)

	// Mark for deletion in mounts.pending.ini
	if err := markMountForDeletion(id); err != nil {
		e.logger.Error("failed to mark mount for deletion", "error", err)
		callback(ProgressUpdate{
			Status: "failed",
			Error:  fmt.Sprintf("Failed to mark mount for deletion: %v", err),
		})
		return ExecutionResult{
			Status:   StatusFailed,
			ErrorMsg: fmt.Sprintf("Failed to mark mount for deletion: %v", err),
		}
	}

	callback(ProgressUpdate{
		Status:  "pending",
		Message: fmt.Sprintf("Mount %s marked for deletion in %s, will be executed at boot", mountPoint, MountsPendingConfigFile),
	})

	return ExecutionResult{
		Status: StatusPending,
		Result: map[string]interface{}{
			"message": fmt.Sprintf("Mount %s marked for deletion, will be executed at boot", mountPoint),
		},
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
