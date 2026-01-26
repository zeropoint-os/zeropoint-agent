package api

import (
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"sort"

	"github.com/gorilla/mux"
	"gopkg.in/ini.v1"
)

// Mount represents a mounted filesystem
//
// swagger:model Mount
type Mount struct {
	ID         string `json:"id"`          // Unique identifier for the mount (section name in ini)
	MountPoint string `json:"mount_point"` // Where filesystem is mounted (e.g., /)
	DiskID     string `json:"disk_id"`     // Stable disk ID that this mount uses (e.g., nvme-eui.0025385c2140105d)
	Type       string `json:"type"`        // Filesystem type (ext4, tmpfs, overlay, etc.)
	Status     string `json:"status"`      // "active" or "pending"
}

// CreateMountRequest is the request to create/update a mount entry
//
// swagger:model CreateMountRequest
type CreateMountRequest struct {
	MountPoint string `json:"mount_point"` // Where filesystem is mounted
	DiskID     string `json:"disk_id"`     // Stable disk ID (from /api/storage/disks)
	Type       string `json:"type"`        // Filesystem type
}

// MountsResponse is the response for list mounts
//
// swagger:model MountsResponse
type MountsResponse struct {
	Mounts []Mount `json:"mounts"`
}

// readMountsINI reads the completed mounts.ini file
func readMountsINI() ([]Mount, error) {
	configFile := "/etc/zeropoint/mounts.ini"

	// If file doesn't exist, return empty list
	if _, err := os.Stat(configFile); os.IsNotExist(err) {
		return []Mount{}, nil
	}

	cfg, err := ini.Load(configFile)
	if err != nil {
		return nil, fmt.Errorf("failed to read mounts.ini: %w", err)
	}

	var mounts []Mount
	for _, section := range cfg.Sections() {
		if section.Name() == ini.DefaultSection {
			continue
		}

		mount := Mount{
			ID:         section.Name(),
			MountPoint: section.Key("mount_point").String(),
			DiskID:     section.Key("disk_id").String(),
			Type:       section.Key("type").String(),
			Status:     "active",
		}
		mounts = append(mounts, mount)
	}

	return mounts, nil
}

// readMountsPendingINI reads the pending mounts.pending.ini file
func readMountsPendingINI() ([]Mount, error) {
	configFile := "/etc/zeropoint/mounts.pending.ini"

	// If file doesn't exist, return empty list
	if _, err := os.Stat(configFile); os.IsNotExist(err) {
		return []Mount{}, nil
	}

	cfg, err := ini.Load(configFile)
	if err != nil {
		return nil, fmt.Errorf("failed to read mounts.pending.ini: %w", err)
	}

	var mounts []Mount
	for _, section := range cfg.Sections() {
		if section.Name() == ini.DefaultSection {
			continue
		}

		mount := Mount{
			ID:         section.Name(),
			MountPoint: section.Key("mount_point").String(),
			DiskID:     section.Key("disk_id").String(),
			Type:       section.Key("type").String(),
			Status:     "pending",
		}
		mounts = append(mounts, mount)
	}

	return mounts, nil
}

// ListMounts handles GET /api/storage/mounts
// Returns active mounts from mounts.ini plus any pending changes from mounts.pending.ini
//
// @Summary List mounted filesystems
// @Description Returns list of configured mounts (active and pending)
// @Tags storage
// @Produce json
// @Success 200 {object} MountsResponse
// @Failure 500 {object} map[string]string
// @Router /api/storage/mounts [get]
func (e *apiEnv) ListMounts(w http.ResponseWriter, r *http.Request) {
	// Get active mounts
	activeMounts, err := readMountsINI()
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}

	// Get pending mounts
	pendingMounts, err := readMountsPendingINI()
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}

	// Combine: active mounts + pending updates
	// Pending mounts override active ones if same ID
	mountMap := make(map[string]Mount)
	for _, m := range activeMounts {
		mountMap[m.ID] = m
	}
	for _, m := range pendingMounts {
		// Check if it's a deletion marker
		if m.MountPoint == "" && m.DiskID == "" {
			delete(mountMap, m.ID)
		} else {
			mountMap[m.ID] = m
		}
	}

	// Convert map back to slice
	var allMounts []Mount
	for _, m := range mountMap {
		allMounts = append(allMounts, m)
	}

	// Sort by mount point for stable ordering
	sort.Slice(allMounts, func(i, j int) bool {
		return allMounts[i].MountPoint < allMounts[j].MountPoint
	})

	response := MountsResponse{Mounts: allMounts}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(response)
}

// GetMount handles GET /api/storage/mounts/{id}
// Returns a single mount by ID
//
// @Summary Get a single mount
// @Description Returns detailed information for a single mount
// @Tags storage
// @Produce json
// @Param id path string true "Mount ID"
// @Success 200 {object} Mount
// @Failure 404 {object} map[string]string
// @Failure 500 {object} map[string]string
// @Router /api/storage/mounts/{id} [get]
func (e *apiEnv) GetMount(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	id := vars["id"]

	// Check pending first
	pendingMounts, err := readMountsPendingINI()
	if err == nil {
		for _, m := range pendingMounts {
			if m.ID == id {
				w.Header().Set("Content-Type", "application/json")
				json.NewEncoder(w).Encode(m)
				return
			}
		}
	}

	// Check active
	activeMounts, err := readMountsINI()
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}

	for _, m := range activeMounts {
		if m.ID == id {
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(m)
			return
		}
	}

	http.Error(w, "mount not found", http.StatusNotFound)
}

// CreateMount handles POST /api/storage/mounts
// Creates or updates a mount entry - delegates to queue system which writes to pending file
//
// @Summary Create or update a mount
// @Description Enqueues mount operation (executed at boot)
// @Tags storage
// @Accept json
// @Produce json
// @Param body body CreateMountRequest true "Mount details"
// @Success 201 {object} Mount "Mount staged for boot-time execution"
// @Failure 400 {string} string "Bad request"
// @Failure 500 {string} string "Internal server error"
// @Router /api/storage/mounts [post]
func (e *apiEnv) CreateMount(w http.ResponseWriter, r *http.Request) {
	var req CreateMountRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "invalid request body", http.StatusBadRequest)
		return
	}

	// Validate required fields
	if req.MountPoint == "" || req.DiskID == "" || req.Type == "" {
		http.Error(w, "mount_point, disk_id, and type are required", http.StatusBadRequest)
		return
	}

	// Prevent root mount modification
	if req.MountPoint == "/" {
		http.Error(w, "cannot create or modify root mount point", http.StatusBadRequest)
		return
	}

	// Return a pending mount response (actual job creation happens via queue endpoint)
	mount := Mount{
		ID:         sanitizeID(req.MountPoint),
		MountPoint: req.MountPoint,
		DiskID:     req.DiskID,
		Type:       req.Type,
		Status:     "pending",
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusCreated)
	json.NewEncoder(w).Encode(mount)
}

// UpdateMount handles PUT /api/storage/mounts/{id}
// Updates an existing mount entry - delegates to queue system
//
// @Summary Update a mount
// @Description Updates mount in pending (executed at boot)
// @Tags storage
// @Accept json
// @Produce json
// @Param id path string true "Mount ID"
// @Param body body CreateMountRequest true "Updated mount details"
// @Success 200 {object} Mount
// @Failure 400 {string} string "Bad request"
// @Failure 500 {string} string "Internal server error"
// @Router /api/storage/mounts/{id} [put]
func (e *apiEnv) UpdateMount(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	id := vars["id"]

	var req CreateMountRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "invalid request body", http.StatusBadRequest)
		return
	}

	// Validate required fields
	if req.MountPoint == "" || req.DiskID == "" || req.Type == "" {
		http.Error(w, "mount_point, disk_id, and type are required", http.StatusBadRequest)
		return
	}

	// Prevent root mount modification
	if req.MountPoint == "/" {
		http.Error(w, "cannot create or modify root mount point", http.StatusBadRequest)
		return
	}

	// Return the updated mount response (actual job creation happens via queue endpoint)
	mount := Mount{
		ID:         id,
		MountPoint: req.MountPoint,
		DiskID:     req.DiskID,
		Type:       req.Type,
		Status:     "pending",
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(mount)
}

// DeleteMount handles DELETE /api/storage/mounts/{id}
// Marks a mount for deletion - delegates to queue system
//
// @Summary Delete a mount
// @Description Marks mount for removal (executed at boot)
// @Tags storage
// @Param id path string true "Mount ID"
// @Success 204 "Mount marked for deletion"
// @Failure 400 {string} string "Bad request"
// @Failure 404 {string} string "Not found"
// @Failure 500 {string} string "Internal server error"
// @Router /api/storage/mounts/{id} [delete]
func (e *apiEnv) DeleteMount(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	id := vars["id"]

	// Find the mount to check if it's root
	activeMounts, err := readMountsINI()
	if err == nil {
		for _, m := range activeMounts {
			if m.ID == id && m.MountPoint == "/" {
				http.Error(w, "cannot delete root mount point", http.StatusBadRequest)
				return
			}
		}
	}

	w.WriteHeader(http.StatusNoContent)
}

// sanitizeID converts a mount_point to a safe section ID for ini file
// e.g., "/mnt/storage" -> "mnt_storage"
func sanitizeID(mountPoint string) string {
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
