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

// readMountResultsINI reads the /etc/zeropoint/mounts.ini file and returns active mounts by sanitized mount ID
// Returns map of sanitized mount ID -> mount data, matching the disk.ini format
func readMountResultsINI() (map[string]map[string]string, error) {
	configFile := MountsConfigFile

	// If file doesn't exist, no active mounts yet
	if _, err := os.Stat(configFile); os.IsNotExist(err) {
		return make(map[string]map[string]string), nil
	}

	cfg, err := ini.Load(configFile)
	if err != nil {
		return nil, fmt.Errorf("failed to read mounts.ini: %w", err)
	}

	mounts := make(map[string]map[string]string)
	for _, section := range cfg.Sections() {
		if section.Name() == ini.DefaultSection {
			continue
		}

		mountData := make(map[string]string)
		for _, key := range section.Keys() {
			mountData[key.Name()] = key.Value()
		}
		mounts[section.Name()] = mountData
	}

	return mounts, nil
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

	for _, mountData := range results {
		requestID := mountData["request_id"]
		if requestID == "" {
			continue
		}

		// Validate that the job actually exists before processing
		// This prevents errors if the boot service puts a placeholder like <JOB_ID>
		_, err := h.manager.Get(requestID)
		if err != nil {
			mountPoint := mountData["mount_point"]
			h.logger.Warn("skipping mount result with non-existent job", "request_id", requestID, "mount_point", mountPoint)
			continue
		}

		completedRequestIDs[requestID] = true
		mountPoint := mountData["mount_point"]
		status := mountData["status"]
		h.logger.Info("processing mount result", "mount_point", mountPoint, "request_id", requestID, "status", status)

		// Add completion event to job
		eventType := "final"
		if status == "error" {
			eventType = "error"
		}

		message := mountData["message"]
		event := Event{
			Timestamp: time.Now().UTC(),
			Type:      eventType,
			Message:   fmt.Sprintf("Boot-time mount execution: %s", message),
		}

		if err := h.manager.AppendEvent(requestID, event); err != nil {
			h.logger.Error("failed to append completion event", "job_id", requestID, "error", err)
		}

		// Mark job as complete or error using UpdateStatus
		completionTime := time.Now().UTC()
		jobResult := map[string]interface{}{
			"mount_point": mountPoint,
			"status":      "completed_at_boot",
			"message":     message,
		}

		if status == "success" || status == "" {
			// Empty status means it was moved to mounts.ini without explicit status (already active)
			if err := h.manager.UpdateStatus(requestID, StatusCompleted, nil, &completionTime, jobResult, ""); err != nil {
				h.logger.Error("failed to mark job complete", "job_id", requestID, "error", err)
			}
		} else if status == "error" {
			if err := h.manager.UpdateStatus(requestID, StatusFailed, nil, &completionTime, jobResult, message); err != nil {
				h.logger.Error("failed to mark job error", "job_id", requestID, "error", err)
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
