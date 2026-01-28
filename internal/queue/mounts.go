package queue

import (
	"fmt"
	"os"
	"time"

	"gopkg.in/ini.v1"
)

// Mount configuration file paths
const (
	MountsPendingConfigFile = "/etc/zeropoint/mounts.pending.ini"
	MountsConfigFile        = "/etc/zeropoint/mounts.ini"
)

// EnqueueCreateMountRequest is the request to create/update a mount
//
// swagger:model EnqueueCreateMountRequest
type EnqueueCreateMountRequest struct {
	MountPoint string   `json:"mount_point"` // Where filesystem is mounted
	Disk       string   `json:"disk"`        // Stable disk ID (from disk layer)
	Partition  int      `json:"partition"`   // Partition number (0, 1, 2, etc.)
	DependsOn  []string `json:"depends_on,omitempty"`
}

// EnqueueDeleteMountRequest is the request to delete a mount
//
// swagger:model EnqueueDeleteMountRequest
type EnqueueDeleteMountRequest struct {
	MountPoint string   `json:"mount_point"` // Mount point to delete
	DependsOn  []string `json:"depends_on,omitempty"`
}

// MountOperationResult represents a completed mount operation from mounts.ini
type MountOperationResult struct {
	MountPoint string
	Status     string // "success" or "error"
	Message    string
	RequestID  string
	Timestamp  string
}

// readMountResultsINI reads the /etc/zeropoint/mounts.ini file and returns completed operations
// Returns slice of MountOperationResult for each mount in the file
func readMountResultsINI() ([]MountOperationResult, error) {
	configFile := MountsConfigFile

	// If file doesn't exist, no completed operations yet
	if _, err := os.Stat(configFile); os.IsNotExist(err) {
		return []MountOperationResult{}, nil
	}

	cfg, err := ini.Load(configFile)
	if err != nil {
		return nil, fmt.Errorf("failed to read mounts.ini: %w", err)
	}

	var results []MountOperationResult
	for _, section := range cfg.Sections() {
		if section.Name() == ini.DefaultSection {
			continue
		}

		result := MountOperationResult{
			MountPoint: section.Name(),
			Status:     section.Key("status").String(),
			Message:    section.Key("message").String(),
			RequestID:  section.Key("request_id").String(),
			Timestamp:  section.Key("timestamp").String(),
		}
		results = append(results, result)
	}

	return results, nil
}

// readMountPendingINI reads the /etc/zeropoint/mounts.pending.ini file and returns pending operations
func readMountPendingINI() (map[string]string, error) {
	configFile := MountsPendingConfigFile

	// If file doesn't exist, no pending operations
	if _, err := os.Stat(configFile); os.IsNotExist(err) {
		return make(map[string]string), nil
	}

	cfg, err := ini.Load(configFile)
	if err != nil {
		return nil, fmt.Errorf("failed to read mounts.pending.ini: %w", err)
	}

	pending := make(map[string]string)
	for _, section := range cfg.Sections() {
		if section.Name() == ini.DefaultSection {
			continue
		}
		requestID := section.Key("request_id").String()
		if requestID != "" {
			pending[requestID] = section.Name() // map requestID -> mountPoint
		}
	}

	return pending, nil
}

// ProcessMountsResults processes completed mount operations and updates job status
// This is called during agent startup to mark boot-time mount jobs as complete/error
// Also logs any operations that are still pending (haven't executed yet)
func (h *Handlers) ProcessMountsResults() error {
	// Get completed results
	results, err := readMountResultsINI()
	if err != nil {
		h.logger.Error("failed to read mount results", "error", err)
		return err
	}

	completedRequestIDs := make(map[string]bool)

	for _, result := range results {
		if result.RequestID == "" {
			continue
		}

		// Validate that the job actually exists before processing
		// This prevents errors if the boot service puts a placeholder like <JOB_ID>
		_, err := h.manager.Get(result.RequestID)
		if err != nil {
			h.logger.Warn("skipping mount result with non-existent job", "request_id", result.RequestID, "mount_point", result.MountPoint)
			continue
		}

		completedRequestIDs[result.RequestID] = true
		h.logger.Info("processing mount result", "mount_point", result.MountPoint, "request_id", result.RequestID, "status", result.Status)

		// Add completion event to job
		eventType := "final"
		if result.Status == "error" {
			eventType = "error"
		}

		event := Event{
			Timestamp: time.Now().UTC(),
			Type:      eventType,
			Message:   fmt.Sprintf("Boot-time mount execution: %s", result.Message),
		}

		if err := h.manager.AppendEvent(result.RequestID, event); err != nil {
			h.logger.Error("failed to append completion event", "job_id", result.RequestID, "error", err)
		}

		// Mark job as complete or error using UpdateStatus
		completionTime := time.Now().UTC()
		jobResult := map[string]interface{}{
			"mount_point": result.MountPoint,
			"status":      "completed_at_boot",
			"message":     result.Message,
		}

		if result.Status == "success" {
			if err := h.manager.UpdateStatus(result.RequestID, StatusCompleted, nil, &completionTime, jobResult, ""); err != nil {
				h.logger.Error("failed to mark job complete", "job_id", result.RequestID, "error", err)
			}
		} else if result.Status == "error" {
			if err := h.manager.UpdateStatus(result.RequestID, StatusFailed, nil, &completionTime, jobResult, result.Message); err != nil {
				h.logger.Error("failed to mark job error", "job_id", result.RequestID, "error", err)
			}
		}
	}

	// Log any operations that are still pending (in mounts.pending.ini but not in mounts.ini yet)
	pending, err := readMountPendingINI()
	if err != nil {
		h.logger.Warn("failed to read pending mount operations", "error", err)
	} else if len(pending) > 0 {
		for requestID, mountPoint := range pending {
			if !completedRequestIDs[requestID] {
				h.logger.Info("mount operation still pending (will execute on next reboot)",
					"request_id", requestID, "mount_point", mountPoint)
			}
		}
	}

	if len(results) == 0 && len(pending) == 0 {
		h.logger.Debug("no boot-time mount operations to process")
	}

	return nil
}
