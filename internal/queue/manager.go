package queue

import (
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"sort"
	"sync"
	"time"

	"github.com/google/uuid"
)

// Manager handles job enqueueing, tracking, and execution
type Manager struct {
	jobsDir string
	mu      sync.RWMutex
	logger  *slog.Logger
}

// NewManager creates a new job manager
func NewManager(jobsDir string, logger *slog.Logger) (*Manager, error) {
	// Ensure jobs directory exists
	if err := os.MkdirAll(jobsDir, 0755); err != nil {
		return nil, fmt.Errorf("failed to create jobs directory: %w", err)
	}

	return &Manager{
		jobsDir: jobsDir,
		logger:  logger,
	}, nil
}

// jobDir returns the directory for a specific job
func (m *Manager) jobDir(jobID string) string {
	return filepath.Join(m.jobsDir, jobID)
}

// jobFile returns the path to a job's metadata file
func (m *Manager) jobFile(jobID string) string {
	return filepath.Join(m.jobDir(jobID), "job.json")
}

// eventsFile returns the path to a job's events file
func (m *Manager) eventsFile(jobID string) string {
	return filepath.Join(m.jobDir(jobID), "events.jsonl")
}

// Enqueue creates a new job and adds it to the queue
func (m *Manager) Enqueue(cmd Command, dependsOn []string) (string, error) {
	m.mu.Lock()
	defer m.mu.Unlock()

	jobID := uuid.New().String()

	// Validate dependencies exist and are not cycles
	if err := m.validateDependencies(jobID, dependsOn); err != nil {
		return "", err
	}

	// Create job directory
	jobDirPath := m.jobDir(jobID)
	if err := os.MkdirAll(jobDirPath, 0755); err != nil {
		return "", fmt.Errorf("failed to create job directory: %w", err)
	}

	// Extract tags from command args if present
	var tags []string
	if tagsInterface, ok := cmd.Args["tags"]; ok {
		if tagsList, ok := tagsInterface.([]interface{}); ok {
			for _, t := range tagsList {
				if tagStr, ok := t.(string); ok {
					tags = append(tags, tagStr)
				}
			}
		}
	}

	// Create job metadata
	job := &Job{
		ID:        jobID,
		Status:    StatusQueued,
		Command:   cmd,
		DependsOn: dependsOn,
		Tags:      tags,
		CreatedAt: time.Now().UTC(),
	}

	// Write job metadata
	if err := m.writeJobMetadata(job); err != nil {
		return "", err
	}

	// Append initial event
	if err := m.appendEvent(jobID, Event{
		Timestamp: time.Now().UTC(),
		Type:      "info",
		Message:   "Job enqueued",
	}); err != nil {
		return "", err
	}

	m.logger.Info("job enqueued", "job_id", jobID, "command", cmd.Type, "depends_on", dependsOn)

	return jobID, nil
}

// validateDependencies checks that all dependencies exist and no cycles are created
func (m *Manager) validateDependencies(jobID string, dependsOn []string) error {
	seen := make(map[string]bool)
	for _, depID := range dependsOn {
		if depID == jobID {
			return fmt.Errorf("job cannot depend on itself")
		}

		// Check if dependency exists
		job, err := m.getJob(depID)
		if err != nil {
			return fmt.Errorf("dependency job '%s' not found: %w", depID, err)
		}

		// Simple cycle detection: if a job depends on this one, reject
		if err := m.checkCycle(jobID, job.DependsOn); err != nil {
			return err
		}

		if seen[depID] {
			return fmt.Errorf("duplicate dependency: %s", depID)
		}
		seen[depID] = true
	}

	return nil
}

// checkCycle recursively checks for circular dependencies
func (m *Manager) checkCycle(jobID string, deps []string) error {
	for _, depID := range deps {
		if depID == jobID {
			return fmt.Errorf("circular dependency detected")
		}

		job, err := m.getJob(depID)
		if err == nil && len(job.DependsOn) > 0 {
			if err := m.checkCycle(jobID, job.DependsOn); err != nil {
				return err
			}
		}
	}

	return nil
}

// Get retrieves a job by ID
func (m *Manager) Get(jobID string) (*JobResponse, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()

	job, err := m.getJob(jobID)
	if err != nil {
		return nil, err
	}

	events, err := m.getEvents(jobID)
	if err != nil {
		return nil, err
	}

	return &JobResponse{
		ID:          job.ID,
		Status:      job.Status,
		Command:     job.Command,
		DependsOn:   job.DependsOn,
		Tags:        job.Tags,
		CreatedAt:   job.CreatedAt,
		StartedAt:   job.StartedAt,
		CompletedAt: job.CompletedAt,
		Result:      job.Result,
		Error:       job.Error,
		Events:      events,
	}, nil
}

// GetJobMetadata retrieves a job's metadata field
func (m *Manager) GetJobMetadata(jobID string) (map[string]interface{}, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()

	job, err := m.getJob(jobID)
	if err != nil {
		return nil, err
	}

	if job.Metadata == nil {
		return make(map[string]interface{}), nil
	}

	return job.Metadata, nil
}

// getJob is an internal method that reads job metadata without locking (caller must lock)
func (m *Manager) getJob(jobID string) (*Job, error) {
	jobPath := m.jobFile(jobID)
	data, err := os.ReadFile(jobPath)
	if err != nil {
		return nil, fmt.Errorf("job not found: %w", err)
	}

	var job Job
	if err := json.Unmarshal(data, &job); err != nil {
		return nil, fmt.Errorf("failed to unmarshal job: %w", err)
	}

	return &job, nil
}

// getEvents reads all events for a job (caller must handle locking if needed)
func (m *Manager) getEvents(jobID string) ([]Event, error) {
	eventsPath := m.eventsFile(jobID)
	file, err := os.Open(eventsPath)
	if err != nil {
		if os.IsNotExist(err) {
			return []Event{}, nil
		}
		return nil, fmt.Errorf("failed to read events: %w", err)
	}
	defer file.Close()

	var events []Event
	dec := json.NewDecoder(file)
	for {
		var event Event
		err := dec.Decode(&event)
		if err == io.EOF {
			break
		}
		if err != nil {
			return nil, fmt.Errorf("failed to decode event: %w", err)
		}
		events = append(events, event)
	}

	return events, nil
}

// ListAll returns all jobs
func (m *Manager) ListAll() ([]JobResponse, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()

	entries, err := os.ReadDir(m.jobsDir)
	if err != nil {
		return nil, fmt.Errorf("failed to read jobs directory: %w", err)
	}

	var jobs []JobResponse
	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}

		jobID := entry.Name()
		job, err := m.getJob(jobID)
		if err != nil {
			m.logger.Error("failed to read job", "job_id", jobID, "error", err)
			continue
		}

		events, err := m.getEvents(jobID)
		if err != nil {
			m.logger.Error("failed to read job events", "job_id", jobID, "error", err)
			events = []Event{}
		}

		jobs = append(jobs, JobResponse{
			ID:          job.ID,
			Status:      job.Status,
			Command:     job.Command,
			DependsOn:   job.DependsOn,
			Tags:        job.Tags,
			CreatedAt:   job.CreatedAt,
			StartedAt:   job.StartedAt,
			CompletedAt: job.CompletedAt,
			Result:      job.Result,
			Error:       job.Error,
			Events:      events,
		})
	}

	// Sort by created time, newest first
	sort.Slice(jobs, func(i, j int) bool {
		return jobs[i].CreatedAt.After(jobs[j].CreatedAt)
	})

	return jobs, nil
}

// ListAllTopoSorted returns all jobs sorted in topological order
func (m *Manager) ListAllTopoSorted() ([]JobResponse, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()

	entries, err := os.ReadDir(m.jobsDir)
	if err != nil {
		return nil, fmt.Errorf("failed to read jobs directory: %w", err)
	}

	var jobs []*Job
	jobMap := make(map[string]*Job)

	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}

		jobID := entry.Name()
		job, err := m.getJob(jobID)
		if err != nil {
			m.logger.Error("failed to read job", "job_id", jobID, "error", err)
			continue
		}

		jobs = append(jobs, job)
		jobMap[jobID] = job
	}

	// Topological sort
	sorted := m.topoSort(jobs, jobMap)

	// Convert to JobResponse with events
	var responses []JobResponse
	for _, job := range sorted {
		events, err := m.getEvents(job.ID)
		if err != nil {
			m.logger.Error("failed to read job events", "job_id", job.ID, "error", err)
			events = []Event{}
		}

		responses = append(responses, JobResponse{
			ID:          job.ID,
			Status:      job.Status,
			Command:     job.Command,
			DependsOn:   job.DependsOn,
			Tags:        job.Tags,
			CreatedAt:   job.CreatedAt,
			StartedAt:   job.StartedAt,
			CompletedAt: job.CompletedAt,
			Result:      job.Result,
			Error:       job.Error,
			Events:      events,
		})
	}

	return responses, nil
}

// Cancel cancels a queued job and all its dependents
func (m *Manager) Cancel(jobID string) error {
	m.mu.Lock()
	defer m.mu.Unlock()

	job, err := m.getJob(jobID)
	if err != nil {
		return fmt.Errorf("job not found: %w", err)
	}

	if job.Status != StatusQueued {
		return fmt.Errorf("can only cancel queued jobs; job status is %s", job.Status)
	}

	// Mark job as cancelled
	job.Status = StatusCancelled
	job.Error = "cancelled by user"
	now := time.Now().UTC()
	job.CompletedAt = &now

	if err := m.writeJobMetadata(job); err != nil {
		return err
	}

	if err := m.appendEvent(jobID, Event{
		Timestamp: time.Now().UTC(),
		Type:      "info",
		Message:   "Job cancelled by user",
	}); err != nil {
		return err
	}

	// Cascade cancellation to all dependent jobs
	m.cascadeCancelDependents(jobID)

	m.logger.Info("job cancelled", "job_id", jobID)

	return nil
}

// cascadeCancelDependents recursively cancels all jobs that depend on jobID
func (m *Manager) cascadeCancelDependents(jobID string) {
	entries, err := os.ReadDir(m.jobsDir)
	if err != nil {
		m.logger.Error("failed to read jobs directory for cascade", "error", err)
		return
	}

	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}

		depJobID := entry.Name()
		depJob, err := m.getJob(depJobID)
		if err != nil {
			continue
		}

		// Check if this job depends on the cancelled job
		for _, dep := range depJob.DependsOn {
			if dep == jobID && depJob.Status == StatusQueued {
				// Cancel this job
				depJob.Status = StatusCancelled
				depJob.Error = fmt.Sprintf("dependency cancelled: %s", jobID)
				now := time.Now().UTC()
				depJob.CompletedAt = &now

				if err := m.writeJobMetadata(depJob); err != nil {
					m.logger.Error("failed to write job metadata during cascade", "job_id", depJobID, "error", err)
					continue
				}

				if err := m.appendEvent(depJobID, Event{
					Timestamp: time.Now().UTC(),
					Type:      "info",
					Message:   fmt.Sprintf("Job cancelled due to dependency cancellation: %s", jobID),
				}); err != nil {
					m.logger.Error("failed to append event during cascade", "job_id", depJobID, "error", err)
				}

				m.logger.Info("job cascade cancelled", "job_id", depJobID, "due_to", jobID)

				// Recursively cancel its dependents
				m.cascadeCancelDependents(depJobID)
				break
			}
		}
	}
}

// Delete deletes a job (only if not running)
func (m *Manager) Delete(jobID string) error {
	m.mu.Lock()
	defer m.mu.Unlock()

	job, err := m.getJob(jobID)
	if err != nil {
		return fmt.Errorf("job not found: %w", err)
	}

	if job.Status == StatusRunning {
		return fmt.Errorf("cannot delete a running job")
	}

	// Delete job directory
	jobDirPath := m.jobDir(jobID)
	if err := os.RemoveAll(jobDirPath); err != nil {
		return fmt.Errorf("failed to delete job directory: %w", err)
	}

	m.logger.Info("job deleted", "job_id", jobID)

	return nil
}

// GetQueued returns all queued jobs in topological order
func (m *Manager) GetQueued() ([]*Job, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()

	entries, err := os.ReadDir(m.jobsDir)
	if err != nil {
		return nil, fmt.Errorf("failed to read jobs directory: %w", err)
	}

	var jobs []*Job
	jobMap := make(map[string]*Job)

	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}

		jobID := entry.Name()
		job, err := m.getJob(jobID)
		if err != nil {
			m.logger.Error("failed to read job", "job_id", jobID, "error", err)
			continue
		}

		if job.Status == StatusQueued {
			jobs = append(jobs, job)
			jobMap[jobID] = job
		}
	}

	// Topological sort
	sorted := m.topoSort(jobs, jobMap)
	return sorted, nil
}

// topoSort performs a topological sort on queued jobs
func (m *Manager) topoSort(jobs []*Job, jobMap map[string]*Job) []*Job {
	// Build in-degree map - only count dependencies that are still queued
	inDegree := make(map[string]int)
	for _, job := range jobs {
		if _, exists := inDegree[job.ID]; !exists {
			inDegree[job.ID] = 0
		}
		// Only count dependencies that are also in the queued jobs list
		for _, dep := range job.DependsOn {
			if _, inQueued := jobMap[dep]; inQueued {
				inDegree[job.ID]++
			}
		}
	}

	// Find all jobs with in-degree 0
	queue := []*Job{}
	for _, job := range jobs {
		if inDegree[job.ID] == 0 {
			queue = append(queue, job)
		}
	}

	var sorted []*Job
	for len(queue) > 0 {
		job := queue[0]
		queue = queue[1:]
		sorted = append(sorted, job)

		// Find jobs that depend on this one
		for _, other := range jobs {
			for _, dep := range other.DependsOn {
				if dep == job.ID {
					inDegree[other.ID]--
					if inDegree[other.ID] == 0 {
						queue = append(queue, other)
					}
				}
			}
		}
	}

	return sorted
}

// GetPending returns all pending jobs (jobs awaiting external completion)
func (m *Manager) GetPending() ([]*Job, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()

	entries, err := os.ReadDir(m.jobsDir)
	if err != nil {
		return nil, fmt.Errorf("failed to read jobs directory: %w", err)
	}

	var jobs []*Job

	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}

		jobID := entry.Name()
		job, err := m.getJob(jobID)
		if err != nil {
			m.logger.Error("failed to read job", "job_id", jobID, "error", err)
			continue
		}

		if job.Status == StatusPending {
			jobs = append(jobs, job)
		}
	}

	return jobs, nil
}

// UpdateStatus updates a job's status and writes to disk
func (m *Manager) UpdateStatus(jobID string, status JobStatus, startedAt, completedAt *time.Time, result interface{}, errMsg string) error {
	m.mu.Lock()
	defer m.mu.Unlock()

	job, err := m.getJob(jobID)
	if err != nil {
		return err
	}

	job.Status = status
	job.StartedAt = startedAt
	job.CompletedAt = completedAt
	job.Result = result
	job.Error = errMsg

	return m.writeJobMetadata(job)
}

// UpdateDependencies updates a job's DependsOn field
func (m *Manager) UpdateDependencies(jobID string, dependsOn []string) error {
	m.mu.Lock()
	defer m.mu.Unlock()

	job, err := m.getJob(jobID)
	if err != nil {
		return err
	}

	job.DependsOn = dependsOn
	return m.writeJobMetadata(job)
}

// UpdateMetadata updates a job's metadata field
func (m *Manager) UpdateMetadata(jobID string, metadata map[string]interface{}) error {
	m.mu.Lock()
	defer m.mu.Unlock()

	job, err := m.getJob(jobID)
	if err != nil {
		return err
	}

	if job.Metadata == nil {
		job.Metadata = make(map[string]interface{})
	}
	for k, v := range metadata {
		job.Metadata[k] = v
	}
	return m.writeJobMetadata(job)
}

// AppendEvent appends an event to a job's event log
func (m *Manager) AppendEvent(jobID string, event Event) error {
	m.mu.Lock()
	defer m.mu.Unlock()

	return m.appendEvent(jobID, event)
}

// appendEvent is an internal method (caller must handle locking)
func (m *Manager) appendEvent(jobID string, event Event) error {
	eventsPath := m.eventsFile(jobID)

	// Ensure events file exists
	if err := os.MkdirAll(filepath.Dir(eventsPath), 0755); err != nil {
		return err
	}

	file, err := os.OpenFile(eventsPath, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0644)
	if err != nil {
		return fmt.Errorf("failed to open events file: %w", err)
	}
	defer file.Close()

	// Write event as JSON line
	data, err := json.Marshal(event)
	if err != nil {
		return fmt.Errorf("failed to marshal event: %w", err)
	}

	_, err = file.Write(append(data, '\n'))
	return err
}

// writeJobMetadata writes job metadata to disk (caller must handle locking)
func (m *Manager) writeJobMetadata(job *Job) error {
	jobPath := m.jobFile(job.ID)

	// Ensure job directory exists
	if err := os.MkdirAll(filepath.Dir(jobPath), 0755); err != nil {
		return err
	}

	data, err := json.MarshalIndent(job, "", "  ")
	if err != nil {
		return fmt.Errorf("failed to marshal job: %w", err)
	}

	// Write atomically: write to temp, then rename
	tmpPath := jobPath + ".tmp"
	if err := os.WriteFile(tmpPath, data, 0644); err != nil {
		return fmt.Errorf("failed to write job file: %w", err)
	}

	if err := os.Rename(tmpPath, jobPath); err != nil {
		return fmt.Errorf("failed to rename job file: %w", err)
	}

	return nil
}
