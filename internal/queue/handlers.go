package queue

import (
	"encoding/json"
	"log/slog"
	"net/http"

	"github.com/gorilla/mux"
)

// Handlers handles HTTP requests for the job queue API
type Handlers struct {
	manager *Manager
	logger  *slog.Logger
}

// NewHandlers creates a new queue handlers instance
func NewHandlers(manager *Manager, logger *slog.Logger) *Handlers {
	return &Handlers{
		manager: manager,
		logger:  logger,
	}
}

// EnqueueInstallRequest is the request for enqueueing an install job
type EnqueueInstallRequest struct {
	ModuleID  string   `json:"module_id"`
	Source    string   `json:"source,omitempty"`
	LocalPath string   `json:"local_path,omitempty"`
	DependsOn []string `json:"depends_on,omitempty"`
}

// EnqueueUninstallRequest is the request for enqueueing an uninstall job
type EnqueueUninstallRequest struct {
	ModuleID  string   `json:"module_id"`
	DependsOn []string `json:"depends_on,omitempty"`
}

// EnqueueCreateExposureRequest is the request for enqueueing a create exposure job
type EnqueueCreateExposureRequest struct {
	ExposureID    string   `json:"exposure_id"`
	ModuleID      string   `json:"module_id"`
	Protocol      string   `json:"protocol"`
	Hostname      string   `json:"hostname,omitempty"`
	ContainerPort uint32   `json:"container_port"`
	Tags          []string `json:"tags,omitempty"`
	DependsOn     []string `json:"depends_on,omitempty"`
}

// EnqueueDeleteExposureRequest is the request for enqueueing a delete exposure job
type EnqueueDeleteExposureRequest struct {
	ExposureID string   `json:"exposure_id"`
	DependsOn  []string `json:"depends_on,omitempty"`
}

// EnqueueCreateLinkRequest is the request for enqueueing a create link job
type EnqueueCreateLinkRequest struct {
	LinkID    string                            `json:"link_id"`
	Modules   map[string]map[string]interface{} `json:"modules,omitempty"`
	Tags      []string                          `json:"tags,omitempty"`
	DependsOn []string                          `json:"depends_on,omitempty"`
}

// EnqueueDeleteLinkRequest is the request for enqueueing a delete link job
type EnqueueDeleteLinkRequest struct {
	LinkID    string   `json:"link_id"`
	DependsOn []string `json:"depends_on,omitempty"`
}

// EnqueueInstall handles POST /api/jobs/enqueue_install
// @ID enqueueInstall
// @Summary Enqueue a module installation job
// @Description Enqueue a module installation job with optional dependencies on other jobs
// @Tags jobs
// @Accept json
// @Produce json
// @Param body body EnqueueInstallRequest true "Installation request"
// @Success 201 {object} JobResponse "Job enqueued successfully"
// @Failure 400 {string} string "Bad request"
// @Router /jobs/enqueue_install [post]
func (h *Handlers) EnqueueInstall(w http.ResponseWriter, r *http.Request) {
	var req EnqueueInstallRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "invalid request body", http.StatusBadRequest)
		return
	}

	if req.ModuleID == "" {
		http.Error(w, "module_id is required", http.StatusBadRequest)
		return
	}

	if req.Source == "" && req.LocalPath == "" {
		http.Error(w, "either source or local_path is required", http.StatusBadRequest)
		return
	}

	cmd := Command{
		Type: CmdInstallModule,
		Args: map[string]interface{}{
			"module_id":  req.ModuleID,
			"source":     req.Source,
			"local_path": req.LocalPath,
		},
	}

	jobID, err := h.manager.Enqueue(cmd, req.DependsOn)
	if err != nil {
		h.logger.Error("failed to enqueue install job", "error", err)
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	job, err := h.manager.Get(jobID)
	if err != nil {
		h.logger.Error("failed to fetch enqueued job", "job_id", jobID, "error", err)
		http.Error(w, "failed to fetch job", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusCreated)
	json.NewEncoder(w).Encode(job)
}

// EnqueueUninstall handles POST /api/jobs/enqueue_uninstall
// @ID enqueueUninstall
// @Summary Enqueue a module uninstallation job
// @Description Enqueue a module uninstallation job with optional dependencies on other jobs
// @Tags jobs
// @Accept json
// @Produce json
// @Param body body EnqueueUninstallRequest true "Uninstallation request"
// @Success 201 {object} JobResponse "Job enqueued successfully"
// @Failure 400 {string} string "Bad request"
// @Router /jobs/enqueue_uninstall [post]
func (h *Handlers) EnqueueUninstall(w http.ResponseWriter, r *http.Request) {
	var req EnqueueUninstallRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "invalid request body", http.StatusBadRequest)
		return
	}

	if req.ModuleID == "" {
		http.Error(w, "module_id is required", http.StatusBadRequest)
		return
	}

	cmd := Command{
		Type: CmdUninstallModule,
		Args: map[string]interface{}{
			"module_id": req.ModuleID,
		},
	}

	jobID, err := h.manager.Enqueue(cmd, req.DependsOn)
	if err != nil {
		h.logger.Error("failed to enqueue uninstall job", "error", err)
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	job, err := h.manager.Get(jobID)
	if err != nil {
		h.logger.Error("failed to fetch enqueued job", "job_id", jobID, "error", err)
		http.Error(w, "failed to fetch job", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusCreated)
	json.NewEncoder(w).Encode(job)
}

// EnqueueCreateExposure handles POST /api/jobs/enqueue_create_exposure
// @ID enqueueCreateExposure
// @Summary Enqueue an exposure creation job
// @Description Enqueue an exposure creation job with optional dependencies on other jobs
// @Tags jobs
// @Accept json
// @Produce json
// @Param body body EnqueueCreateExposureRequest true "Create exposure request"
// @Success 201 {object} JobResponse "Job enqueued successfully"
// @Failure 400 {string} string "Bad request"
// @Router /jobs/enqueue_create_exposure [post]
func (h *Handlers) EnqueueCreateExposure(w http.ResponseWriter, r *http.Request) {
	var req EnqueueCreateExposureRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "invalid request body", http.StatusBadRequest)
		return
	}

	if req.ExposureID == "" || req.ModuleID == "" || req.Protocol == "" || req.ContainerPort == 0 {
		http.Error(w, "exposure_id, module_id, protocol, and container_port are required", http.StatusBadRequest)
		return
	}

	cmd := Command{
		Type: CmdCreateExposure,
		Args: map[string]interface{}{
			"exposure_id":    req.ExposureID,
			"module_id":      req.ModuleID,
			"protocol":       req.Protocol,
			"hostname":       req.Hostname,
			"container_port": req.ContainerPort,
			"tags":           req.Tags,
		},
	}

	jobID, err := h.manager.Enqueue(cmd, req.DependsOn)
	if err != nil {
		h.logger.Error("failed to enqueue create exposure job", "error", err)
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	job, err := h.manager.Get(jobID)
	if err != nil {
		h.logger.Error("failed to fetch enqueued job", "job_id", jobID, "error", err)
		http.Error(w, "failed to fetch job", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusCreated)
	json.NewEncoder(w).Encode(job)
}

// EnqueueDeleteExposure handles POST /api/jobs/enqueue_delete_exposure
// @ID enqueueDeleteExposure
// @Summary Enqueue an exposure deletion job
// @Description Enqueue an exposure deletion job with optional dependencies on other jobs
// @Tags jobs
// @Accept json
// @Produce json
// @Param body body EnqueueDeleteExposureRequest true "Delete exposure request"
// @Success 201 {object} JobResponse "Job enqueued successfully"
// @Failure 400 {string} string "Bad request"
// @Router /jobs/enqueue_delete_exposure [post]
func (h *Handlers) EnqueueDeleteExposure(w http.ResponseWriter, r *http.Request) {
	var req EnqueueDeleteExposureRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "invalid request body", http.StatusBadRequest)
		return
	}

	if req.ExposureID == "" {
		http.Error(w, "exposure_id is required", http.StatusBadRequest)
		return
	}

	cmd := Command{
		Type: CmdDeleteExposure,
		Args: map[string]interface{}{
			"exposure_id": req.ExposureID,
		},
	}

	jobID, err := h.manager.Enqueue(cmd, req.DependsOn)
	if err != nil {
		h.logger.Error("failed to enqueue delete exposure job", "error", err)
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	job, err := h.manager.Get(jobID)
	if err != nil {
		h.logger.Error("failed to fetch enqueued job", "job_id", jobID, "error", err)
		http.Error(w, "failed to fetch job", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusCreated)
	json.NewEncoder(w).Encode(job)
}

// EnqueueCreateLink handles POST /api/jobs/enqueue_create_link
// @ID enqueueCreateLink
// @Summary Enqueue a link creation job
// @Description Enqueue a link creation job with optional dependencies on other jobs
// @Tags jobs
// @Accept json
// @Produce json
// @Param body body EnqueueCreateLinkRequest true "Create link request"
// @Success 201 {object} JobResponse "Job enqueued successfully"
// @Failure 400 {string} string "Bad request"
// @Router /jobs/enqueue_create_link [post]
func (h *Handlers) EnqueueCreateLink(w http.ResponseWriter, r *http.Request) {
	var req EnqueueCreateLinkRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "invalid request body", http.StatusBadRequest)
		return
	}

	if req.LinkID == "" {
		http.Error(w, "link_id is required", http.StatusBadRequest)
		return
	}

	// If modules are provided, they must not be empty
	if req.Modules == nil || len(req.Modules) == 0 {
		http.Error(w, "modules is required", http.StatusBadRequest)
		return
	}

	cmd := Command{
		Type: CmdCreateLink,
		Args: map[string]interface{}{
			"link_id": req.LinkID,
			"modules": req.Modules,
			"tags":    req.Tags,
		},
	}

	jobID, err := h.manager.Enqueue(cmd, req.DependsOn)
	if err != nil {
		h.logger.Error("failed to enqueue create link job", "error", err)
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	job, err := h.manager.Get(jobID)
	if err != nil {
		h.logger.Error("failed to fetch enqueued job", "job_id", jobID, "error", err)
		http.Error(w, "failed to fetch job", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusCreated)
	json.NewEncoder(w).Encode(job)
}

// EnqueueDeleteLink handles POST /api/jobs/enqueue_delete_link
// @ID enqueueDeleteLink
// @Summary Enqueue a link deletion job
// @Description Enqueue a link deletion job with optional dependencies on other jobs
// @Tags jobs
// @Accept json
// @Produce json
// @Param body body EnqueueDeleteLinkRequest true "Delete link request"
// @Success 201 {object} JobResponse "Job enqueued successfully"
// @Failure 400 {string} string "Bad request"
// @Router /jobs/enqueue_delete_link [post]
func (h *Handlers) EnqueueDeleteLink(w http.ResponseWriter, r *http.Request) {
	var req EnqueueDeleteLinkRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "invalid request body", http.StatusBadRequest)
		return
	}

	if req.LinkID == "" {
		http.Error(w, "link_id is required", http.StatusBadRequest)
		return
	}

	cmd := Command{
		Type: CmdDeleteLink,
		Args: map[string]interface{}{
			"link_id": req.LinkID,
		},
	}

	jobID, err := h.manager.Enqueue(cmd, req.DependsOn)
	if err != nil {
		h.logger.Error("failed to enqueue delete link job", "error", err)
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	job, err := h.manager.Get(jobID)
	if err != nil {
		h.logger.Error("failed to fetch enqueued job", "job_id", jobID, "error", err)
		http.Error(w, "failed to fetch job", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusCreated)
	json.NewEncoder(w).Encode(job)
}

// GetJob handles GET /jobs/{id}
// @ID getJob
// @Summary Get job details
// @Description Get job details including status and all events
// @Tags jobs
// @Produce json
// @Param id path string true "Job ID"
// @Success 200 {object} JobResponse "Job details"
// @Failure 404 {string} string "Job not found"
// @Router /jobs/{id} [get]
func (h *Handlers) GetJob(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	jobID := vars["id"]
	if jobID == "" {
		http.Error(w, "job id is required", http.StatusBadRequest)
		return
	}

	job, err := h.manager.Get(jobID)
	if err != nil {
		h.logger.Debug("job not found", "job_id", jobID)
		http.Error(w, "job not found", http.StatusNotFound)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(job)
}

// ListJobs handles GET /jobs (returns jobs in topological order)
// @ID listJobs
// @Summary List all jobs
// @Description List all jobs sorted in topological order by dependencies
// @Tags jobs
// @Produce json
// @Success 200 {object} ListJobsResponse "List of jobs"
// @Failure 500 {string} string "Internal server error"
// @Router /jobs [get]
func (h *Handlers) ListJobs(w http.ResponseWriter, r *http.Request) {
	jobs, err := h.manager.ListAllTopoSorted()
	if err != nil {
		h.logger.Error("failed to list jobs", "error", err)
		http.Error(w, "failed to list jobs", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(ListJobsResponse{Jobs: jobs})
}

// CancelJob handles DELETE /jobs/{id}
// @ID cancelJob
// @Summary Cancel a queued job
// @Description Cancel a queued job (only queued jobs can be cancelled). Cascades cancellation to dependent jobs.
// @Tags jobs
// @Produce json
// @Param id path string true "Job ID"
// @Success 200 {object} JobResponse "Job cancelled"
// @Failure 400 {string} string "Cannot cancel job (already running or completed)"
// @Failure 404 {string} string "Job not found"
// @Router /jobs/{id} [delete]
func (h *Handlers) CancelJob(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	jobID := vars["id"]
	if jobID == "" {
		http.Error(w, "job id is required", http.StatusBadRequest)
		return
	}

	if err := h.manager.Cancel(jobID); err != nil {
		h.logger.Debug("failed to cancel job", "job_id", jobID, "error", err)
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	job, err := h.manager.Get(jobID)
	if err != nil {
		h.logger.Error("failed to fetch cancelled job", "job_id", jobID, "error", err)
		http.Error(w, "failed to fetch job", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(job)
}
