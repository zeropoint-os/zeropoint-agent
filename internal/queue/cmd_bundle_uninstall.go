package queue

import (
	"context"
	"fmt"
	"log/slog"
)

// BundleUninstallExecutor implements CommandExecutor for bundle_uninstall commands
type BundleUninstallExecutor struct {
	cmd    Command
	logger *slog.Logger
}

// Execute runs the bundle uninstall command (meta-job)
// The bundle_uninstall is a meta-job that marks completion after all dependency jobs complete.
// The actual orchestration (exposure deletion, link deletion, module uninstall) is handled by BundleUninstallPlanCreator
// which creates the dependency graph when the bundle uninstall is first enqueued.
func (e *BundleUninstallExecutor) Execute(ctx context.Context, callback ProgressCallback) ExecutionResult {
	bundleID, ok := e.cmd.Args["bundle_id"].(string)
	if !ok || bundleID == "" {
		return ExecutionResult{
			Status:   StatusFailed,
			ErrorMsg: "bundle_id is required",
		}
	}

	callback(ProgressUpdate{
		Status:  "completed",
		Message: fmt.Sprintf("Bundle uninstallation completed: %s", bundleID),
	})

	result := map[string]interface{}{
		"bundle_id": bundleID,
		"status":    "completed",
	}

	return ExecutionResult{
		Status: StatusCompleted,
		Result: result,
	}
}
