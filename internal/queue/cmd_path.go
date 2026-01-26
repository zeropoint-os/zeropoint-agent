package queue

import (
	"context"
	"fmt"
	"log/slog"
	"os"

	"gopkg.in/ini.v1"
)

// EditSystemPathExecutor executes system path edit commands
type EditSystemPathExecutor struct {
	cmd    Command
	logger *slog.Logger
}

// Execute stages a system path edit in paths.pending.ini
func (e *EditSystemPathExecutor) Execute(ctx context.Context, callback ProgressCallback) ExecutionResult {
	pathID, ok := e.cmd.Args["path_id"].(string)
	if !ok || pathID == "" {
		return ExecutionResult{Status: StatusFailed, ErrorMsg: "path_id is required"}
	}

	newPath, ok := e.cmd.Args["new_path"].(string)
	if !ok || newPath == "" {
		return ExecutionResult{Status: StatusFailed, ErrorMsg: "new_path is required"}
	}

	oldPath, ok := e.cmd.Args["old_path"].(string)
	if !ok || oldPath == "" {
		return ExecutionResult{Status: StatusFailed, ErrorMsg: "old_path is required"}
	}

	// Notify progress
	callback(ProgressUpdate{
		Status:  "in_progress",
		Message: fmt.Sprintf("Preparing to edit system path %s", pathID),
	})

	// Write to paths.pending.ini
	if err := writePathPendingINI(pathID, "edit", oldPath, newPath); err != nil {
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
		Message: fmt.Sprintf("Path edit staged in %s for boot-time execution", PathsPendingConfigFile),
	})

	return ExecutionResult{
		Status: StatusPending,
		Result: map[string]interface{}{
			"message":  fmt.Sprintf("Path %s edit staged, migration will occur at boot", pathID),
			"old_path": oldPath,
			"new_path": newPath,
		},
	}
}

// AddUserPathExecutor executes user path addition commands
type AddUserPathExecutor struct {
	cmd    Command
	logger *slog.Logger
}

// Execute adds a user path (no pending needed - immediate)
func (e *AddUserPathExecutor) Execute(ctx context.Context, callback ProgressCallback) ExecutionResult {
	pathID, ok := e.cmd.Args["path_id"].(string)
	if !ok || pathID == "" {
		return ExecutionResult{Status: StatusFailed, ErrorMsg: "path_id is required"}
	}

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

	// Notify progress
	callback(ProgressUpdate{
		Status:  "in_progress",
		Message: fmt.Sprintf("Adding user path %s", pathID),
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

	// Write to paths data (handled by API layer, executor just validates)
	// This is immediate, no pending
	callback(ProgressUpdate{
		Status:  "completed",
		Message: fmt.Sprintf("User path %s added successfully", pathID),
	})

	return ExecutionResult{
		Status: StatusCompleted,
		Result: map[string]interface{}{
			"message": fmt.Sprintf("User path %s added", pathID),
			"path_id": pathID,
			"path":    path,
		},
	}
}

// DeleteUserPathExecutor executes user path deletion commands
type DeleteUserPathExecutor struct {
	cmd    Command
	logger *slog.Logger
}

// Execute deletes a user path (no pending needed - immediate)
func (e *DeleteUserPathExecutor) Execute(ctx context.Context, callback ProgressCallback) ExecutionResult {
	pathID, ok := e.cmd.Args["path_id"].(string)
	if !ok || pathID == "" {
		return ExecutionResult{Status: StatusFailed, ErrorMsg: "path_id is required"}
	}

	// Prevent deletion of system paths
	if isSystemPath(pathID) {
		return ExecutionResult{
			Status:   StatusFailed,
			ErrorMsg: fmt.Sprintf("Cannot delete system path %s", pathID),
		}
	}

	// Notify progress
	callback(ProgressUpdate{
		Status:  "in_progress",
		Message: fmt.Sprintf("Deleting user path %s", pathID),
	})

	// Deletion is immediate (handled by API layer)
	callback(ProgressUpdate{
		Status:  "completed",
		Message: fmt.Sprintf("User path %s deleted successfully", pathID),
	})

	return ExecutionResult{
		Status: StatusCompleted,
		Result: map[string]interface{}{
			"message": fmt.Sprintf("User path %s deleted", pathID),
			"path_id": pathID,
		},
	}
}

// writePathPendingINI writes a path operation to /etc/zeropoint/paths.pending.ini
func writePathPendingINI(pathID, operation, oldPath, newPath string) error {
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
