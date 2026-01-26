package queue

import (
	"context"
	"fmt"
	"log/slog"

	"zeropoint-agent/internal/modules"
)

// UninstallModuleExecutor implements CommandExecutor for uninstall_module commands
type UninstallModuleExecutor struct {
	cmd         Command
	uninstaller interface{} // *modules.Uninstaller
	logger      *slog.Logger
}

// Execute runs the uninstall module command
func (e *UninstallModuleExecutor) Execute(ctx context.Context, callback ProgressCallback) ExecutionResult {
	moduleID, ok := e.cmd.Args["module_id"].(string)
	if !ok || moduleID == "" {
		return ExecutionResult{
			Status:   StatusFailed,
			ErrorMsg: "module_id is required",
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

	// Build uninstall request
	req := modules.UninstallRequest{
		ModuleID: moduleID,
	}

	// Call uninstaller directly with progress callback
	uninstaller := e.uninstaller.(*modules.Uninstaller)
	if err := uninstaller.Uninstall(req, modulesProgressCallback); err != nil {
		return ExecutionResult{
			Status:   StatusFailed,
			ErrorMsg: fmt.Sprintf("uninstallation failed: %v", err),
		}
	}

	result := map[string]interface{}{
		"module_id": moduleID,
		"status":    "uninstalled",
	}

	return ExecutionResult{
		Status: StatusCompleted,
		Result: result,
	}
}
