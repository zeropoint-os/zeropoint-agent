package queue

import (
	"time"
)

// JobStatus represents the current status of a job
type JobStatus string

const (
	StatusQueued    JobStatus = "queued"
	StatusRunning   JobStatus = "running"
	StatusCompleted JobStatus = "completed"
	StatusFailed    JobStatus = "failed"
	StatusCancelled JobStatus = "cancelled"
)

// CommandType represents the type of command to execute
type CommandType string

const (
	CmdInstallModule   CommandType = "install_module"
	CmdUninstallModule CommandType = "uninstall_module"
	CmdCreateExposure  CommandType = "create_exposure"
	CmdDeleteExposure  CommandType = "delete_exposure"
	CmdCreateLink      CommandType = "create_link"
	CmdDeleteLink      CommandType = "delete_link"
)

// Command represents a queued command to execute
type Command struct {
	Type CommandType            `json:"type"`
	Args map[string]interface{} `json:"args"` // Command-specific arguments
}

// Job represents a job in the queue
type Job struct {
	ID          string      `json:"id"`
	Status      JobStatus   `json:"status"`
	Command     Command     `json:"command"`
	DependsOn   []string    `json:"depends_on"` // IDs of jobs this depends on
	CreatedAt   time.Time   `json:"created_at"`
	StartedAt   *time.Time  `json:"started_at,omitempty"`
	CompletedAt *time.Time  `json:"completed_at,omitempty"`
	Result      interface{} `json:"result,omitempty"`
	Error       string      `json:"error,omitempty"`
}

// Event represents a single event in a job's execution
type Event struct {
	Timestamp time.Time   `json:"timestamp"`
	Type      string      `json:"type"` // "info", "progress", "error", "warning"
	Message   string      `json:"message"`
	Data      interface{} `json:"data,omitempty"`
}

// JobResponse represents a job in API responses
type JobResponse struct {
	ID          string      `json:"id"`
	Status      JobStatus   `json:"status"`
	Command     Command     `json:"command"`
	DependsOn   []string    `json:"depends_on"`
	CreatedAt   time.Time   `json:"created_at"`
	StartedAt   *time.Time  `json:"started_at,omitempty"`
	CompletedAt *time.Time  `json:"completed_at,omitempty"`
	Result      interface{} `json:"result,omitempty"`
	Error       string      `json:"error,omitempty"`
	Events      []Event     `json:"events"`
}

// EnqueueRequest is the base for operation-specific enqueue requests
// Specific operations (install, expose, etc) add this as an embedded field
type EnqueueRequest struct {
	DependsOn []string `json:"depends_on,omitempty"`
}

// ListJobsResponse is the response for listing jobs
type ListJobsResponse struct {
	Jobs []JobResponse `json:"jobs"`
}
