package queue

import (
	"context"
	"fmt"
	"log/slog"
	"os"

	"gopkg.in/ini.v1"
)

// DiskExecutor handles both manage and release disk operations
type DiskExecutor struct {
	cmd       Command
	logger    *slog.Logger
	operation string // "manage" or "release"
}

// Execute stages the disk management operation for boot-time execution
// Retryable: if the operation was already completed by the boot service, returns StatusCompleted
func (e *DiskExecutor) Execute(ctx context.Context, callback ProgressCallback) ExecutionResult {
	diskID, ok := e.cmd.Args["id"].(string)
	if !ok || diskID == "" {
		return ExecutionResult{Status: StatusFailed, ErrorMsg: "disk id is required"}
	}

	// Check if this operation already completed in disks.ini
	existingResults, err := readStorageResultsINI()
	if err == nil {
		for _, result := range existingResults {
			if result.DiskID == diskID {
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
	case "manage":
		return e.executeManage(diskID, callback)
	case "release":
		return e.executeRelease(diskID, callback)
	default:
		return ExecutionResult{Status: StatusFailed, ErrorMsg: "unknown disk operation: " + e.operation}
	}
}

// executeManage writes disk management spec to disks.pending.ini
func (e *DiskExecutor) executeManage(diskID string, callback ProgressCallback) ExecutionResult {
	callback(ProgressUpdate{
		Status:  "in_progress",
		Message: fmt.Sprintf("Preparing to manage disk %s", diskID),
	})

	if err := writeStoragePendingINI(diskID, e.cmd.Args); err != nil {
		e.logger.Error("failed to write disks.pending.ini", "error", err)
		callback(ProgressUpdate{
			Status: "failed",
			Error:  fmt.Sprintf("Failed to stage disk management: %v", err),
		})
		return ExecutionResult{
			Status:   StatusFailed,
			ErrorMsg: fmt.Sprintf("Failed to stage disk management: %v", err),
		}
	}

	callback(ProgressUpdate{
		Status:  "pending",
		Message: fmt.Sprintf("Disk %s staged in %s for boot-time management", diskID, DisksPendingConfigFile),
	})

	return ExecutionResult{
		Status: StatusPending,
		Result: map[string]interface{}{
			"message": fmt.Sprintf("Disk %s management staged, will be executed at boot", diskID),
		},
	}
}

// executeRelease writes deletion marker to disks.pending.ini
func (e *DiskExecutor) executeRelease(diskID string, callback ProgressCallback) ExecutionResult {
	callback(ProgressUpdate{
		Status:  "in_progress",
		Message: fmt.Sprintf("Preparing to release disk %s", diskID),
	})

	if err := writeStorageDeletionMarker(diskID); err != nil {
		e.logger.Error("failed to mark disk for release", "error", err)
		callback(ProgressUpdate{
			Status: "failed",
			Error:  fmt.Sprintf("Failed to mark disk for release: %v", err),
		})
		return ExecutionResult{
			Status:   StatusFailed,
			ErrorMsg: fmt.Sprintf("Failed to mark disk for release: %v", err),
		}
	}

	callback(ProgressUpdate{
		Status:  "pending",
		Message: fmt.Sprintf("Disk %s marked for release in %s, will be executed at boot", diskID, DisksPendingConfigFile),
	})

	return ExecutionResult{
		Status: StatusPending,
		Result: map[string]interface{}{
			"message": fmt.Sprintf("Disk %s marked for release, will be executed at boot", diskID),
		},
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
