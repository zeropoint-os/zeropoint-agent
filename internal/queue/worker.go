package queue

import (
	"context"
	"fmt"
	"log/slog"
	"time"
)

// Executor is responsible for executing a command and returning a result with status
type Executor interface {
	ExecuteWithJob(ctx context.Context, jobID string, manager *Manager, cmd Command) ExecutionResult
}

// Worker processes queued jobs in topological order
type Worker struct {
	manager          *Manager
	executor         Executor
	logger           *slog.Logger
	stop             chan struct{}
	done             chan struct{}
	lastPendingCheck time.Time
}

// NewWorker creates a new job worker
func NewWorker(manager *Manager, executor Executor, logger *slog.Logger) *Worker {
	return &Worker{
		manager:  manager,
		executor: executor,
		logger:   logger,
		stop:     make(chan struct{}),
		done:     make(chan struct{}),
	}
}

// Start begins the worker loop in a goroutine
func (w *Worker) Start(ctx context.Context) {
	go w.run(ctx)
}

// Stop signals the worker to stop processing
func (w *Worker) Stop() {
	close(w.stop)
	<-w.done
}

// run is the main worker loop
func (w *Worker) run(ctx context.Context) {
	defer close(w.done)

	ticker := time.NewTicker(1 * time.Second)
	defer ticker.Stop()

	for {
		select {
		case <-w.stop:
			w.logger.Info("worker stopping")
			return
		case <-ctx.Done():
			w.logger.Info("worker context cancelled")
			return
		case <-ticker.C:
			w.processNextJob(ctx)

			// Every 5 seconds, also retry pending jobs that might have been completed externally
			if time.Since(w.lastPendingCheck) > 5*time.Second {
				w.retryPendingJobs(ctx)
				w.lastPendingCheck = time.Now()
			}
		}
	}
}

// processNextJob picks the next runnable job and executes it
func (w *Worker) processNextJob(ctx context.Context) {
	queued, err := w.manager.GetQueued()
	if err != nil {
		w.logger.Error("failed to get queued jobs", "error", err)
		return
	}

	if len(queued) == 0 {
		return
	}

	// Pick the first job (already in topo order)
	job := queued[0]

	// Verify dependencies are satisfied
	if !w.dependenciesSatisfied(job) {
		return
	}

	// Execute the job
	w.executeJob(ctx, job)
}

// dependenciesSatisfied checks if all dependencies of a job are completed
func (w *Worker) dependenciesSatisfied(job *Job) bool {
	for _, depID := range job.DependsOn {
		depJob, err := w.manager.Get(depID)
		if err != nil {
			w.logger.Error("failed to fetch dependency", "job_id", depID, "error", err)
			return false
		}

		// Dependency must be completed or failed (not queued or running)
		if depJob.Status != StatusCompleted && depJob.Status != StatusFailed && depJob.Status != StatusCancelled {
			return false
		}

		// If dependency failed or was cancelled, this job will be auto-cancelled
		if depJob.Status == StatusFailed || depJob.Status == StatusCancelled {
			// Auto-cancel this job as well
			w.autoCancelDueToFailedDep(job.ID, depID, depJob.Status)
			return false
		}
	}

	return true
}

// autoCancelDueToFailedDep marks a job as cancelled due to failed dependency
func (w *Worker) autoCancelDueToFailedDep(jobID, depID string, depStatus JobStatus) {
	errMsg := fmt.Sprintf("dependency %s has status %s", depID, depStatus)
	now := time.Now().UTC()
	if err := w.manager.UpdateStatus(jobID, StatusCancelled, &now, &now, nil, errMsg); err != nil {
		w.logger.Error("failed to auto-cancel job", "job_id", jobID, "error", err)
		return
	}

	if err := w.manager.AppendEvent(jobID, Event{
		Timestamp: time.Now().UTC(),
		Type:      "info",
		Message:   fmt.Sprintf("Cancelled due to dependency failure: %s", depID),
	}); err != nil {
		w.logger.Error("failed to append event", "job_id", jobID, "error", err)
	}

	// Cascade cancellation to dependents
	w.manager.cascadeCancelDependents(jobID)
}

// executeJob runs a single job
func (w *Worker) executeJob(ctx context.Context, job *Job) {
	w.logger.Info("executing job", "job_id", job.ID, "command", job.Command.Type)

	// Mark job as running
	now := time.Now().UTC()
	if err := w.manager.UpdateStatus(job.ID, StatusRunning, &now, nil, nil, ""); err != nil {
		w.logger.Error("failed to mark job as running", "job_id", job.ID, "error", err)
		return
	}

	if err := w.manager.AppendEvent(job.ID, Event{
		Timestamp: time.Now().UTC(),
		Type:      "info",
		Message:   "Job execution started",
	}); err != nil {
		w.logger.Error("failed to append event", "job_id", job.ID, "error", err)
	}

	// Execute the command - it returns its own status
	result := w.executor.ExecuteWithJob(ctx, job.ID, w.manager, job.Command)

	// Mark job with the status returned by the executor
	completedTime := time.Now().UTC()
	var errMsg string

	// If the command failed, log it
	if result.Status == StatusFailed {
		errMsg = result.ErrorMsg
		w.logger.Error("job execution failed", "job_id", job.ID, "error", errMsg)

		if err := w.manager.AppendEvent(job.ID, Event{
			Timestamp: time.Now().UTC(),
			Type:      "error",
			Message:   fmt.Sprintf("Job failed: %s", errMsg),
		}); err != nil {
			w.logger.Error("failed to append event", "job_id", job.ID, "error", err)
		}

		// Cascade cancellation to dependents on failure
		w.manager.cascadeCancelDependents(job.ID)
	} else if result.Status == StatusCompleted {
		w.logger.Info("job execution completed", "job_id", job.ID)

		if err := w.manager.AppendEvent(job.ID, Event{
			Timestamp: time.Now().UTC(),
			Type:      "info",
			Message:   "Job execution completed",
		}); err != nil {
			w.logger.Error("failed to append event", "job_id", job.ID, "error", err)
		}
	} else if result.Status == StatusPending {
		w.logger.Info("job execution pending", "job_id", job.ID)

		if err := w.manager.AppendEvent(job.ID, Event{
			Timestamp: time.Now().UTC(),
			Type:      "info",
			Message:   "Job execution pending (awaiting external completion)",
		}); err != nil {
			w.logger.Error("failed to append event", "job_id", job.ID, "error", err)
		}
	}

	// Update final status - use what the executor returned
	if err := w.manager.UpdateStatus(job.ID, result.Status, &now, &completedTime, result.Result, errMsg); err != nil {
		w.logger.Error("failed to update job status", "job_id", job.ID, "error", err)
	}
}

// retryPendingJobs re-executes pending jobs to check if they've been completed externally
// Only retries jobs whose dependencies are satisfied to maintain ordering
func (w *Worker) retryPendingJobs(ctx context.Context) {
	pending, err := w.manager.GetPending()
	if err != nil {
		w.logger.Error("failed to get pending jobs for retry", "error", err)
		return
	}

	if len(pending) == 0 {
		return
	}

	for _, job := range pending {
		// Only retry disk, mount, and path operations (these can complete externally)
		if job.Command.Type != CmdManageDisk && job.Command.Type != CmdReleaseDisk &&
			job.Command.Type != CmdCreateMount && job.Command.Type != CmdDeleteMount &&
			job.Command.Type != CmdEditPath && job.Command.Type != CmdAddPath && job.Command.Type != CmdDeletePath {
			continue
		}

		// Check if dependencies are satisfied before retrying
		if !w.dependenciesSatisfied(job) {
			continue
		}

		// Check if the job is already completed without re-executing
		result := w.executor.ExecuteWithJob(ctx, job.ID, w.manager, job.Command)

		// Only update status if the result changed from what we have
		if result.Status != StatusPending {
			// Job completed or failed - update status without appending more events
			completedTime := time.Now().UTC()
			var errMsg string

			if result.Status == StatusFailed {
				errMsg = result.ErrorMsg
				w.logger.Info("job completion detected on retry", "job_id", job.ID, "status", "failed")
			} else {
				w.logger.Info("job completion detected on retry", "job_id", job.ID, "status", "completed")
			}

			if err := w.manager.UpdateStatus(job.ID, result.Status, nil, &completedTime, result.Result, errMsg); err != nil {
				w.logger.Error("failed to update job status on retry", "job_id", job.ID, "error", err)
			}
		}
	}
}
