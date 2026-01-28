package queue

import (
	"context"
	"fmt"
	"log/slog"
)

// DeleteExposureExecutor implements CommandExecutor for delete_exposure commands
type DeleteExposureExecutor struct {
	cmd     Command
	handler interface{} // ExposureHandler
	logger  *slog.Logger
}

// Execute runs the delete exposure command
func (e *DeleteExposureExecutor) Execute(ctx context.Context, callback ProgressCallback, metadata map[string]interface{}) ExecutionResult {
	_ = metadata
	exposureID, ok := e.cmd.Args["exposure_id"].(string)
	if !ok || exposureID == "" {
		return ExecutionResult{
			Status:   StatusFailed,
			ErrorMsg: "exposure_id is required",
		}
	}

	e.logger.Info("deleting exposure", "exposure_id", exposureID)

	// Call exposure handler method directly to delete exposure
	exposureHandler := e.handler.(ExposureHandler)
	if err := exposureHandler.DeleteExposure(ctx, exposureID); err != nil {
		e.logger.Error("failed to delete exposure", "exposure_id", exposureID, "error", err)
		return ExecutionResult{
			Status:   StatusFailed,
			ErrorMsg: fmt.Sprintf("failed to delete exposure: %v", err),
		}
	}

	result := map[string]interface{}{
		"exposure_id": exposureID,
		"status":      "deleted",
	}

	return ExecutionResult{
		Status: StatusCompleted,
		Result: result,
	}
}
