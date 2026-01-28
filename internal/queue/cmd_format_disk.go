package queue

import (
	"context"
	"log/slog"
)

// FormatDiskExecutor implements CommandExecutor for format_disk commands
type FormatDiskExecutor struct {
	cmd    Command
	logger *slog.Logger
}

// Execute stages the format operation for boot-time execution via systemd
func (e *FormatDiskExecutor) Execute(ctx context.Context, callback ProgressCallback, metadata map[string]interface{}) ExecutionResult {
	_ = metadata
	// Format disk operations are staged at boot time via systemd
	// Return pending status since the actual execution happens on reboot
	callback(ProgressUpdate{
		Status:  "pending",
		Message: "Format operation staged for boot-time execution. System must be rebooted to execute.",
	})
	return ExecutionResult{
		Status: StatusPending,
		Result: map[string]interface{}{
			"message": "Format operation staged for boot-time execution. System must be rebooted to execute.",
		},
	}
}

// UnknownCommandExecutor handles unknown command types
type UnknownCommandExecutor struct {
	cmd    Command
	logger *slog.Logger
}

// Execute returns an error for unknown command types
func (e *UnknownCommandExecutor) Execute(ctx context.Context, callback ProgressCallback, metadata map[string]interface{}) ExecutionResult {
	_ = metadata
	return ExecutionResult{
		Status:   StatusFailed,
		ErrorMsg: "unknown command type: " + string(e.cmd.Type),
	}
}
