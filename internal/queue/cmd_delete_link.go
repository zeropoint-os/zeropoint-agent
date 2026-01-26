package queue

import (
	"context"
	"fmt"
	"log/slog"
)

// DeleteLinkExecutor implements CommandExecutor for delete_link commands
type DeleteLinkExecutor struct {
	cmd     Command
	handler interface{} // LinkHandler
	logger  *slog.Logger
}

// Execute runs the delete link command
func (e *DeleteLinkExecutor) Execute(ctx context.Context, callback ProgressCallback) ExecutionResult {
	linkID, ok := e.cmd.Args["link_id"].(string)
	if !ok || linkID == "" {
		return ExecutionResult{
			Status:   StatusFailed,
			ErrorMsg: "link_id is required",
		}
	}

	e.logger.Info("deleting link", "link_id", linkID)

	// Call link handler method directly to delete link
	linkHandler := e.handler.(LinkHandler)
	if err := linkHandler.DeleteLink(ctx, linkID); err != nil {
		e.logger.Error("failed to delete link", "link_id", linkID, "error", err)
		return ExecutionResult{
			Status:   StatusFailed,
			ErrorMsg: fmt.Sprintf("failed to delete link: %v", err),
		}
	}

	return ExecutionResult{
		Status: StatusCompleted,
		Result: map[string]interface{}{
			"link_id": linkID,
			"status":  "deleted",
		},
	}
}
