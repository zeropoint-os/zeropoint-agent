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

// MountPath represents a configured path for mount-based storage
//
// swagger:model MountPath
type MountPath struct {
	ID         string `json:"id"`                    // Unique identifier (e.g., path_mnt_mnt_storage_media)
	Mount      string `json:"mount"`                 // Mount ID this path belongs to (FK)
	PathSuffix string `json:"path_suffix"`           // Subdirectory name (e.g., "media", "photos")
	Status     string `json:"status"`                // "active" or "pending"
	MountPoint string `json:"mount_point,omitempty"` // Enriched: actual mount path from mounts.ini
	FullPath   string `json:"full_path,omitempty"`   // Enriched: full path (mount_point + path_suffix)
}

// MountPathsResponse is the response for list paths
//
// swagger:model MountPathsResponse
type MountPathsResponse struct {
	Paths []MountPath `json:"paths"`
}

// readMountPathsINI reads the active paths.ini file (mount-based paths)
func readMountPathsINI() ([]MountPath, error) {
	configFile := "/etc/zeropoint/paths.ini"

	// If file doesn't exist, return empty list
	if _, err := os.Stat(configFile); os.IsNotExist(err) {
		return []MountPath{}, nil
	}

	cfg, err := ini.Load(configFile)
	if err != nil {
		return nil, fmt.Errorf("failed to read paths.ini: %w", err)
	}

	var paths []MountPath
	for _, section := range cfg.Sections() {
		if section.Name() == ini.DefaultSection {
			continue
		}

		path := MountPath{
			ID:         section.Name(),
			Mount:      section.Key("mount").String(),
			PathSuffix: section.Key("path_suffix").String(),
			Status:     "active",
		}
		paths = append(paths, path)
	}

	return paths, nil
}

// readMountPathsPendingINI reads the pending paths.pending.ini file
func readMountPathsPendingINI() ([]MountPath, error) {
	configFile := "/etc/zeropoint/paths.pending.ini"

	// If file doesn't exist, return empty list
	if _, err := os.Stat(configFile); os.IsNotExist(err) {
		return []MountPath{}, nil
	}

	cfg, err := ini.Load(configFile)
	if err != nil {
		return nil, fmt.Errorf("failed to read paths.pending.ini: %w", err)
	}

	var paths []MountPath
	for _, section := range cfg.Sections() {
		if section.Name() == ini.DefaultSection {
			continue
		}

		path := MountPath{
			ID:         section.Name(),
			Mount:      section.Key("mount").String(),
			PathSuffix: section.Key("path_suffix").String(),
			Status:     "pending",
		}
		paths = append(paths, path)
	}

	return paths, nil
}

// ListPaths handles GET /api/storage/paths (mount-based)
// @Summary List configured paths (mount-based)
// @Description Returns list of configured mount-based paths with enriched mount information
// @Tags storage
// @Produce json
// @Success 200 {object} MountPathsResponse
// @Failure 500 {string} string "Internal server error"
// @Router /api/storage/paths [get]
func (e *apiEnv) ListPaths(w http.ResponseWriter, r *http.Request) {
	// Get active paths
	activePaths, err := readMountPathsINI()
	if err != nil {
		http.Error(w, fmt.Sprintf("failed to read paths: %v", err), http.StatusInternalServerError)
		return
	}

	// Get pending paths
	pendingPaths, err := readMountPathsPendingINI()
	if err != nil {
		http.Error(w, fmt.Sprintf("failed to read pending paths: %v", err), http.StatusInternalServerError)
		return
	}

	// Merge active and pending, with pending taking precedence
	pathMap := make(map[string]MountPath)
	for _, p := range activePaths {
		pathMap[p.ID] = p
	}
	for _, p := range pendingPaths {
		pathMap[p.ID] = p
	}

	// Convert map to slice and sort
	var allPaths []MountPath
	for _, p := range pathMap {
		allPaths = append(allPaths, p)
	}

	sort.Slice(allPaths, func(i, j int) bool {
		return allPaths[i].ID < allPaths[j].ID
	})

	// Enrich with mount point information
	activeMounts, err := readMountsINI()
	if err != nil {
		// Log the warning if logger available
	}

	// Build mount map for quick lookup
	mountMap := make(map[string]Mount)
	for _, m := range activeMounts {
		mountMap[m.ID] = m
	}

	// Enrich paths with mount information
	for i := range allPaths {
		if mount, exists := mountMap[allPaths[i].Mount]; exists {
			allPaths[i].MountPoint = mount.MountPoint
			allPaths[i].FullPath = mount.MountPoint + "/" + allPaths[i].PathSuffix
		}
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(MountPathsResponse{Paths: allPaths})
}

// GetPath handles GET /api/storage/paths/{id} (mount-based)
// @Summary Get a specific path by ID
// @Description Returns details about a specific mount-based path including enriched mount information
// @Tags storage
// @Produce json
// @Param id path string true "Path ID"
// @Success 200 {object} MountPath
// @Failure 404 {string} string "Path not found"
// @Failure 500 {string} string "Internal server error"
// @Router /api/storage/paths/{id} [get]
func (e *apiEnv) GetPath(w http.ResponseWriter, r *http.Request) {
	pathID := mux.Vars(r)["id"]

	// Read active paths
	activePaths, err := readMountPathsINI()
	if err != nil {
		http.Error(w, "failed to read paths", http.StatusInternalServerError)
		return
	}

	// Read pending paths
	pendingPaths, err := readMountPathsPendingINI()
	if err != nil {
		http.Error(w, "failed to read pending paths", http.StatusInternalServerError)
		return
	}

	// Search in both active and pending
	var foundPath *MountPath
	for i := range activePaths {
		if activePaths[i].ID == pathID {
			foundPath = &activePaths[i]
			break
		}
	}
	if foundPath == nil {
		for i := range pendingPaths {
			if pendingPaths[i].ID == pathID {
				foundPath = &pendingPaths[i]
				break
			}
		}
	}

	if foundPath == nil {
		http.Error(w, "path not found", http.StatusNotFound)
		return
	}

	// Enrich with mount point
	activeMounts, err := readMountsINI()
	if err == nil {
		for _, m := range activeMounts {
			if m.ID == foundPath.Mount {
				foundPath.MountPoint = m.MountPoint
				foundPath.FullPath = m.MountPoint + "/" + foundPath.PathSuffix
				break
			}
		}
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(foundPath)
}

// CreatePathRequest is the request to create a user path
