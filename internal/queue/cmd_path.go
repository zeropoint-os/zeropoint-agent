package queue

import (
	"context"
	"fmt"
	"log/slog"
	"os"

	"gopkg.in/ini.v1"
)

// PathExecutor handles all path operations (edit, add, delete) for both system and user paths
type PathExecutor struct {
	cmd       Command
	logger    *slog.Logger
	operation string // "edit", "add", or "delete"
}

// Execute handles path operations, writing to paths.pending.ini with operation details
// Retryable: if the operation was already completed by the boot service, returns StatusCompleted
func (e *PathExecutor) Execute(ctx context.Context, callback ProgressCallback) ExecutionResult {
	pathID, ok := e.cmd.Args["path_id"].(string)
	if !ok || pathID == "" {
		return ExecutionResult{Status: StatusFailed, ErrorMsg: "path_id is required"}
	}

	// Check if this operation already completed in paths.ini
	existingResults, err := readPathResultsINI()
	if err == nil {
		for _, result := range existingResults {
			if result.PathID == pathID && result.Operation == e.operation {
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

	isSystemPath := isSystemPath(pathID)

	switch e.operation {
	case "edit":
		return e.executeEdit(pathID, isSystemPath, callback)
	case "add":
		return e.executeAdd(pathID, isSystemPath, callback)
	case "delete":
		return e.executeDelete(pathID, isSystemPath, callback)
	default:
		return ExecutionResult{Status: StatusFailed, ErrorMsg: fmt.Sprintf("unknown operation: %s", e.operation)}
	}
}

func (e *PathExecutor) executeEdit(pathID string, isSystemPath bool, callback ProgressCallback) ExecutionResult {
	newPath, ok := e.cmd.Args["new_path"].(string)
	if !ok || newPath == "" {
		return ExecutionResult{Status: StatusFailed, ErrorMsg: "new_path is required"}
	}

	oldPath, ok := e.cmd.Args["old_path"].(string)
	if !ok || oldPath == "" {
		return ExecutionResult{Status: StatusFailed, ErrorMsg: "old_path is required"}
	}

	callback(ProgressUpdate{
		Status:  "in_progress",
		Message: fmt.Sprintf("Preparing to edit path %s", pathID),
	})

	// Write to paths.pending.ini with all path details
	if err := writePathToPendingINI(pathID, "edit", oldPath, newPath, isSystemPath); err != nil {
		e.logger.Error("failed to write paths.pending.ini", "error", err)
		callback(ProgressUpdate{
			Status: "failed",
			Error:  fmt.Sprintf("Failed to write path configuration: %v", err),
		})
		return ExecutionResult{
			Status:   StatusFailed,
			ErrorMsg: fmt.Sprintf("Failed to write path configuration: %v", err),
		}
	}

	callback(ProgressUpdate{
		Status:  "pending",
		Message: fmt.Sprintf("Path edit staged in %s for boot-time processing", PathsPendingConfigFile),
	})

	return ExecutionResult{
		Status: StatusPending,
		Result: map[string]interface{}{
			"message": fmt.Sprintf("Path %s edit staged, will be executed at boot", pathID),
		},
	}
}

func (e *PathExecutor) executeAdd(pathID string, isSystemPath bool, callback ProgressCallback) ExecutionResult {
	name, ok := e.cmd.Args["name"].(string)
	if !ok || name == "" {
		return ExecutionResult{Status: StatusFailed, ErrorMsg: "name is required"}
	}

	path, ok := e.cmd.Args["path"].(string)
	if !ok || path == "" {
		return ExecutionResult{Status: StatusFailed, ErrorMsg: "path is required"}
	}

	mountID, ok := e.cmd.Args["mount_id"].(string)
	if !ok || mountID == "" {
		return ExecutionResult{Status: StatusFailed, ErrorMsg: "mount_id is required"}
	}

	description, ok := e.cmd.Args["description"].(string)
	if !ok {
		description = ""
	}

	callback(ProgressUpdate{
		Status:  "in_progress",
		Message: fmt.Sprintf("Adding path %s", pathID),
	})

	// Validate mount_id exists in mounts.ini
	if !isMountIDValid(mountID) {
		callback(ProgressUpdate{
			Status: "failed",
			Error:  fmt.Sprintf("Mount ID '%s' does not exist", mountID),
		})
		return ExecutionResult{
			Status:   StatusFailed,
			ErrorMsg: fmt.Sprintf("Mount ID '%s' does not exist", mountID),
		}
	}

	// Write to paths.pending.ini with operation details
	if err := writePathToPendingINIFull(pathID, "add", path, mountID, name, description, isSystemPath); err != nil {
		e.logger.Error("failed to write paths.pending.ini", "error", err)
		callback(ProgressUpdate{
			Status: "failed",
			Error:  fmt.Sprintf("Failed to write path configuration: %v", err),
		})
		return ExecutionResult{
			Status:   StatusFailed,
			ErrorMsg: fmt.Sprintf("Failed to write path configuration: %v", err),
		}
	}

	callback(ProgressUpdate{
		Status:  "pending",
		Message: fmt.Sprintf("Path %s staged in %s for boot-time execution", pathID, PathsPendingConfigFile),
	})

	return ExecutionResult{
		Status: StatusPending,
		Result: map[string]interface{}{
			"message": fmt.Sprintf("Path %s add staged, will be executed at boot", pathID),
		},
	}
}

func (e *PathExecutor) executeDelete(pathID string, isSystemPath bool, callback ProgressCallback) ExecutionResult {
	// Prevent deletion of system paths
	if isSystemPath {
		return ExecutionResult{
			Status:   StatusFailed,
			ErrorMsg: fmt.Sprintf("Cannot delete system path %s", pathID),
		}
	}

	callback(ProgressUpdate{
		Status:  "in_progress",
		Message: fmt.Sprintf("Deleting path %s", pathID),
	})

	// Write deletion marker to paths.pending.ini
	if err := writePathDeletionMarker(pathID); err != nil {
		e.logger.Error("failed to write deletion marker", "error", err)
		callback(ProgressUpdate{
			Status: "failed",
			Error:  fmt.Sprintf("Failed to delete path: %v", err),
		})
		return ExecutionResult{
			Status:   StatusFailed,
			ErrorMsg: fmt.Sprintf("Failed to delete path: %v", err),
		}
	}

	callback(ProgressUpdate{
		Status:  "pending",
		Message: fmt.Sprintf("Path deletion staged in %s for boot-time execution", PathsPendingConfigFile),
	})

	return ExecutionResult{
		Status: StatusPending,
		Result: map[string]interface{}{
			"message": fmt.Sprintf("Path %s delete staged, will be executed at boot", pathID),
		},
	}
}

// writePathToPendingINI writes an edit operation to paths.pending.ini
func writePathToPendingINI(pathID, operation, oldPath, newPath string, isSystemPath bool) error {
	configDir := "/etc/zeropoint"
	configFile := PathsPendingConfigFile

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
	section, err := cfg.NewSection(pathID)
	if err != nil {
		section, _ = cfg.GetSection(pathID)
	}

	// Store path operation configuration
	section.Key("operation").SetValue(operation)
	section.Key("old_path").SetValue(oldPath)
	section.Key("new_path").SetValue(newPath)
	section.Key("is_system_path").SetValue(fmt.Sprintf("%v", isSystemPath))

	// Write file
	if err := cfg.SaveTo(configFile); err != nil {
		return fmt.Errorf("failed to write paths.pending.ini: %w", err)
	}

	// Ensure proper file permissions
	if err := os.Chmod(configFile, 0600); err != nil {
		return fmt.Errorf("failed to set file permissions: %w", err)
	}

	return nil
}

// writePathToPendingINIFull writes a complete path entry to paths.pending.ini
func writePathToPendingINIFull(pathID, operation, path, mountID, name, description string, isSystemPath bool) error {
	configDir := "/etc/zeropoint"
	configFile := PathsPendingConfigFile

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
	section, err := cfg.NewSection(pathID)
	if err != nil {
		section, _ = cfg.GetSection(pathID)
	}

	// Store complete path configuration
	section.Key("operation").SetValue(operation)
	section.Key("path").SetValue(path)
	section.Key("mount_id").SetValue(mountID)
	section.Key("name").SetValue(name)
	section.Key("description").SetValue(description)
	section.Key("is_system_path").SetValue(fmt.Sprintf("%v", isSystemPath))

	// Write file
	if err := cfg.SaveTo(configFile); err != nil {
		return fmt.Errorf("failed to write paths.pending.ini: %w", err)
	}

	// Ensure proper file permissions
	if err := os.Chmod(configFile, 0600); err != nil {
		return fmt.Errorf("failed to set file permissions: %w", err)
	}

	return nil
}

// writePathDeletionMarker writes a deletion marker for a path
func writePathDeletionMarker(pathID string) error {
	configDir := "/etc/zeropoint"
	configFile := PathsPendingConfigFile

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
	section, err := cfg.NewSection(pathID)
	if err != nil {
		section, _ = cfg.GetSection(pathID)
	}

	// Store deletion marker
	section.Key("operation").SetValue("delete")
	section.Key("path").SetValue("") // Empty path indicates deletion
	section.Key("is_system_path").SetValue("false")

	// Write file
	if err := cfg.SaveTo(configFile); err != nil {
		return fmt.Errorf("failed to write paths.pending.ini: %w", err)
	}

	// Ensure proper file permissions
	if err := os.Chmod(configFile, 0600); err != nil {
		return fmt.Errorf("failed to set file permissions: %w", err)
	}

	return nil
}

// isPathUnderMount checks if a path is under a given mount point
func isPathUnderMount(path, mountPoint string) bool {
	// Ensure both end with / for comparison
	if mountPoint != "/" && mountPoint[len(mountPoint)-1] != '/' {
		mountPoint = mountPoint + "/"
	}
	if path[len(path)-1] != '/' {
		path = path + "/"
	}

	return len(path) >= len(mountPoint) && path[:len(mountPoint)] == mountPoint
}

// isSystemPath checks if a path ID is a system path (zp_ prefix)
func isSystemPath(pathID string) bool {
	return len(pathID) >= 3 && pathID[:3] == "zp_"
}

// isMountIDValid checks if a mount ID exists in the mounts system
func isMountIDValid(mountID string) bool {
	configFile := "/etc/zeropoint/mounts.ini"

	// If file doesn't exist, no mounts are configured
	if _, err := os.Stat(configFile); os.IsNotExist(err) {
		return false
	}

	cfg, err := ini.Load(configFile)
	if err != nil {
		return false
	}

	_, err = cfg.GetSection(mountID)
	return err == nil
}
