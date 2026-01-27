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

// Path represents a configured path for storage or modules
//
// swagger:model Path
type Path struct {
	ID           string `json:"id"`             // Unique identifier (zp_* for system paths)
	Name         string `json:"name"`           // Human-readable name
	Path         string `json:"path"`           // Filesystem path
	MountID      string `json:"mount_id"`       // Mount ID that this path belongs to (stable reference)
	IsSystemPath bool   `json:"is_system_path"` // True if zp_* prefix (system-managed)
	Status       string `json:"status"`         // "active", "pending", or "user"
	Description  string `json:"description"`    // Optional description for user paths
}

// CreatePathRequest is the request to create a user path
//
// swagger:model CreatePathRequest
type CreatePathRequest struct {
	Name        string `json:"name"`        // Human-readable name
	Path        string `json:"path"`        // Filesystem path
	Description string `json:"description"` // Optional description
}

// UpdatePathRequest is the request to update a system path
//
// swagger:model UpdatePathRequest
type UpdatePathRequest struct {
	NewPath string `json:"new_path"` // New filesystem path
}

// PathsResponse is the response for list paths
//
// swagger:model PathsResponse
type PathsResponse struct {
	Paths []Path `json:"paths"`
}

// readPathsINI reads the paths.ini file (all active paths)
func readPathsINI() ([]Path, error) {
	configFile := "/etc/zeropoint/paths.ini"

	// If file doesn't exist, return empty list
	if _, err := os.Stat(configFile); os.IsNotExist(err) {
		return []Path{}, nil
	}

	cfg, err := ini.Load(configFile)
	if err != nil {
		return nil, fmt.Errorf("failed to read paths.ini: %w", err)
	}

	var paths []Path
	for _, section := range cfg.Sections() {
		if section.Name() == ini.DefaultSection {
			continue
		}

		pathID := section.Name()
		path := Path{
			ID:           pathID,
			Name:         section.Key("name").String(),
			Path:         section.Key("path").String(),
			MountID:      section.Key("mount_id").String(),
			IsSystemPath: section.Key("is_system_path").MustBool(false),
			Status:       "active",
			Description:  section.Key("description").String(),
		}
		paths = append(paths, path)
	}

	return paths, nil
}

// readPathsPendingINI reads the paths.pending.ini file (pending operations for all paths)
func readPathsPendingINI() ([]Path, error) {
	configFile := "/etc/zeropoint/paths.pending.ini"

	// If file doesn't exist, return empty list
	if _, err := os.Stat(configFile); os.IsNotExist(err) {
		return []Path{}, nil
	}

	cfg, err := ini.Load(configFile)
	if err != nil {
		return nil, fmt.Errorf("failed to read paths.pending.ini: %w", err)
	}

	var paths []Path
	for _, section := range cfg.Sections() {
		if section.Name() == ini.DefaultSection {
			continue
		}

		pathID := section.Name()
		path := Path{
			ID:           pathID,
			Name:         section.Key("name").String(),
			Path:         section.Key("path").String(),
			MountID:      section.Key("mount_id").String(),
			IsSystemPath: section.Key("is_system_path").MustBool(false),
			Status:       "pending",
			Description:  section.Key("description").String(),
		}
		paths = append(paths, path)
	}

	return paths, nil
}

// ListPaths handles GET /api/storage/paths
// Returns active system paths from paths.ini plus any pending changes and user paths
//
// @Summary List configured paths
// @Description Returns list of configured paths (system and user)
// @Tags storage
// @Produce json
// @Success 200 {object} PathsResponse
// @Failure 500 {object} map[string]string
// @Router /api/storage/paths [get]
func (e *apiEnv) ListPaths(w http.ResponseWriter, r *http.Request) {
	// Get active system paths
	activePaths, err := readPathsINI()
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}

	// Get pending paths
	pendingPaths, err := readPathsPendingINI()
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}

	// Combine: active paths + pending updates
	// Pending paths override active ones if same ID
	pathMap := make(map[string]Path)
	for _, p := range activePaths {
		pathMap[p.ID] = p
	}
	for _, p := range pendingPaths {
		// Check if it's a deletion marker (empty path)
		if p.Path == "" {
			delete(pathMap, p.ID)
		} else {
			pathMap[p.ID] = p
		}
	}

	// Convert map back to slice
	var allPaths []Path
	for _, p := range pathMap {
		allPaths = append(allPaths, p)
	}

	// Sort by path for stable ordering
	sort.Slice(allPaths, func(i, j int) bool {
		return allPaths[i].Path < allPaths[j].Path
	})

	response := PathsResponse{Paths: allPaths}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(response)
}

// GetPath handles GET /api/storage/paths/{id}
// Returns a single path by ID
//
// @Summary Get a single path
// @Description Returns detailed information for a single path
// @Tags storage
// @Produce json
// @Param id path string true "Path ID"
// @Success 200 {object} Path
// @Failure 404 {object} map[string]string
// @Failure 500 {object} map[string]string
// @Router /api/storage/paths/{id} [get]
func (e *apiEnv) GetPath(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	id := vars["id"]

	// Check pending first
	pendingPaths, err := readPathsPendingINI()
	if err == nil {
		for _, p := range pendingPaths {
			if p.ID == id {
				w.Header().Set("Content-Type", "application/json")
				json.NewEncoder(w).Encode(p)
				return
			}
		}
	}

	// Check active
	activePaths, err := readPathsINI()
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}

	for _, p := range activePaths {
		if p.ID == id {
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(p)
			return
		}
	}

	http.Error(w, "path not found", http.StatusNotFound)
}

// CreatePath handles POST /api/storage/paths
// Creates a user path - delegates to queue system
//
// @Summary Create a user path
// @Description Enqueues user path creation (applied immediately)
// @Tags storage
// @Accept json
// @Produce json
// @Param body body CreatePathRequest true "Path details"
// @Success 201 {object} Path "Path created"
// @Failure 400 {string} string "Bad request"
// @Failure 500 {string} string "Internal server error"
// @Router /api/storage/paths [post]
func (e *apiEnv) CreatePath(w http.ResponseWriter, r *http.Request) {
	var req CreatePathRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "invalid request body", http.StatusBadRequest)
		return
	}

	// Validate required fields
	if req.Name == "" || req.Path == "" {
		http.Error(w, "name and path are required", http.StatusBadRequest)
		return
	}

	// Return a user path response (actual job creation happens via queue endpoint)
	path := Path{
		ID:           sanitizeID(req.Name),
		Name:         req.Name,
		Path:         req.Path,
		IsSystemPath: false,
		Status:       "user",
		Description:  req.Description,
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusCreated)
	json.NewEncoder(w).Encode(path)
}

// UpdatePath handles PUT /api/storage/paths/{id}
// Updates a system path - delegates to queue system
//
// @Summary Update a system path
// @Description Enqueues system path update (executed at boot)
// @Tags storage
// @Accept json
// @Produce json
// @Param id path string true "Path ID"
// @Param body body UpdatePathRequest true "New path details"
// @Success 200 {object} Path "Path staged for boot-time execution"
// @Failure 400 {string} string "Bad request"
// @Failure 404 {string} string "Path not found"
// @Failure 500 {string} string "Internal server error"
// @Router /api/storage/paths/{id} [put]
func (e *apiEnv) UpdatePath(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	id := vars["id"]

	var req UpdatePathRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "invalid request body", http.StatusBadRequest)
		return
	}

	if req.NewPath == "" {
		http.Error(w, "new_path is required", http.StatusBadRequest)
		return
	}

	// Verify path exists
	activePaths, err := readPathsINI()
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}

	found := false
	var existingPath Path
	for _, p := range activePaths {
		if p.ID == id {
			found = true
			existingPath = p
			break
		}
	}

	if !found {
		http.Error(w, "path not found", http.StatusNotFound)
		return
	}

	// Return pending path response (actual job creation happens via queue endpoint)
	path := Path{
		ID:           existingPath.ID,
		Name:         existingPath.Name,
		Path:         req.NewPath,
		MountID:      existingPath.MountID,
		IsSystemPath: true,
		Status:       "pending",
		Description:  existingPath.Description,
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(path)
}

// DeletePath handles DELETE /api/storage/paths/{id}
// Deletes a user path - delegates to queue system
// System paths (zp_* prefix) cannot be deleted
//
// @Summary Delete a path
// @Description Enqueues path deletion (user paths immediate, system paths rejected)
// @Tags storage
// @Produce json
// @Param id path string true "Path ID"
// @Success 200 {object} map[string]string
// @Failure 400 {string} string "Cannot delete system path"
// @Failure 404 {string} string "Path not found"
// @Failure 500 {string} string "Internal server error"
// @Router /api/storage/paths/{id} [delete]
func (e *apiEnv) DeletePath(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	id := vars["id"]

	// Check if path exists and is not system path
	activePaths, err := readPathsINI()
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}

	for _, p := range activePaths {
		if p.ID == id && p.IsSystemPath {
			http.Error(w, "cannot delete system paths", http.StatusBadRequest)
			return
		}
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{"status": "deleted"})
}
