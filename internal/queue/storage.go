package queue

import (
	"encoding/json"
	"fmt"
	"os"
	"time"

	"gopkg.in/ini.v1"
)

// Storage configuration file paths
const (
	DisksPendingConfigFile = "/etc/zeropoint/disks.pending.ini"
	DisksConfigFile        = "/etc/zeropoint/disks.ini"
)

// EnqueueFormatRequest is the request to format a disk immediately (streams events)
//
// swagger:model EnqueueFormatRequest
type EnqueueFormatRequest struct {
	ID                        string                 `json:"id"` // Stable device identifier (e.g., usb-SanDisk_Cruzer_4C8F9A1B)
	DependsOn                 []string               `json:"depends_on,omitempty"`
	PartitionLayout           string                 `json:"partition_layout,omitempty"` // was reserved for future explicit layouts; not implemented currently.
	Filesystem                string                 `json:"filesystem" example:"ext4"`
	Label                     string                 `json:"label,omitempty"`
	Wipefs                    bool                   `json:"wipefs"`
	Luks                      map[string]interface{} `json:"luks,omitempty"`
	Lvm                       map[string]interface{} `json:"lvm,omitempty"`
	Confirm                   bool                   `json:"confirm"`
	ConfirmFixedDiskOperation bool                   `json:"confirm_fixed_disk_operation,omitempty"`
	AutoPartition             bool                   `json:"auto_partition,omitempty"`
}

// StorageOperationResult represents a completed format operation from disks.ini
type StorageOperationResult struct {
	DiskID    string
	Status    string // "success" or "error"
	Message   string
	RequestID string
	Timestamp string
}

// writeStoragePendingINI writes a format operation to /etc/zeropoint/disks.pending.ini
// The file format is INI with [id] sections containing all format configuration parameters
// Uses stable device id (e.g., usb-SanDisk_Cruzer_...) as section key since it survives reboots
func writeStoragePendingINI(req *EnqueueFormatRequest, jobID string) error {
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
			// If we can't load, start fresh (this overwrites any corrupt file)
			cfg = ini.Empty()
		}
	}

	// Create or update section for this disk (use stable id as section name)
	section, err := cfg.NewSection(req.ID)
	if err != nil {
		// Section might already exist, get it instead
		section, _ = cfg.GetSection(req.ID)
	}

	// Store all configuration parameters
	section.Key("filesystem").SetValue(req.Filesystem)
	if req.Label != "" {
		section.Key("label").SetValue(req.Label)
	}
	if req.Wipefs {
		section.Key("wipefs").SetValue("true")
	}
	if req.AutoPartition {
		section.Key("auto_partition").SetValue("true")
	}
	if req.ConfirmFixedDiskOperation {
		section.Key("confirm_fixed_disk_operation").SetValue("true")
	}
	if len(req.Luks) > 0 {
		luksJSON, _ := json.Marshal(req.Luks)
		section.Key("luks").SetValue(string(luksJSON))
	}
	if len(req.Lvm) > 0 {
		lvmJSON, _ := json.Marshal(req.Lvm)
		section.Key("lvm").SetValue(string(lvmJSON))
	}
	section.Key("request_id").SetValue(jobID)
	section.Key("timestamp").SetValue(time.Now().UTC().Format(time.RFC3339))

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

// readStorageResultsINI reads the /etc/zeropoint/disks.ini file and returns completed operations
// Returns slice of StorageOperationResult for each disk in the file
func readStorageResultsINI() ([]StorageOperationResult, error) {
	configFile := DisksConfigFile

	// If file doesn't exist, no completed operations yet
	if _, err := os.Stat(configFile); os.IsNotExist(err) {
		return []StorageOperationResult{}, nil
	}

	cfg, err := ini.Load(configFile)
	if err != nil {
		return nil, fmt.Errorf("failed to read disks.ini: %w", err)
	}

	var results []StorageOperationResult
	for _, section := range cfg.Sections() {
		if section.Name() == ini.DefaultSection {
			continue
		}

		result := StorageOperationResult{
			DiskID:    section.Name(),
			Status:    section.Key("status").String(),
			Message:   section.Key("message").String(),
			RequestID: section.Key("request_id").String(),
			Timestamp: section.Key("timestamp").String(),
		}
		results = append(results, result)
	}

	return results, nil
}

// readStoragePendingINI reads the /etc/zeropoint/disks.pending.ini file and returns pending operations
func readStoragePendingINI() (map[string]string, error) {
	configFile := DisksPendingConfigFile

	// If file doesn't exist, no pending operations
	if _, err := os.Stat(configFile); os.IsNotExist(err) {
		return make(map[string]string), nil
	}

	cfg, err := ini.Load(configFile)
	if err != nil {
		return nil, fmt.Errorf("failed to read disks.pending.ini: %w", err)
	}

	pending := make(map[string]string)
	for _, section := range cfg.Sections() {
		if section.Name() == ini.DefaultSection {
			continue
		}
		requestID := section.Key("request_id").String()
		if requestID != "" {
			pending[requestID] = section.Name() // map requestID -> diskID
		}
	}

	return pending, nil
}

// ProcessStorageResults processes completed format operations and updates job status
// This is called during agent startup to mark boot-time format jobs as complete/error
// Also logs any operations that are still pending (haven't executed yet)
func (h *Handlers) ProcessStorageResults() error {
	// Get completed results
	results, err := readStorageResultsINI()
	if err != nil {
		h.logger.Error("failed to read storage results", "error", err)
		return err
	}

	completedRequestIDs := make(map[string]bool)

	for _, result := range results {
		if result.RequestID == "" {
			continue
		}

		completedRequestIDs[result.RequestID] = true
		h.logger.Info("processing storage result", "disk_id", result.DiskID, "request_id", result.RequestID, "status", result.Status)

		// Add completion event to job
		eventType := "final"
		if result.Status == "error" {
			eventType = "error"
		}

		event := Event{
			Timestamp: time.Now().UTC(),
			Type:      eventType,
			Message:   fmt.Sprintf("Boot-time format execution: %s", result.Message),
		}

		if err := h.manager.AppendEvent(result.RequestID, event); err != nil {
			h.logger.Error("failed to append completion event", "job_id", result.RequestID, "error", err)
		}

		// Mark job as complete or error using UpdateStatus
		completionTime := time.Now().UTC()
		jobResult := map[string]interface{}{
			"disk_id": result.DiskID,
			"status":  "completed_at_boot",
			"message": result.Message,
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

	// Log any operations that are still pending (in disks.pending.ini but not in disks.ini yet)
	pending, err := readStoragePendingINI()
	if err != nil {
		h.logger.Warn("failed to read pending storage operations", "error", err)
	} else if len(pending) > 0 {
		for requestID, diskID := range pending {
			if !completedRequestIDs[requestID] {
				h.logger.Info("format operation still pending (will execute on next reboot)",
					"request_id", requestID, "disk_id", diskID)
			}
		}
	}

	if len(results) == 0 && len(pending) == 0 {
		h.logger.Debug("no boot-time format operations to process")
	}

	return nil
}
