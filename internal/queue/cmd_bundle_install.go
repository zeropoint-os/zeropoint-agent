package queue

import (
	"context"
	"fmt"
	"log/slog"
)

// BundleInstallExecutor implements CommandExecutor for bundle_install commands
type BundleInstallExecutor struct {
	cmd    Command
	logger *slog.Logger
}

// Execute runs the bundle install command (meta-job)
// The bundle_install is a meta-job that marks completion after all dependency jobs complete.
// The actual orchestration (module installation, linking, exposures) is handled by BundleInstallPlanCreator
// which creates the dependency graph when the bundle is first enqueued.
func (e *BundleInstallExecutor) Execute(ctx context.Context, callback ProgressCallback) ExecutionResult {
	bundleName, ok := e.cmd.Args["bundle_name"].(string)
	if !ok || bundleName == "" {
		return ExecutionResult{
			Status:   StatusFailed,
			ErrorMsg: "bundle_name is required",
		}
	}

	bundleID, ok := e.cmd.Args["bundle_id"].(string)
	if !ok || bundleID == "" {
		return ExecutionResult{
			Status:   StatusFailed,
			ErrorMsg: "bundle_id is required",
		}
	}

	callback(ProgressUpdate{
		Status:  "completed",
		Message: fmt.Sprintf("Bundle installation completed: %s", bundleName),
	})

	result := map[string]interface{}{
		"bundle_name": bundleName,
		"bundle_id":   bundleID,
		"status":      "completed",
	}

	return ExecutionResult{
		Status: StatusCompleted,
		Result: result,
	}
}
