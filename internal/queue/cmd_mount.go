package queue

import (
	"context"
	"fmt"
	"log/slog"
	"os"

	"gopkg.in/ini.v1"
)

// CreateMountExecutor executes mount creation commands
type CreateMountExecutor struct {
	cmd    Command
	logger *slog.Logger
}

// Execute creates a mount entry in mounts.pending.ini
func (e *CreateMountExecutor) Execute(ctx context.Context, callback ProgressCallback) ExecutionResult {
	mountPoint, ok := e.cmd.Args["mount_point"].(string)
	if !ok || mountPoint == "" {
		return ExecutionResult{Status: StatusFailed, ErrorMsg: "mount_point is required"}
	}

	filesystem, ok := e.cmd.Args["filesystem"].(string)
	if !ok || filesystem == "" {
		return ExecutionResult{Status: StatusFailed, ErrorMsg: "filesystem is required"}
	}

	fsType, ok := e.cmd.Args["type"].(string)
	if !ok || fsType == "" {
		return ExecutionResult{Status: StatusFailed, ErrorMsg: "type is required"}
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
	if err := writeMountPendingINI(id, mountPoint, filesystem, fsType); err != nil {
		e.logger.Error("failed to write mounts.pending.ini", "error", err)
		callback(ProgressUpdate{
			Status: "failed",
			Error:  fmt.Sprintf("Failed to write mount configuration: %v", err),
		})
		return ExecutionResult{
			Status:   StatusFailed,
			ErrorMsg: fmt.Sprintf("Failed to write mount configuration: %v", err),
		}
	}

	callback(ProgressUpdate{
		Status:  "completed",
		Message: fmt.Sprintf("Mount %s staged in %s for boot-time execution", mountPoint, MountsPendingConfigFile),
	})

	return ExecutionResult{
		Status: StatusCompleted,
		Result: map[string]interface{}{
			"message": fmt.Sprintf("Mount %s created in pending file, will be executed at boot", mountPoint),
		},
	}
}

// DeleteMountExecutor executes mount deletion commands
type DeleteMountExecutor struct {
	cmd    Command
	logger *slog.Logger
}

// Execute marks a mount for deletion in mounts.pending.ini
func (e *DeleteMountExecutor) Execute(ctx context.Context, callback ProgressCallback) ExecutionResult {
	mountPoint, ok := e.cmd.Args["mount_point"].(string)
	if !ok || mountPoint == "" {
		return ExecutionResult{Status: StatusFailed, ErrorMsg: "mount_point is required"}
	}

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
		Status:  "completed",
		Message: fmt.Sprintf("Mount %s marked for deletion in %s, will be executed at boot", mountPoint, MountsPendingConfigFile),
	})

	return ExecutionResult{
		Status: StatusCompleted,
		Result: map[string]interface{}{
			"message": fmt.Sprintf("Mount %s marked for deletion, will be executed at boot", mountPoint),
		},
	}
}

// writeMountPendingINI writes a mount entry to /etc/zeropoint/mounts.pending.ini
func writeMountPendingINI(id, mountPoint, filesystem, fsType string) error {
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

	// Store mount configuration (essential fields only)
	section.Key("mount_point").SetValue(mountPoint)
	section.Key("filesystem").SetValue(filesystem)
	section.Key("type").SetValue(fsType)

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

	// Get or create section
	section, err := cfg.NewSection(id)
	if err != nil {
		section, _ = cfg.GetSection(id)
	}

	// Mark for deletion
	section.Key("action").SetValue("delete")

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

// sanitizeMountID converts a mount_point to a safe section ID for ini file
func sanitizeMountID(mountPoint string) string {
	var result string
	for _, ch := range mountPoint {
		if (ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z') || (ch >= '0' && ch <= '9') {
			result += string(ch)
		} else if ch == '/' || ch == '-' || ch == '_' {
			result += "_"
		}
	}
	// Remove leading/trailing underscores
	for len(result) > 0 && result[0] == '_' {
		result = result[1:]
	}
	for len(result) > 0 && result[len(result)-1] == '_' {
		result = result[:len(result)-1]
	}
	if result == "" {
		result = "mount"
	}
	return result
}
