package queue

import (
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"reflect"
	"strings"
	"time"

	"zeropoint-agent/internal/catalog"

	"github.com/gorilla/mux"
)

// Handlers handles HTTP requests for the job queue API
type Handlers struct {
	manager      *Manager
	catalogStore *catalog.Store
	bundleStore  interface{} // BundleStoreHandler interface - avoid circular imports
	logger       *slog.Logger
}

// NewHandlers creates a new queue handlers instance
func NewHandlers(manager *Manager, catalogStore *catalog.Store, bundleStore interface{}, logger *slog.Logger) *Handlers {
	return &Handlers{
		manager:      manager,
		catalogStore: catalogStore,
		bundleStore:  bundleStore,
		logger:       logger,
	}
}

// EnqueueFormat handles POST /jobs/enqueue_format (legacy endpoint, kept for backward compatibility)
// @ID enqueueFormat
// @Summary Enqueue a disk format job (legacy)
// @Description DEPRECATED: Use enqueue_manage_disk instead. This endpoint kept for backward compatibility.
// @Tags jobs
// @Accept json
// @Produce json
// @Param body body EnqueueFormatRequest true "Format request"
// @Success 201 {object} JobResponse "Job enqueued successfully (pending reboot)"
// @Failure 400 {string} string "Bad request"
// @Router /jobs/enqueue_format [post]
func (h *Handlers) EnqueueFormat(w http.ResponseWriter, r *http.Request) {
	var req EnqueueFormatRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "invalid request body", http.StatusBadRequest)
		return
	}

	if req.ID == "" {
		http.Error(w, "id is required", http.StatusBadRequest)
		return
	}

	// Require explicit confirmation
	if !req.Confirm {
		http.Error(w, "confirm must be true for destructive operation", http.StatusBadRequest)
		return
	}

	// Cancel any existing manage_disk job for this device ID
	// (the new request supersedes the old one)
	allJobs, err := h.manager.ListAll()
	if err == nil {
		for _, job := range allJobs {
			if job.Command.Type == CmdManageDisk && job.Status == StatusPending {
				if jobID, ok := job.Command.Args["id"].(string); ok && jobID == req.ID {
					// Cancel the old job
					now := time.Now().UTC()
					_ = h.manager.UpdateStatus(job.ID, StatusCancelled, nil, &now, nil, "Superseded by newer format request for same device")
					_ = h.manager.AppendEvent(job.ID, Event{
						Timestamp: time.Now().UTC(),
						Type:      "info",
						Message:   "Cancelled: superseded by newer format request for device " + req.ID,
					})
				}
			}
		}
	}

	// Create a pending manage disk job using the new command type
	args := map[string]interface{}{
		"id":             req.ID,
		"filesystem":     req.Filesystem,
		"label":          req.Label,
		"wipefs":         req.Wipefs,
		"auto_partition": req.AutoPartition,
		"confirm":        req.Confirm,
	}

	// Add optional fields
	if len(req.Luks) > 0 {
		args["luks"] = req.Luks
	}
	if len(req.Lvm) > 0 {
		args["lvm"] = req.Lvm
	}

	cmd := Command{
		Type: CmdManageDisk,
		Args: args,
	}

	jobID, err := h.manager.Enqueue(cmd, req.DependsOn)
	if err != nil {
		h.logger.Error("failed to enqueue format job", "error", err)
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	// Record initial event
	_ = h.manager.AppendEvent(jobID, Event{
		Timestamp: time.Now().UTC(),
		Type:      "step",
		Message:   "Format operation staged for boot-time execution for device " + req.ID,
	})

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

// EnqueueManageDisk handles POST /jobs/enqueue_manage_disk
// @ID enqueueManageDisk
// @Summary Enqueue a disk management job
// @Description Enqueue a disk management operation (add to managed pool) to be executed at boot time. The management will be staged in /etc/zeropoint/disks.pending.ini and executed by the systemd boot service. If a management job already exists for this device ID, it will be cancelled and replaced. Requires `confirm:true`.
// @Tags jobs
// @Accept json
// @Produce json
// @Param body body EnqueueManageDiskRequest true "Manage disk request"
// @Success 201 {object} JobResponse "Job enqueued successfully (pending reboot)"
// @Failure 400 {string} string "Bad request"
// @Router /jobs/enqueue_manage_disk [post]
func (h *Handlers) EnqueueManageDisk(w http.ResponseWriter, r *http.Request) {
	var req EnqueueManageDiskRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "invalid request body", http.StatusBadRequest)
		return
	}

	if req.ID == "" {
		http.Error(w, "id is required", http.StatusBadRequest)
		return
	}

	// Require explicit confirmation only for destructive operations
	if req.Wipefs && !req.Confirm {
		http.Error(w, "confirm must be true for destructive operation", http.StatusBadRequest)
		return
	}

	// Cancel any existing manage_disk job for this device ID
	// (the new request supersedes the old one)
	allJobs, err := h.manager.ListAll()
	if err == nil {
		for _, job := range allJobs {
			if job.Command.Type == CmdManageDisk && job.Status == StatusPending {
				if jobID, ok := job.Command.Args["id"].(string); ok && jobID == req.ID {
					// Cancel the old job
					now := time.Now().UTC()
					_ = h.manager.UpdateStatus(job.ID, StatusCancelled, nil, &now, nil, "Superseded by newer manage request for same device")
					_ = h.manager.AppendEvent(job.ID, Event{
						Timestamp: time.Now().UTC(),
						Type:      "info",
						Message:   "Cancelled: superseded by newer manage request for device " + req.ID,
					})
				}
			}
		}
	}

	// Create a pending manage disk job in the job queue
	args := map[string]interface{}{
		"id":             req.ID,
		"filesystem":     req.Filesystem,
		"label":          req.Label,
		"wipefs":         req.Wipefs,
		"auto_partition": req.AutoPartition,
		"confirm":        req.Confirm,
	}

	// Add optional fields
	if len(req.Luks) > 0 {
		args["luks"] = req.Luks
	}
	if len(req.Lvm) > 0 {
		args["lvm"] = req.Lvm
	}

	cmd := Command{
		Type: CmdManageDisk,
		Args: args,
	}

	jobID, err := h.manager.Enqueue(cmd, req.DependsOn)
	if err != nil {
		h.logger.Error("failed to enqueue manage disk job", "error", err)
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	// Initialize metadata to track if initial status has been emitted
	_ = h.manager.UpdateMetadata(jobID, map[string]interface{}{
		"status_emitted": false,
	})

	// Record initial event
	_ = h.manager.AppendEvent(jobID, Event{
		Timestamp: time.Now().UTC(),
		Type:      "step",
		Message:   "Disk management operation staged for boot-time execution for device " + req.ID,
	})

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

// EnqueueReleaseDisk handles POST /jobs/enqueue_release_disk
// @ID enqueueReleaseDisk
// @Summary Enqueue a disk release job
// @Description Enqueue a disk release operation (remove from managed pool) to be executed at boot time. The release will be staged in /etc/zeropoint/disks.pending.ini and executed by the systemd boot service.
// @Tags jobs
// @Accept json
// @Produce json
// @Param body body EnqueueReleaseDiskRequest true "Release disk request"
// @Success 201 {object} JobResponse "Job enqueued successfully (pending reboot)"
// @Failure 400 {string} string "Bad request"
// @Router /jobs/enqueue_release_disk [post]
func (h *Handlers) EnqueueReleaseDisk(w http.ResponseWriter, r *http.Request) {
	var req EnqueueReleaseDiskRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "invalid request body", http.StatusBadRequest)
		return
	}

	if req.ID == "" {
		http.Error(w, "id is required", http.StatusBadRequest)
		return
	}

	// Create a release disk job in the job queue
	cmd := Command{
		Type: CmdReleaseDisk,
		Args: map[string]interface{}{
			"id": req.ID,
		},
	}

	jobID, err := h.manager.Enqueue(cmd, req.DependsOn)
	if err != nil {
		h.logger.Error("failed to enqueue release disk job", "error", err)
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	// Record initial event
	_ = h.manager.AppendEvent(jobID, Event{
		Timestamp: time.Now().UTC(),
		Type:      "step",
		Message:   "Disk release operation staged for boot-time execution for device " + req.ID,
	})

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

// EnqueueCreateMount handles POST /jobs/enqueue_create_mount
// @ID enqueueCreateMount
// @Summary Enqueue a mount creation job
// @Description Enqueue a mount operation to be executed at boot time. The mount will be staged in /etc/zeropoint/mounts.pending.ini and executed by the systemd boot service.
// @Tags jobs
// @Accept json
// @Produce json
// @Param body body EnqueueCreateMountRequest true "Mount creation request"
// @Success 201 {object} JobResponse "Job enqueued successfully (pending reboot)"
// @Failure 400 {string} string "Bad request"
// @Router /jobs/enqueue_create_mount [post]
func (h *Handlers) EnqueueCreateMount(w http.ResponseWriter, r *http.Request) {
	var req EnqueueCreateMountRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "invalid request body", http.StatusBadRequest)
		return
	}

	// Validate required fields
	if req.MountPoint == "" || req.Disk == "" {
		http.Error(w, "mount_point and disk are required", http.StatusBadRequest)
		return
	}

	// Prevent root mount modification
	if req.MountPoint == "/" {
		http.Error(w, "cannot create or modify root mount point", http.StatusBadRequest)
		return
	}

	jobID, err := h.manager.Enqueue(Command{
		Type: CmdCreateMount,
		Args: map[string]interface{}{
			"mount_point": req.MountPoint,
			"disk":        req.Disk,
			"partition":   req.Partition,
		},
	}, req.DependsOn)

	if err != nil {
		h.logger.Error("failed to enqueue mount creation", "error", err)
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}

	h.logger.Info("mount creation job enqueued", "job_id", jobID)

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

// EnqueueDeleteMount handles POST /jobs/enqueue_delete_mount
// @ID enqueueDeleteMount
// @Summary Enqueue a mount deletion job
// @Description Enqueue a mount deletion to be executed at boot time. The mount removal will be staged in /etc/zeropoint/mounts.pending.ini and executed by the systemd boot service.
// @Tags jobs
// @Accept json
// @Produce json
// @Param body body EnqueueDeleteMountRequest true "Mount deletion request"
// @Success 201 {object} JobResponse "Job enqueued successfully (pending reboot)"
// @Failure 400 {string} string "Bad request"
// @Router /jobs/enqueue_delete_mount [post]
func (h *Handlers) EnqueueDeleteMount(w http.ResponseWriter, r *http.Request) {
	var req EnqueueDeleteMountRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "invalid request body", http.StatusBadRequest)
		return
	}

	// Validate required field
	if req.MountPoint == "" {
		http.Error(w, "mount_point is required", http.StatusBadRequest)
		return
	}

	// Prevent root mount deletion
	if req.MountPoint == "/" {
		http.Error(w, "cannot delete root mount point", http.StatusBadRequest)
		return
	}

	jobID, err := h.manager.Enqueue(Command{
		Type: CmdDeleteMount,
		Args: map[string]interface{}{
			"mount_point": req.MountPoint,
		},
	}, req.DependsOn)

	if err != nil {
		h.logger.Error("failed to enqueue mount deletion", "error", err)
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}

	h.logger.Info("mount deletion job enqueued", "job_id", jobID)

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
// @Router /jobs/enqueue_install_module [post]
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
			"tags":       req.Tags,
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
// @Router /jobs/enqueue_uninstall_module [post]
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
			"tags":      req.Tags,
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
			"tags":        req.Tags,
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
	if len(req.Modules) == 0 {
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
			"tags":    req.Tags,
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

// ListJobs handles GET /jobs (returns jobs in topological order, optionally filtered by status)
// @ID listJobs
// @Summary List all jobs
// @Description List all jobs sorted in topological order by dependencies, optionally filtered by status
// @Tags jobs
// @Produce json
// @Param status query string false "Status filter: all, active, completed, failed, cancelled (default: all)"
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

	// Filter by status if provided
	statusFilter := r.URL.Query().Get("status")
	if statusFilter != "" && statusFilter != "all" {
		filteredJobs := make([]JobResponse, 0)
		for _, job := range jobs {
			if matchesStatusFilterResponse(job, statusFilter) {
				filteredJobs = append(filteredJobs, job)
			}
		}
		jobs = filteredJobs
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(ListJobsResponse{Jobs: jobs})
}

// DeleteJobs handles DELETE /jobs (deletes jobs based on status filter)
// @ID deleteJobs
// @Summary Delete jobs by status filter
// @Description Delete jobs filtered by status. Only allows deletion of completed, failed, or cancelled jobs. Cannot delete active or running jobs for safety.
// @Tags jobs
// @Param status query string false "Status filter: completed, failed, cancelled (default: completed,failed,cancelled). 'all', 'active', 'queued', and 'running' are not allowed"
// @Success 200 {object} map[string]interface{} "Number of jobs deleted"
// @Failure 400 {string} string "Bad request - invalid or unsafe status filter"
// @Failure 500 {string} string "Internal server error"
// @Router /jobs [delete]
func (h *Handlers) DeleteJobs(w http.ResponseWriter, r *http.Request) {
	statusFilter := r.URL.Query().Get("status")
	if statusFilter == "" {
		// Default to deleting completed and failed jobs only
		statusFilter = "completed,failed,cancelled"
	}

	// Prevent deletion of unsafe statuses
	if statusFilter == "all" {
		http.Error(w, "cannot delete all jobs - only completed, failed, or cancelled jobs can be deleted", http.StatusBadRequest)
		return
	}
	if statusFilter == "active" || statusFilter == "running" || statusFilter == "queued" {
		http.Error(w, "cannot delete active, running, or queued jobs - only completed, failed, or cancelled jobs can be deleted", http.StatusBadRequest)
		return
	}

	// Validate that all statuses in the filter are safe (no active, running, or queued)
	statuses := strings.Split(statusFilter, ",")
	for _, status := range statuses {
		status = strings.TrimSpace(status)
		if status == "active" || status == "running" || status == "queued" {
			http.Error(w, "cannot delete active, running, or queued jobs - only completed, failed, or cancelled jobs can be deleted", http.StatusBadRequest)
			return
		}
	}

	jobs, err := h.manager.ListAllTopoSorted()
	if err != nil {
		h.logger.Error("failed to list jobs for deletion", "error", err)
		http.Error(w, "failed to list jobs", http.StatusInternalServerError)
		return
	}

	deletedCount := 0
	for _, job := range jobs {
		if matchesStatusFilterResponse(job, statusFilter) {
			if err := h.manager.Delete(job.ID); err != nil {
				h.logger.Warn("failed to delete job", "job_id", job.ID, "error", err)
			} else {
				deletedCount++
			}
		}
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"deleted": deletedCount,
	})
}

// matchesStatusFilterResponse checks if a job response matches one of the provided status filters
// statusFilter can be comma-separated values like "completed,failed,cancelled"
func matchesStatusFilterResponse(job JobResponse, statusFilter string) bool {
	statuses := strings.Split(statusFilter, ",")
	for _, status := range statuses {
		status = strings.TrimSpace(status)
		switch status {
		case "active":
			if job.Status == StatusQueued || job.Status == StatusRunning {
				return true
			}
		case "completed":
			if job.Status == StatusCompleted {
				return true
			}
		case "failed":
			if job.Status == StatusFailed {
				return true
			}
		case "cancelled":
			if job.Status == StatusCancelled {
				return true
			}
		}
	}
	return false
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

// EnqueueBundleInstall handles POST /api/jobs/enqueue_install_bundle
// @ID enqueueBundleInstall
// @Summary Enqueue a bundle installation meta-job
// @Description Create a meta-job for bundle installation that orchestrates installation of all bundle components
// @Tags jobs
// @Accept json
// @Produce json
// @Param body body EnqueueBundleInstallRequest true "Bundle installation request"
// @Success 201 {object} JobResponse "Bundle job created successfully"
// @Failure 400 {string} string "Bad request"
// @Router /jobs/enqueue_install_bundle [post]
func (h *Handlers) EnqueueBundleInstall(w http.ResponseWriter, r *http.Request) {
	var req EnqueueBundleInstallRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "invalid request body", http.StatusBadRequest)
		return
	}

	if req.BundleName == "" {
		http.Error(w, "bundle_name is required", http.StatusBadRequest)
		return
	}

	// Fetch bundle from catalog
	bundle, err := h.catalogStore.GetBundle(req.BundleName)
	if err != nil {
		http.Error(w, "failed to fetch bundle: "+err.Error(), http.StatusBadRequest)
		return
	}
	if bundle == nil {
		http.Error(w, "bundle not found", http.StatusNotFound)
		return
	}

	var componentJobIDs []string

	// Enqueue install_module jobs for each module in the bundle
	if len(bundle.Modules) > 0 {
		var moduleDeps []string
		for _, moduleName := range bundle.Modules {
			// Fetch module from catalog to get source
			module, err := h.catalogStore.GetModule(moduleName)
			if err != nil {
				http.Error(w, "failed to fetch module: "+err.Error(), http.StatusBadRequest)
				return
			}
			if module == nil {
				http.Error(w, "module not found in catalog: "+moduleName, http.StatusNotFound)
				return
			}

			moduleJobID, err := h.manager.Enqueue(Command{
				Type: CmdInstallModule,
				Args: map[string]interface{}{
					"module_id": moduleName,
					"source":    module.Source,
					"bundle_id": req.BundleName, // Track which bundle this module is for
				},
			}, moduleDeps)
			if err != nil {
				http.Error(w, "failed to enqueue module: "+err.Error(), http.StatusBadRequest)
				return
			}
			componentJobIDs = append(componentJobIDs, moduleJobID)
			// Each subsequent module depends on all previous ones (sequential installation)
			moduleDeps = append(moduleDeps, moduleJobID)
		}
	}

	// Enqueue create_link jobs for each link in the bundle
	if len(bundle.Links) > 0 {
		for linkID, linkConfig := range bundle.Links {
			// Convert bundle link format to module format expected by create_link
			modules := make(map[string]map[string]interface{})
			for _, link := range linkConfig {
				bindMap := make(map[string]interface{})
				for k, v := range link.Bind {
					bindMap[k] = v
				}
				modules[link.Module] = bindMap
			}

			linkJobID, err := h.manager.Enqueue(Command{
				Type: CmdCreateLink,
				Args: map[string]interface{}{
					"link_id":   linkID,
					"modules":   modules,
					"bundle_id": req.BundleName, // Track which bundle this link is for
				},
			}, componentJobIDs)
			if err != nil {
				http.Error(w, "failed to enqueue link: "+err.Error(), http.StatusBadRequest)
				return
			}
			componentJobIDs = append(componentJobIDs, linkJobID)
		}
	}

	// Enqueue create_exposure jobs for each exposure in the bundle
	if len(bundle.Exposures) > 0 {
		for exposureID, exposureConfig := range bundle.Exposures {
			exposureJobID, err := h.manager.Enqueue(Command{
				Type: CmdCreateExposure,
				Args: map[string]interface{}{
					"exposure_id":    exposureID,
					"module_id":      exposureConfig.Module,
					"container_port": uint32(exposureConfig.ModulePort),
					"protocol":       exposureConfig.Protocol,
					"hostname":       exposureID,
					"bundle_id":      req.BundleName, // Track which bundle this exposure is for
				},
			}, componentJobIDs)
			if err != nil {
				http.Error(w, "failed to enqueue exposure: "+err.Error(), http.StatusBadRequest)
				return
			}
			componentJobIDs = append(componentJobIDs, exposureJobID)
		}
	}

	// Create the bundle_install meta-job that depends on all component jobs
	jobID, err := h.manager.Enqueue(Command{
		Type: CmdBundleInstall,
		Args: map[string]interface{}{
			"bundle_id":   req.BundleName,
			"bundle_name": req.BundleName,
		},
	}, componentJobIDs)

	if err != nil {
		h.logger.Debug("failed to enqueue bundle install job", "bundle_name", req.BundleName, "error", err)
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	// Create persistent bundle record with all component details
	if h.bundleStore != nil {
		// Type assert to get the actual BundleStore methods
		if bs, ok := h.bundleStore.(interface {
			CreateBundle(bundleID, bundleName, jobID string) interface{}
			AddModuleComponent(bundleID, moduleID string, status, errMsg string) error
			AddLinkComponent(bundleID, linkID string, status, errMsg string) error
			AddExposureComponent(bundleID, exposureID string, status, errMsg string) error
		}); ok {
			bs.CreateBundle(req.BundleName, bundle.Name, jobID)

			// Add all modules as components
			for _, moduleName := range bundle.Modules {
				_ = bs.AddModuleComponent(req.BundleName, moduleName, "queued", "")
			}

			// Add all links as components
			if bundle.Links != nil {
				for linkID := range bundle.Links {
					_ = bs.AddLinkComponent(req.BundleName, linkID, "queued", "")
				}
			}

			// Add all exposures as components
			if bundle.Exposures != nil {
				for exposureID := range bundle.Exposures {
					_ = bs.AddExposureComponent(req.BundleName, exposureID, "queued", "")
				}
			}
		}
	}

	job, err := h.manager.Get(jobID)
	if err != nil {
		h.logger.Error("failed to fetch enqueued bundle job", "job_id", jobID, "error", err)
		http.Error(w, "failed to fetch job", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusCreated)
	json.NewEncoder(w).Encode(job)
}

// EnqueueBundleUninstall handles POST /api/jobs/enqueue_uninstall_bundle
// @ID enqueueBundleUninstall
// @Summary Enqueue a bundle uninstallation meta-job
// @Description Create a meta-job for bundle uninstallation that orchestrates removal of all bundle components
// @Tags jobs
// @Accept json
// @Produce json
// @Param body body EnqueueBundleUninstallRequest true "Bundle uninstallation request"
// @Success 201 {object} JobResponse "Bundle uninstall job created successfully"
// @Failure 400 {string} string "Bad request"
// @Router /jobs/enqueue_uninstall_bundle [post]
func (h *Handlers) EnqueueBundleUninstall(w http.ResponseWriter, r *http.Request) {
	var req EnqueueBundleUninstallRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "invalid request body", http.StatusBadRequest)
		return
	}

	if req.BundleID == "" {
		http.Error(w, "bundle_id is required", http.StatusBadRequest)
		return
	}

	// Get bundle from bundleStore to find all components
	bundleIface := h.bundleStore.(interface {
		GetBundle(bundleID string) (interface{}, error)
	})

	bundleData, err := bundleIface.GetBundle(req.BundleID)
	if err != nil {
		http.Error(w, "bundle not found: "+err.Error(), http.StatusNotFound)
		return
	}

	// Use reflection to extract components from bundle data
	// The bundleData is a *BundleRecord from the API package
	bundleVal := reflect.ValueOf(bundleData)
	if bundleVal.Kind() == reflect.Ptr {
		bundleVal = bundleVal.Elem()
	}

	if bundleVal.Kind() != reflect.Struct {
		http.Error(w, "invalid bundle data", http.StatusInternalServerError)
		return
	}

	componentsField := bundleVal.FieldByName("Components")
	if !componentsField.IsValid() {
		http.Error(w, "unable to get bundle components", http.StatusInternalServerError)
		return
	}

	var componentJobIDs []string

	// Components is a struct with Exposures, Links, Modules slices
	exposuresField := componentsField.FieldByName("Exposures")
	linksField := componentsField.FieldByName("Links")
	modulesField := componentsField.FieldByName("Modules")

	// Enqueue delete_exposure jobs first (no dependencies)
	if exposuresField.IsValid() && exposuresField.Kind() == reflect.Slice {
		for i := 0; i < exposuresField.Len(); i++ {
			exp := exposuresField.Index(i)
			expID := exp.FieldByName("ID").String()

			exposureJobID, err := h.manager.Enqueue(Command{
				Type: CmdDeleteExposure,
				Args: map[string]interface{}{
					"exposure_id": expID,
					"bundle_id":   req.BundleID,
				},
			}, []string{}) // No dependencies
			if err != nil {
				http.Error(w, "failed to enqueue exposure deletion: "+err.Error(), http.StatusBadRequest)
				return
			}
			componentJobIDs = append(componentJobIDs, exposureJobID)
		}
	}

	// Enqueue delete_link jobs (depend on all exposures being deleted)
	if linksField.IsValid() && linksField.Kind() == reflect.Slice {
		for i := 0; i < linksField.Len(); i++ {
			link := linksField.Index(i)
			linkID := link.FieldByName("ID").String()

			linkJobID, err := h.manager.Enqueue(Command{
				Type: CmdDeleteLink,
				Args: map[string]interface{}{
					"link_id":   linkID,
					"bundle_id": req.BundleID,
				},
			}, componentJobIDs)
			if err != nil {
				http.Error(w, "failed to enqueue link deletion: "+err.Error(), http.StatusBadRequest)
				return
			}
			componentJobIDs = append(componentJobIDs, linkJobID)
		}
	}

	// Enqueue uninstall_module jobs (depend on all links being deleted)
	if modulesField.IsValid() && modulesField.Kind() == reflect.Slice {
		for i := 0; i < modulesField.Len(); i++ {
			mod := modulesField.Index(i)
			modID := mod.FieldByName("ID").String()

			moduleJobID, err := h.manager.Enqueue(Command{
				Type: CmdUninstallModule,
				Args: map[string]interface{}{
					"module_id": modID,
					"bundle_id": req.BundleID,
				},
			}, componentJobIDs)
			if err != nil {
				http.Error(w, "failed to enqueue module uninstall: "+err.Error(), http.StatusBadRequest)
				return
			}
			componentJobIDs = append(componentJobIDs, moduleJobID)
		}
	}

	// Create the bundle_uninstall meta-job that depends on all component jobs
	jobID, err := h.manager.Enqueue(Command{
		Type: CmdBundleUninstall,
		Args: map[string]interface{}{
			"bundle_id": req.BundleID,
		},
	}, componentJobIDs)

	if err != nil {
		h.logger.Debug("failed to enqueue bundle uninstall job", "bundle_id", req.BundleID, "error", err)
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	job, err := h.manager.Get(jobID)
	if err != nil {
		h.logger.Error("failed to fetch enqueued bundle uninstall job", "job_id", jobID, "error", err)
		http.Error(w, "failed to fetch job", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusCreated)
	json.NewEncoder(w).Encode(job)
}

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

// EnqueueCreatePathMount handles POST /jobs/enqueue_create_path (for mount-based paths)
// @ID enqueueCreatePathMount
// @Summary Enqueue a path creation job (mount-based)
// @Description Enqueue a path creation to be executed at boot time. Creates a subdirectory within a mount. The path creation will be staged in /etc/zeropoint/paths.pending.ini and executed by the systemd boot service.
// @Tags jobs
// @Accept json
// @Produce json
// @Param body body EnqueueCreateMountPathRequest true "Path creation request"
// @Success 201 {object} JobResponse "Job enqueued successfully (pending reboot)"
// @Failure 400 {string} string "Bad request"
// @Failure 500 {string} string "Internal server error"
// @Router /jobs/enqueue_create_path [post]
func (h *Handlers) EnqueueCreatePathMount(w http.ResponseWriter, r *http.Request) {
	var req EnqueueCreateMountPathRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "invalid request body", http.StatusBadRequest)
		return
	}

	// Validate required fields
	if req.Mount == "" {
		http.Error(w, "mount is required", http.StatusBadRequest)
		return
	}
	if req.PathSuffix == "" {
		http.Error(w, "path_suffix is required", http.StatusBadRequest)
		return
	}

	// Validate path_suffix
	if err := ValidatePathSuffix(req.PathSuffix); err != nil {
		http.Error(w, fmt.Sprintf("invalid path_suffix: %v", err), http.StatusBadRequest)
		return
	}

	jobID, err := h.manager.Enqueue(Command{
		Type: CmdAddPath,
		Args: map[string]interface{}{
			"mount":       req.Mount,
			"path_suffix": req.PathSuffix,
		},
	}, req.DependsOn)

	if err != nil {
		h.logger.Error("failed to enqueue path creation", "error", err)
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}

	h.logger.Info("path creation job enqueued", "job_id", jobID, "mount", req.Mount, "path_suffix", req.PathSuffix)

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

// EnqueueDeletePathMount handles DELETE path requests for mount-based paths
// @ID enqueueDeletePathMount
// @Summary Enqueue a path deletion job (mount-based)
// @Description Enqueue a path deletion to be executed at boot time. Removes a subdirectory within a mount. The path removal will be staged in /etc/zeropoint/paths.pending.ini and executed by the systemd boot service.
// @Tags jobs
// @Accept json
// @Produce json
// @Param body body EnqueueDeleteMountPathRequest true "Path deletion request"
// @Success 201 {object} JobResponse "Job enqueued successfully (pending reboot)"
// @Failure 400 {string} string "Bad request"
// @Failure 500 {string} string "Internal server error"
// @Router /jobs/enqueue_delete_path [post]
func (h *Handlers) EnqueueDeletePathMount(w http.ResponseWriter, r *http.Request) {
	var req EnqueueDeleteMountPathRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "invalid request body", http.StatusBadRequest)
		return
	}

	// Validate required fields
	if req.Mount == "" {
		http.Error(w, "mount is required", http.StatusBadRequest)
		return
	}
	if req.PathSuffix == "" {
		http.Error(w, "path_suffix is required", http.StatusBadRequest)
		return
	}

	// Validate path_suffix
	if err := ValidatePathSuffix(req.PathSuffix); err != nil {
		http.Error(w, fmt.Sprintf("invalid path_suffix: %v", err), http.StatusBadRequest)
		return
	}

	jobID, err := h.manager.Enqueue(Command{
		Type: CmdDeletePath,
		Args: map[string]interface{}{
			"mount":       req.Mount,
			"path_suffix": req.PathSuffix,
		},
	}, req.DependsOn)

	if err != nil {
		h.logger.Error("failed to enqueue path deletion", "error", err)
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}

	h.logger.Info("path deletion job enqueued", "job_id", jobID, "mount", req.Mount, "path_suffix", req.PathSuffix)

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
