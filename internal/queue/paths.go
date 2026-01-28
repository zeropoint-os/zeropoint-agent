package queue

import (
	"fmt"
	"os"
	"time"

	"gopkg.in/ini.v1"
)

// Path configuration file paths
const (
	PathsPendingConfigFile = "/etc/zeropoint/paths.pending.ini"
	PathsConfigFile        = "/etc/zeropoint/paths.ini"
)

// PathOperationResult represents a completed path operation from paths.ini
type PathOperationResult struct {
	PathID    string
	Operation string // "edit", "add", "delete"
	Status    string // "success" or "error"
	Message   string
	RequestID string
	Timestamp string
	OldPath   string // For edit operations
	NewPath   string // For edit operations
}

// readPathResultsINI reads the /etc/zeropoint/paths.ini file and returns completed operations
// Returns slice of PathOperationResult for each path in the file
func readPathResultsINI() ([]PathOperationResult, error) {
	configFile := PathsConfigFile

	// If file doesn't exist, no completed operations yet
	if _, err := os.Stat(configFile); os.IsNotExist(err) {
		return []PathOperationResult{}, nil
	}

	cfg, err := ini.Load(configFile)
	if err != nil {
		return nil, fmt.Errorf("failed to read paths.ini: %w", err)
	}

	var results []PathOperationResult
	for _, section := range cfg.Sections() {
		if section.Name() == ini.DefaultSection {
			continue
		}

		result := PathOperationResult{
			PathID:    section.Name(),
			Operation: section.Key("operation").String(),
			Status:    section.Key("status").String(),
			Message:   section.Key("message").String(),
			RequestID: section.Key("request_id").String(),
			Timestamp: section.Key("timestamp").String(),
			OldPath:   section.Key("old_path").String(),
			NewPath:   section.Key("new_path").String(),
		}
		results = append(results, result)
	}

	return results, nil
}

// readPathPendingINI reads the /etc/zeropoint/paths.pending.ini file and returns pending operations
func readPathPendingINI() (map[string]string, error) {
	configFile := PathsPendingConfigFile

	// If file doesn't exist, no pending operations
	if _, err := os.Stat(configFile); os.IsNotExist(err) {
		return make(map[string]string), nil
	}

	cfg, err := ini.Load(configFile)
	if err != nil {
		return nil, fmt.Errorf("failed to read paths.pending.ini: %w", err)
	}

	pending := make(map[string]string)
	for _, section := range cfg.Sections() {
		if section.Name() == ini.DefaultSection {
			continue
		}
		requestID := section.Key("request_id").String()
		if requestID != "" {
			pending[requestID] = section.Name() // map requestID -> pathID
		}
	}

	return pending, nil
}

// ProcessPathsResults processes completed path operations and updates job status
// This is called during agent startup to mark boot-time path jobs as complete/error
// Also logs any operations that are still pending (haven't executed yet)
func (h *Handlers) ProcessPathsResults() error {
	// Get completed results
	results, err := readPathResultsINI()
	if err != nil {
		h.logger.Error("failed to read path results", "error", err)
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
			h.logger.Warn("skipping path result with non-existent job", "request_id", result.RequestID, "path_id", result.PathID)
			continue
		}

		completedRequestIDs[result.RequestID] = true
		h.logger.Info("processing path result", "path_id", result.PathID, "request_id", result.RequestID, "operation", result.Operation, "status", result.Status)

		// Add completion event to job
		eventType := "final"
		if result.Status == "error" {
			eventType = "error"
		}

		event := Event{
			Timestamp: time.Now().UTC(),
			Type:      eventType,
			Message:   fmt.Sprintf("Boot-time path operation: %s", result.Message),
		}

		if err := h.manager.AppendEvent(result.RequestID, event); err != nil {
			h.logger.Error("failed to append completion event", "job_id", result.RequestID, "error", err)
		}

		// Mark job as complete or error using UpdateStatus
		completionTime := time.Now().UTC()
		jobResult := map[string]interface{}{
			"path_id":   result.PathID,
			"operation": result.Operation,
			"status":    "completed_at_boot",
			"message":   result.Message,
		}

		if result.Operation == "edit" {
			jobResult["old_path"] = result.OldPath
			jobResult["new_path"] = result.NewPath
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

	// Log any operations that are still pending (in paths.pending.ini but not in paths.ini yet)
	pending, err := readPathPendingINI()
	if err != nil {
		h.logger.Warn("failed to read pending path operations", "error", err)
	} else if len(pending) > 0 {
		for requestID, pathID := range pending {
			if !completedRequestIDs[requestID] {
				h.logger.Info("path operation still pending (will execute on next reboot)",
					"request_id", requestID, "path_id", pathID)
			}
		}
	}

	if len(results) == 0 && len(pending) == 0 {
		h.logger.Debug("no boot-time path operations to process")
	}

	return nil
}
