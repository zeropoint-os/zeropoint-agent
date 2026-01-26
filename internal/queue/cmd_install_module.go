package queue

import (
	"context"
	"fmt"
	"log/slog"

	"zeropoint-agent/internal/modules"
)

// InstallModuleExecutor implements CommandExecutor for install_module commands
type InstallModuleExecutor struct {
	cmd       Command
	installer interface{} // *modules.Installer
	logger    *slog.Logger
}

// Execute runs the install module command
func (e *InstallModuleExecutor) Execute(ctx context.Context, callback ProgressCallback) ExecutionResult {
	moduleID, ok := e.cmd.Args["module_id"].(string)
	if !ok || moduleID == "" {
		return ExecutionResult{
			Status:   StatusFailed,
			ErrorMsg: "module_id is required",
		}
	}

	source, _ := e.cmd.Args["source"].(string)
	localPath, _ := e.cmd.Args["local_path"].(string)

	if source == "" && localPath == "" {
		return ExecutionResult{
			Status:   StatusFailed,
			ErrorMsg: "either source or local_path is required",
		}
	}

	// Extract tags if provided
	var tags []string
	if tagsInterface, ok := e.cmd.Args["tags"]; ok {
		if tagsSlice, ok := tagsInterface.([]interface{}); ok {
			for _, tag := range tagsSlice {
				if tagStr, ok := tag.(string); ok {
					tags = append(tags, tagStr)
				}
			}
		} else if tagsSlice, ok := tagsInterface.([]string); ok {
			tags = tagsSlice
		}
	}

	// Create progress callback that converts modules.ProgressUpdate to our ProgressUpdate
	modulesProgressCallback := func(update modules.ProgressUpdate) {
		callback(ProgressUpdate{
			Status:  update.Status,
			Message: update.Message,
			Error:   update.Error,
		})
	}

	// Build install request
	req := modules.InstallRequest{
		ModuleID:  moduleID,
		Source:    source,
		LocalPath: localPath,
		Tags:      tags,
	}

	// Call installer directly with progress callback
	installer := e.installer.(*modules.Installer)
	if err := installer.Install(req, modulesProgressCallback); err != nil {
		return ExecutionResult{
			Status:   StatusFailed,
			ErrorMsg: fmt.Sprintf("installation failed: %v", err),
		}
	}

	result := map[string]interface{}{
		"module_id": moduleID,
		"status":    "installed",
	}

	return ExecutionResult{
		Status: StatusCompleted,
		Result: result,
	}
}
