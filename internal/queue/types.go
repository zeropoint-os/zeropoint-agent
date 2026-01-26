package queue

import (
	"context"
	"log/slog"
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
	StatusPending   JobStatus = "pending" // Job is pending (e.g., awaiting reboot for format_disk)
)

// ProgressUpdate represents a status update from a command executor
type ProgressUpdate struct {
	Status  string      // "pending", "in_progress", "completed", "failed"
	Message string      // Human-readable status message
	Data    interface{} // Command-specific data
	Error   string      // Error details if status is failed
}

// ProgressCallback is called by command executors to report status updates
type ProgressCallback func(ProgressUpdate)

// ExecutionResult is returned by command executors to specify the result of execution
type ExecutionResult struct {
	Status   JobStatus   // Status to set on the job (completed, failed, pending, cancelled, etc.)
	Result   interface{} // Command-specific result data
	ErrorMsg string      // Error message if status is failed
}

// CommandType represents the type of command to execute
type CommandType string

const (
	CmdInstallModule   CommandType = "install_module"
	CmdUninstallModule CommandType = "uninstall_module"
	CmdCreateExposure  CommandType = "create_exposure"
	CmdDeleteExposure  CommandType = "delete_exposure"
	CmdCreateLink      CommandType = "create_link"
	CmdDeleteLink      CommandType = "delete_link"
	CmdBundleInstall   CommandType = "bundle_install"   // Meta-job that orchestrates bundle installation
	CmdBundleUninstall CommandType = "bundle_uninstall" // Meta-job that orchestrates bundle uninstallation
	CmdFormatDisk      CommandType = "format_disk"
	CmdCreateMount     CommandType = "create_mount"
	CmdDeleteMount     CommandType = "delete_mount"
	CmdEditSystemPath  CommandType = "edit_system_path"
	CmdAddUserPath     CommandType = "add_user_path"
	CmdDeleteUserPath  CommandType = "delete_user_path"
)

// CommandExecutor is the interface all command types must implement
type CommandExecutor interface {
	Execute(ctx context.Context, callback ProgressCallback) ExecutionResult
}

// Command represents a queued command to execute
type Command struct {
	Type CommandType            `json:"type"`
	Args map[string]interface{} `json:"args"` // Command-specific arguments
}

// ToExecutor creates an executor for this command
// This allows polymorphic dispatch based on command type
func (c Command) ToExecutor(installer interface{}, uninstaller interface{}, exposureHandler interface{}, linkHandler interface{}, catalogStore interface{}, bundleStore interface{}, logger *slog.Logger) CommandExecutor {
	switch c.Type {
	case CmdInstallModule:
		return &InstallModuleExecutor{
			cmd:       c,
			installer: installer,
			logger:    logger,
		}
	case CmdUninstallModule:
		return &UninstallModuleExecutor{
			cmd:         c,
			uninstaller: uninstaller,
			logger:      logger,
		}
	case CmdCreateExposure:
		return &CreateExposureExecutor{
			cmd:     c,
			handler: exposureHandler,
			logger:  logger,
		}
	case CmdDeleteExposure:
		return &DeleteExposureExecutor{
			cmd:     c,
			handler: exposureHandler,
			logger:  logger,
		}
	case CmdCreateLink:
		return &CreateLinkExecutor{
			cmd:     c,
			handler: linkHandler,
			logger:  logger,
		}
	case CmdDeleteLink:
		return &DeleteLinkExecutor{
			cmd:     c,
			handler: linkHandler,
			logger:  logger,
		}
	case CmdBundleInstall:
		return &BundleInstallExecutor{
			cmd:    c,
			logger: logger,
		}
	case CmdBundleUninstall:
		return &BundleUninstallExecutor{
			cmd:    c,
			logger: logger,
		}
	case CmdFormatDisk:
		return &FormatDiskExecutor{
			cmd:    c,
			logger: logger,
		}
	case CmdCreateMount:
		return &CreateMountExecutor{
			cmd:    c,
			logger: logger,
		}
	case CmdDeleteMount:
		return &DeleteMountExecutor{
			cmd:    c,
			logger: logger,
		}
	case CmdEditSystemPath:
		return &EditSystemPathExecutor{
			cmd:    c,
			logger: logger,
		}
	case CmdAddUserPath:
		return &AddUserPathExecutor{
			cmd:    c,
			logger: logger,
		}
	case CmdDeleteUserPath:
		return &DeleteUserPathExecutor{
			cmd:    c,
			logger: logger,
		}
	default:
		return &UnknownCommandExecutor{
			cmd:    c,
			logger: logger,
		}
	}
}

// Job represents a job in the queue
type Job struct {
	ID          string      `json:"id"`
	Status      JobStatus   `json:"status"`
	Command     Command     `json:"command"`
	DependsOn   []string    `json:"depends_on"`     // IDs of jobs this depends on
	Tags        []string    `json:"tags,omitempty"` // Tags associated with this job (e.g., bundle name)
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
	Tags        []string    `json:"tags,omitempty"` // Tags associated with this job (e.g., bundle name)
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
