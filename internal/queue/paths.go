package queue

import (
	"fmt"
	"os"
	"regexp"
	"time"

	"gopkg.in/ini.v1"
)

// Path configuration file paths
const (
	PathsPendingConfigFile = "/etc/zeropoint/paths.pending.ini"
	PathsConfigFile        = "/etc/zeropoint/paths.ini"
)

// EnqueueCreateMountPathRequest is the request to create a path within a mount
//
// swagger:model EnqueueCreateMountPathRequest
type EnqueueCreateMountPathRequest struct {
	Mount      string   `json:"mount"`       // Mount ID (FK to mounts)
	PathSuffix string   `json:"path_suffix"` // Subdirectory name (e.g., "media", "photos")
	DependsOn  []string `json:"depends_on,omitempty"`
}

// EnqueueDeleteMountPathRequest is the request to delete a path within a mount
//
// swagger:model EnqueueDeleteMountPathRequest
type EnqueueDeleteMountPathRequest struct {
	Mount      string   `json:"mount"`       // Mount ID (FK to mounts)
	PathSuffix string   `json:"path_suffix"` // Subdirectory name
	DependsOn  []string `json:"depends_on,omitempty"`
}

// PathInfo represents a path entry (active or pending)
//
// swagger:model PathInfo
type PathInfo struct {
	ID         string `json:"id"`    // Path ID (sanitized)
	Mount      string `json:"mount"` // Mount ID (FK)
	PathSuffix string `json:"path_suffix"`
	Status     string `json:"status"`                // "active" or "pending"
	MountPoint string `json:"mount_point,omitempty"` // Enriched: actual mount path
	FullPath   string `json:"full_path,omitempty"`   // Enriched: full path (mount_point + path_suffix)
}

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

// readPathsActivINI reads the /etc/zeropoint/paths.ini file and returns active paths by sanitized path ID
// Returns map of sanitized path ID -> path data, matching the disk/mount.ini format
func readPathsActivINI() (map[string]map[string]string, error) {
	configFile := PathsConfigFile

	// If file doesn't exist, no active paths yet
	if _, err := os.Stat(configFile); os.IsNotExist(err) {
		return make(map[string]map[string]string), nil
	}

	cfg, err := ini.Load(configFile)
	if err != nil {
		return nil, fmt.Errorf("failed to read paths.ini: %w", err)
	}

	paths := make(map[string]map[string]string)
	for _, section := range cfg.Sections() {
		if section.Name() == ini.DefaultSection {
			continue
		}

		pathData := make(map[string]string)
		for _, key := range section.Keys() {
			pathData[key.Name()] = key.Value()
		}
		paths[section.Name()] = pathData
	}

	return paths, nil
}

// readPathsPendingINI (mount-based) reads the /etc/zeropoint/paths.pending.ini file
func readPathsPendingINI() (map[string]map[string]string, error) {
	configFile := PathsPendingConfigFile

	// If file doesn't exist, no pending paths
	if _, err := os.Stat(configFile); os.IsNotExist(err) {
		return make(map[string]map[string]string), nil
	}

	cfg, err := ini.Load(configFile)
	if err != nil {
		return nil, fmt.Errorf("failed to read paths.pending.ini: %w", err)
	}

	paths := make(map[string]map[string]string)
	for _, section := range cfg.Sections() {
		if section.Name() == ini.DefaultSection {
			continue
		}

		pathData := make(map[string]string)
		for _, key := range section.Keys() {
			pathData[key.Name()] = key.Value()
		}
		paths[section.Name()] = pathData
	}

	return paths, nil
}

// writePathsPendingINI writes path creation/deletion spec to paths.pending.ini
func writePathsPendingINI(pathID string, args map[string]interface{}) error {
	configFile := PathsPendingConfigFile

	// Load existing file or create new
	cfg := ini.Empty()
	if _, err := os.Stat(configFile); err == nil {
		var errLoad error
		cfg, errLoad = ini.Load(configFile)
		if errLoad != nil {
			return fmt.Errorf("failed to load paths.pending.ini: %w", errLoad)
		}
	}

	// Create or update section
	section, err := cfg.NewSection(pathID)
	if err != nil {
		return fmt.Errorf("failed to create section: %w", err)
	}

	// Write mount (FK)
	mount, ok := args["mount"].(string)
	if !ok || mount == "" {
		return fmt.Errorf("mount is required")
	}
	section.Key("mount").SetValue(mount)

	// Write path_suffix
	pathSuffix, ok := args["path_suffix"].(string)
	if !ok || pathSuffix == "" {
		return fmt.Errorf("path_suffix is required")
	}
	section.Key("path_suffix").SetValue(pathSuffix)

	// Save file
	return cfg.SaveTo(configFile)
}

// markPathForDeletion marks a path for deletion in paths.pending.ini with [!pathID] section
func markPathForDeletion(pathID string) error {
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

	// Create deletion marker section [!pathID]
	sectionName := "!" + pathID
	section, err := cfg.NewSection(sectionName)
	if err != nil {
		section, _ = cfg.GetSection(sectionName)
	}

	// Empty section indicates deletion
	_ = section

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

// sanitizePathID converts a mount + path_suffix to a valid INI section ID
// Format: path_<mount>_<suffix> e.g., path_mnt_mnt_storage_media
func sanitizePathID(mount, pathSuffix string) string {
	result := "path_" + mount + "_" + pathSuffix
	for i, c := range result {
		if (c < 'a' || c > 'z') && (c < 'A' || c > 'Z') && (c < '0' || c > '9') && c != '_' {
			result = result[:i] + "_" + result[i+1:]
		}
	}
	return result
}

// ValidatePathSuffix ensures path_suffix doesn't contain directory traversal or absolute paths
func ValidatePathSuffix(pathSuffix string) error {
	if pathSuffix == "" {
		return fmt.Errorf("path_suffix cannot be empty")
	}

	if pathSuffix[0] == '/' {
		return fmt.Errorf("path_suffix cannot start with /")
	}

	if pathSuffix == ".." || pathSuffix == "." {
		return fmt.Errorf("path_suffix cannot be . or ..")
	}

	// Check for directory traversal patterns
	if matched, _ := regexp.MatchString(`(^|\/)\.\.(/|$)`, pathSuffix); matched {
		return fmt.Errorf("path_suffix cannot contain .. traversal")
	}

	// Only allow alphanumeric, underscores, hyphens, and forward slashes (for nested paths)
	if matched, _ := regexp.MatchString(`^[a-zA-Z0-9_\-/]+$`, pathSuffix); !matched {
		return fmt.Errorf("path_suffix contains invalid characters")
	}

	return nil
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
