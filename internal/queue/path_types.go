package queue

// EnqueueAddPathRequest is the request to add a new path.
// User paths cannot start with zp_ (reserved for system paths).
//
// swagger:model EnqueueAddPathRequest
type EnqueueAddPathRequest struct {
	ID          string   `json:"path_id"`  // Unique path identifier (cannot start with zp_)
	Name        string   `json:"name"`     // Display name
	Path        string   `json:"path"`     // Path location (must be under a mount)
	MountID     string   `json:"mount_id"` // Mount ID that this path belongs to
	Description string   `json:"description,omitempty"`
	DependsOn   []string `json:"depends_on,omitempty"`
}

// EnqueueEditPathRequest is the request to edit an existing path.
// System paths (zp_* prefix) are staged for boot-time application.
// User paths are updated immediately.
//
// swagger:model EnqueueEditPathRequest
type EnqueueEditPathRequest struct {
	ID          string   `json:"path_id"`  // Unique path identifier
	Name        string   `json:"name"`     // Display name
	Path        string   `json:"path"`     // New path location
	OldPath     string   `json:"old_path"` // Old path location (for system paths)
	MountID     string   `json:"mount_id"` // Mount ID that this path belongs to
	Description string   `json:"description,omitempty"`
	DependsOn   []string `json:"depends_on,omitempty"`
}

// EnqueueDeletePathRequest is the request to delete a path.
// System paths (zp_* prefix) cannot be deleted.
//
// swagger:model EnqueueDeletePathRequest
type EnqueueDeletePathRequest struct {
	ID        string   `json:"path_id"` // Unique path identifier (cannot start with zp_)
	DependsOn []string `json:"depends_on,omitempty"`
}
