package queue

// EnqueueEditSystemPathRequest is the request to edit a system path
//
// swagger:model EnqueueEditSystemPathRequest
type EnqueueEditSystemPathRequest struct {
	PathID    string   `json:"path_id"`  // System path ID (zp_* prefix)
	NewPath   string   `json:"new_path"` // New path location
	DependsOn []string `json:"depends_on,omitempty"`
}

// EnqueueAddUserPathRequest is the request to add a user-defined path
//
// swagger:model EnqueueAddUserPathRequest
type EnqueueAddUserPathRequest struct {
	Name        string   `json:"name"`     // Display name
	Path        string   `json:"path"`     // Path location (must be under a mount)
	MountID     string   `json:"mount_id"` // Mount ID that this path belongs to
	Description string   `json:"description,omitempty"`
	DependsOn   []string `json:"depends_on,omitempty"`
}

// EnqueueDeleteUserPathRequest is the request to delete a user-defined path
//
// swagger:model EnqueueDeleteUserPathRequest
type EnqueueDeleteUserPathRequest struct {
	PathID    string   `json:"path_id"` // User path ID
	DependsOn []string `json:"depends_on,omitempty"`
}
