package queue

import (
	"context"
	"fmt"
	"log/slog"
)

// CreateExposureExecutor implements CommandExecutor for create_exposure commands
type CreateExposureExecutor struct {
	cmd     Command
	handler interface{} // ExposureHandler
	logger  *slog.Logger
}

// Execute runs the create exposure command
func (e *CreateExposureExecutor) Execute(ctx context.Context, callback ProgressCallback) ExecutionResult {
	exposureID, ok := e.cmd.Args["exposure_id"].(string)
	if !ok || exposureID == "" {
		return ExecutionResult{
			Status:   StatusFailed,
			ErrorMsg: "exposure_id is required",
		}
	}

	moduleID, ok := e.cmd.Args["module_id"].(string)
	if !ok || moduleID == "" {
		return ExecutionResult{
			Status:   StatusFailed,
			ErrorMsg: "module_id is required",
		}
	}

	protocol, ok := e.cmd.Args["protocol"].(string)
	if !ok || protocol == "" {
		return ExecutionResult{
			Status:   StatusFailed,
			ErrorMsg: "protocol is required",
		}
	}

	containerPort, ok := e.cmd.Args["container_port"].(int)
	if !ok {
		// Try to convert from float64 (JSON numbers come as float64)
		if portFloat, ok := e.cmd.Args["container_port"].(float64); ok {
			containerPort = int(portFloat)
		} else {
			return ExecutionResult{
				Status:   StatusFailed,
				ErrorMsg: "container_port is required and must be an integer",
			}
		}
	}

	hostname, _ := e.cmd.Args["hostname"].(string)

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

	e.logger.Info("creating exposure", "exposure_id", exposureID, "module_id", moduleID)

	// Call exposure handler method directly to create exposure
	exposureHandler := e.handler.(ExposureHandler)
	if err := exposureHandler.CreateExposure(ctx, exposureID, moduleID, protocol, hostname, uint32(containerPort), tags); err != nil {
		e.logger.Error("failed to create exposure", "exposure_id", exposureID, "error", err)
		return ExecutionResult{
			Status:   StatusFailed,
			ErrorMsg: fmt.Sprintf("failed to create exposure: %v", err),
		}
	}

	result := map[string]interface{}{
		"exposure_id": exposureID,
		"module_id":   moduleID,
		"protocol":    protocol,
		"status":      "created",
	}

	return ExecutionResult{
		Status: StatusCompleted,
		Result: result,
	}
}
