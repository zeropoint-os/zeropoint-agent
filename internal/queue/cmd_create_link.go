package queue

import (
	"context"
	"fmt"
	"log/slog"
)

// CreateLinkExecutor implements CommandExecutor for create_link commands
type CreateLinkExecutor struct {
	cmd     Command
	handler interface{} // LinkHandler
	logger  *slog.Logger
}

// Execute runs the create link command
func (e *CreateLinkExecutor) Execute(ctx context.Context, callback ProgressCallback, metadata map[string]interface{}) ExecutionResult {
	_ = metadata
	linkID, ok := e.cmd.Args["link_id"].(string)
	if !ok || linkID == "" {
		return ExecutionResult{
			Status:   StatusFailed,
			ErrorMsg: "link_id is required",
		}
	}

	modules, ok := e.cmd.Args["modules"].(map[string]interface{})
	if !ok {
		return ExecutionResult{
			Status:   StatusFailed,
			ErrorMsg: "modules is required",
		}
	}

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

	e.logger.Info("creating link", "link_id", linkID)

	// Convert modules to the correct type for the handler
	modulesConfig := make(map[string]map[string]interface{})
	for moduleName, config := range modules {
		if moduleConfig, ok := config.(map[string]interface{}); ok {
			modulesConfig[moduleName] = moduleConfig
		} else {
			return ExecutionResult{
				Status:   StatusFailed,
				ErrorMsg: fmt.Sprintf("module %s config must be a map", moduleName),
			}
		}
	}

	// Call link handler method directly to create link
	linkHandler := e.handler.(LinkHandler)
	if err := linkHandler.CreateLink(ctx, linkID, modulesConfig, tags); err != nil {
		e.logger.Error("failed to create link", "link_id", linkID, "error", err)
		return ExecutionResult{
			Status:   StatusFailed,
			ErrorMsg: fmt.Sprintf("failed to create link: %v", err),
		}
	}

	result := map[string]interface{}{
		"link_id": linkID,
		"modules": modulesConfig,
		"tags":    tags,
		"status":  "created",
	}

	return ExecutionResult{
		Status: StatusCompleted,
		Result: result,
	}
}
