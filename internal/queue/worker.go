package queue

import (
	"context"
	"fmt"
	"log/slog"
	"time"
)

// Executor is responsible for executing a command and returning a result
type Executor interface {
	Execute(ctx context.Context, cmd Command) (interface{}, error)
}

// Worker processes queued jobs in topological order
type Worker struct {
	manager  *Manager
	executor Executor
	logger   *slog.Logger
	stop     chan struct{}
	done     chan struct{}
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

	// Execute the command
	result, execErr := w.executor.Execute(ctx, job.Command)

	// Mark job as completed or failed
	completedTime := time.Now().UTC()
	var status JobStatus
	var errMsg string

	if execErr != nil {
		status = StatusFailed
		errMsg = execErr.Error()
		w.logger.Error("job execution failed", "job_id", job.ID, "error", execErr)

		if err := w.manager.AppendEvent(job.ID, Event{
			Timestamp: time.Now().UTC(),
			Type:      "error",
			Message:   fmt.Sprintf("Job failed: %v", execErr),
		}); err != nil {
			w.logger.Error("failed to append event", "job_id", job.ID, "error", err)
		}

		// Cascade cancellation to dependents
		w.manager.cascadeCancelDependents(job.ID)
	} else {
		status = StatusCompleted
		w.logger.Info("job execution completed", "job_id", job.ID)

		if err := w.manager.AppendEvent(job.ID, Event{
			Timestamp: time.Now().UTC(),
			Type:      "info",
			Message:   "Job execution completed",
		}); err != nil {
			w.logger.Error("failed to append event", "job_id", job.ID, "error", err)
		}
	}

	// Update final status
	if err := w.manager.UpdateStatus(job.ID, status, &now, &completedTime, result, errMsg); err != nil {
		w.logger.Error("failed to update job status", "job_id", job.ID, "error", err)
	}
}
