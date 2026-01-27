package queue

import (
	"encoding/json"
	"fmt"
	"os"

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

// EnqueueManageDiskRequest is the request to add a disk to the managed pool
//
// swagger:model EnqueueManageDiskRequest
type EnqueueManageDiskRequest struct {
	ID            string                 `json:"id"` // Stable device identifier (e.g., usb-SanDisk_Cruzer_4C8F9A1B)
	DependsOn     []string               `json:"depends_on,omitempty"`
	Filesystem    string                 `json:"filesystem" example:"ext4"`
	Label         string                 `json:"label,omitempty"`
	Wipefs        bool                   `json:"wipefs"`
	Luks          map[string]interface{} `json:"luks,omitempty"`
	Lvm           map[string]interface{} `json:"lvm,omitempty"`
	Confirm       bool                   `json:"confirm"`
	AutoPartition bool                   `json:"auto_partition,omitempty"`
}

// EnqueueReleaseDiskRequest is the request to remove a disk from the managed pool
//
// swagger:model EnqueueReleaseDiskRequest
type EnqueueReleaseDiskRequest struct {
	ID       string   `json:"id"` // Stable device identifier to release
	DependsOn []string `json:"depends_on,omitempty"`
}

// StorageOperationResult represents a completed format operation from disks.ini
type StorageOperationResult struct {
	DiskID  string
	Status  string // "success" or "error"
	Message string
}

// NOTE: writeStoragePendingINI and writeStorageDeletionMarker are now in cmd_disk.go
// They handle flexible disk management operations (manage/release) instead of just format

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
			DiskID:  section.Name(),
			Status:  section.Key("status").String(),
			Message: section.Key("message").String(),
		}
		results = append(results, result)
	}

	return results, nil
}

// readDisksINI reads the /etc/zeropoint/disks.ini file and returns managed disk entries
// Returns slice of disk IDs and their metadata for disks we care about (managed resources)
func readDisksINI() (map[string]map[string]string, error) {
	configFile := DisksConfigFile

	// If file doesn't exist, no managed disks yet
	if _, err := os.Stat(configFile); os.IsNotExist(err) {
		return make(map[string]map[string]string), nil
	}

	cfg, err := ini.Load(configFile)
	if err != nil {
		return nil, fmt.Errorf("failed to read disks.ini: %w", err)
	}

	disks := make(map[string]map[string]string)
	for _, section := range cfg.Sections() {
		if section.Name() == ini.DefaultSection {
			continue
		}

		diskData := make(map[string]string)
		for _, key := range section.Keys() {
			diskData[key.Name()] = key.Value()
		}
		disks[section.Name()] = diskData
	}

	return disks, nil
}

// readStoragePendingINI reads the /etc/zeropoint/disks.pending.ini file and returns pending format operations
func readStoragePendingINI() ([]EnqueueFormatRequest, error) {
	configFile := DisksPendingConfigFile

	// If file doesn't exist, no pending operations
	if _, err := os.Stat(configFile); os.IsNotExist(err) {
		return []EnqueueFormatRequest{}, nil
	}

	cfg, err := ini.Load(configFile)
	if err != nil {
		return nil, fmt.Errorf("failed to read disks.pending.ini: %w", err)
	}

	var pending []EnqueueFormatRequest
	for _, section := range cfg.Sections() {
		if section.Name() == ini.DefaultSection {
			continue
		}

		req := EnqueueFormatRequest{
			ID:                        section.Name(),
			Filesystem:                section.Key("filesystem").String(),
			Label:                     section.Key("label").String(),
			Wipefs:                    section.Key("wipefs").MustBool(false),
			AutoPartition:             section.Key("auto_partition").MustBool(false),
			ConfirmFixedDiskOperation: section.Key("confirm_fixed_disk_operation").MustBool(false),
		}

		// Parse LUKS if present
		if luksStr := section.Key("luks").String(); luksStr != "" {
			json.Unmarshal([]byte(luksStr), &req.Luks)
		}

		// Parse LVM if present
		if lvmStr := section.Key("lvm").String(); lvmStr != "" {
			json.Unmarshal([]byte(lvmStr), &req.Lvm)
		}

		pending = append(pending, req)
	}

	return pending, nil
}

// FindJobsByResourceID searches for jobs operating on a specific resource
// Used to find and cancel existing jobs when a new operation is enqueued for the same resource
// Returns a list of jobs in active states (Queued, Running) that operate on the given resource
// The resourceKey parameter specifies the Args key to check (e.g., "path_id", "mount_id", "id")
func FindJobsByResourceID(manager *Manager, resourceKey, resourceID string, commandTypes ...CommandType) ([]*Job, error) {
	jobs, err := manager.GetQueued()
	if err != nil {
		return nil, err
	}

	var matching []*Job
	for _, job := range jobs {
		// Check if command type matches (if filters provided)
		if len(commandTypes) > 0 {
			typeMatches := false
			for _, cmdType := range commandTypes {
				if job.Command.Type == cmdType {
					typeMatches = true
					break
				}
			}
			if !typeMatches {
				continue
			}
		}

		// Check if job operates on this resource
		if argVal, ok := job.Command.Args[resourceKey].(string); ok && argVal == resourceID {
			matching = append(matching, job)
		}
	}

	return matching, nil
}
