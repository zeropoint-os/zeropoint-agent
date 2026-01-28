package queue

import (
	"bufio"
	"context"
	"io"
	"log/slog"
	"os/exec"
	"time"

	"zeropoint-agent/internal/catalog"
	"zeropoint-agent/internal/modules"
)

// ExposureHandler interface for creating/deleting exposures
type ExposureHandler interface {
	CreateExposure(ctx context.Context, exposureID, moduleID, protocol, hostname string, containerPort uint32, tags []string) error
	DeleteExposure(ctx context.Context, exposureID string) error
}

// LinkHandler interface for creating/deleting links
type LinkHandler interface {
	CreateLink(ctx context.Context, linkID string, modules map[string]map[string]interface{}, tags []string) error
	DeleteLink(ctx context.Context, id string) error
}

// BundleStoreHandler interface for persisting bundle installations
type BundleStoreHandler interface {
	CreateBundle(bundleID, bundleName, jobID string) interface{}
	AddModuleComponent(bundleID, moduleID string, status, errMsg string) error
	AddLinkComponent(bundleID, linkID string, status, errMsg string) error
	AddExposureComponent(bundleID, exposureID string, status, errMsg string) error
	UpdateModuleComponentStatus(bundleID, moduleID, status, errMsg string) error
	UpdateLinkComponentStatus(bundleID, linkID, status, errMsg string) error
	UpdateExposureComponentStatus(bundleID, exposureID, status, errMsg string) error
	GetBundle(bundleID string) (interface{}, error)
	CompleteBundleInstallation(bundleID string, success bool) error
	DeleteBundle(bundleID string) error
}

// JobExecutor executes queued commands by calling handlers and installers directly
type JobExecutor struct {
	installer       *modules.Installer
	uninstaller     *modules.Uninstaller
	exposureHandler ExposureHandler
	linkHandler     LinkHandler
	catalogStore    *catalog.Store
	bundleStore     BundleStoreHandler
	logger          *slog.Logger
}

// NewJobExecutor creates a new job executor with direct access to handlers
func NewJobExecutor(installer *modules.Installer, uninstaller *modules.Uninstaller, exposureHandler ExposureHandler, linkHandler LinkHandler, catalogStore *catalog.Store, bundleStore BundleStoreHandler, logger *slog.Logger) *JobExecutor {
	return &JobExecutor{
		installer:       installer,
		uninstaller:     uninstaller,
		exposureHandler: exposureHandler,
		linkHandler:     linkHandler,
		catalogStore:    catalogStore,
		bundleStore:     bundleStore,
		logger:          logger,
	}
}

// ExecuteWithJob runs a command by delegating to its polymorphic executor
func (e *JobExecutor) ExecuteWithJob(ctx context.Context, jobID string, manager *Manager, cmd Command) ExecutionResult {
	// Get job metadata to pass to executor
	metadata, err := manager.GetJobMetadata(jobID)
	if err != nil {
		e.logger.Error("failed to get job metadata", "job_id", jobID, "error", err)
		metadata = make(map[string]interface{})
	}

	// Create callback that appends events to the job
	callback := func(update ProgressUpdate) {
		event := Event{
			Timestamp: time.Now().UTC(),
			Type:      "progress",
			Message:   update.Message,
			Data:      update.Data,
		}
		if update.Error != "" {
			event.Type = "error"
		}
		if err := manager.AppendEvent(jobID, event); err != nil {
			e.logger.Error("failed to append event", "job_id", jobID, "error", err)
		}
	}

	// Get the polymorphic executor for this command type
	executor := cmd.ToExecutor(
		e.installer,
		e.uninstaller,
		e.exposureHandler,
		e.linkHandler,
		e.catalogStore,
		e.bundleStore,
		e.logger,
	)

	// Execute the command with metadata - it returns updated metadata
	result := executor.Execute(ctx, callback, metadata)

	// Persist updated metadata if the executor modified it
	if result.Metadata != nil && len(result.Metadata) > 0 {
		if err := manager.UpdateMetadata(jobID, result.Metadata); err != nil {
			e.logger.Error("failed to update job metadata", "job_id", jobID, "error", err)
		}
	}

	return result
}

// runCmdJobStream runs a command and appends stdout/stderr lines as job events
func runCmdJobStream(ctx context.Context, manager *Manager, jobID string, name string, args ...string) error {
	cmd := exec.CommandContext(ctx, name, args...)
	stdout, _ := cmd.StdoutPipe()
	stderr, _ := cmd.StderrPipe()
	if err := cmd.Start(); err != nil {
		_ = manager.AppendEvent(jobID, Event{Timestamp: time.Now().UTC(), Type: "error", Message: "failed to start command: " + err.Error()})
		return err
	}

	stream := func(r io.Reader, streamName string) {
		scanner := bufio.NewScanner(r)
		for scanner.Scan() {
			_ = manager.AppendEvent(jobID, Event{Timestamp: time.Now().UTC(), Type: "log", Message: streamName + ": " + scanner.Text()})
		}
	}
	go stream(stdout, "stdout")
	go stream(stderr, "stderr")

	if err := cmd.Wait(); err != nil {
		_ = manager.AppendEvent(jobID, Event{Timestamp: time.Now().UTC(), Type: "error", Message: "command failed: " + err.Error()})
		return err
	}
	return nil
}

// Ensure JobExecutor implements Executor interface
var _ Executor = (*JobExecutor)(nil)
