package queue

import (
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"time"

	"zeropoint-agent/internal/catalog"

	"github.com/gorilla/mux"
	"gopkg.in/ini.v1"
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

// EnqueueInstallRequest is the request for enqueueing an install job
type EnqueueInstallRequest struct {
	ModuleID  string   `json:"module_id"`
	Source    string   `json:"source,omitempty"`
	LocalPath string   `json:"local_path,omitempty"`
	Tags      []string `json:"tags,omitempty"`
	DependsOn []string `json:"depends_on,omitempty"`
}

// EnqueueUninstallRequest is the request for enqueueing an uninstall job
type EnqueueUninstallRequest struct {
	ModuleID  string   `json:"module_id"`
	Tags      []string `json:"tags,omitempty" example:"local-ai-chat"`
	DependsOn []string `json:"depends_on,omitempty" example:"job-1,job-2"`
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
	Tags       []string `json:"tags,omitempty" example:"local-ai-chat"`
	DependsOn  []string `json:"depends_on,omitempty" example:"job-1,job-2"`
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
	Tags      []string `json:"tags,omitempty" example:"local-ai-chat"`
	DependsOn []string `json:"depends_on,omitempty" example:"job-1,job-2"`
}

// EnqueueBundleInstallRequest is the request for creating a bundle installation meta-job.
// The frontend sends only the bundle name; the backend will be extended to automatically
// fetch the bundle definition and enqueue all component jobs. The DependsOn field allows
// chaining multiple bundle installations (e.g., for specialized sequential installs).
type EnqueueBundleInstallRequest struct {
	BundleName string   `json:"bundle_name"`
	DependsOn  []string `json:"depends_on,omitempty"` // For chaining multiple bundle installations
}

// EnqueueBundleUninstallRequest is the request for creating a bundle uninstallation meta-job.
type EnqueueBundleUninstallRequest struct {
	BundleID string `json:"bundle_id"`
}

// EnqueueFormatRequest is the request to format a disk immediately (streams events)
//
// swagger:model EnqueueFormatRequest
type EnqueueFormatRequest struct {
	ID        string   `json:"id"` // Stable device identifier (e.g., usb-SanDisk_Cruzer_4C8F9A1B)
	DependsOn []string `json:"depends_on,omitempty"`
	// PartitionLayout was reserved for future explicit layouts; not implemented currently.
	Filesystem                string                 `json:"filesystem" example:"ext4"`
	Label                     string                 `json:"label,omitempty"`
	Wipefs                    bool                   `json:"wipefs"`
	Luks                      map[string]interface{} `json:"luks,omitempty"`
	Lvm                       map[string]interface{} `json:"lvm,omitempty"`
	Confirm                   bool                   `json:"confirm"`
	ConfirmFixedDiskOperation bool                   `json:"confirm_fixed_disk_operation,omitempty"`
	AutoPartition             bool                   `json:"auto_partition,omitempty"`
}

// EnqueueFormat handles POST /jobs/enqueue_format
// @ID enqueueFormat
// @Summary Enqueue a disk format job
// @Description Enqueue a disk format operation to be executed at boot time. The format will be staged in /etc/zeropoint/storage.pending.ini and executed by the systemd boot service. If a format job already exists for this device ID, it will be cancelled and replaced. Requires `confirm:true`.
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

	// Cancel any existing format_disk job for this device ID
	// (the new request supersedes the old one)
	allJobs, err := h.manager.ListAll()
	if err == nil {
		for _, job := range allJobs {
			if job.Command.Type == CmdFormatDisk && job.Status == StatusQueued {
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

	// Create a pending format job in the job queue
	args := map[string]interface{}{
		"id":         req.ID,
		"filesystem": req.Filesystem,
		"label":      req.Label,
		"status":     "pending",
	}

	cmd := Command{
		Type: CmdFormatDisk,
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

	// Write to /etc/zeropoint/storage.pending.ini
	if err := writeStoragePendingINI(&req, jobID); err != nil {
		h.logger.Error("failed to write storage.pending.ini", "error", err)
		// Still return success for job creation, but record the error
		_ = h.manager.AppendEvent(jobID, Event{
			Timestamp: time.Now().UTC(),
			Type:      "warning",
			Message:   "Failed to write staging file: " + err.Error() + " (systemd service will not execute this operation)",
		})
	} else {
		_ = h.manager.AppendEvent(jobID, Event{
			Timestamp: time.Now().UTC(),
			Type:      "log",
			Message:   "Staged in /etc/zeropoint/storage.pending.ini - will execute on reboot",
		})
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

// writeStoragePendingINI writes a format operation to /etc/zeropoint/storage.pending.ini
// The file format is INI with [id] sections containing all format configuration parameters
// Uses stable device id (e.g., usb-SanDisk_Cruzer_...) as section key since it survives reboots
func writeStoragePendingINI(req *EnqueueFormatRequest, jobID string) error {
	configDir := "/etc/zeropoint"
	configFile := filepath.Join(configDir, "storage.pending.ini")

	// Create config directory if it doesn't exist
	if err := os.MkdirAll(configDir, 0755); err != nil {
		return fmt.Errorf("failed to create config directory: %w", err)
	}

	// Load existing INI or create new
	cfg := ini.Empty()
	if _, err := os.Stat(configFile); err == nil {
		var loadErr error
		cfg, loadErr = ini.Load(configFile)
		if loadErr != nil {
			// If we can't load, start fresh (this overwrites any corrupt file)
			cfg = ini.Empty()
		}
	}

	// Create or update section for this disk (use stable id as section name)
	section, err := cfg.NewSection(req.ID)
	if err != nil {
		// Section might already exist, get it instead
		section, _ = cfg.GetSection(req.ID)
	}

	// Store all configuration parameters
	section.Key("filesystem").SetValue(req.Filesystem)
	if req.Label != "" {
		section.Key("label").SetValue(req.Label)
	}
	if req.Wipefs {
		section.Key("wipefs").SetValue("true")
	}
	if req.AutoPartition {
		section.Key("auto_partition").SetValue("true")
	}
	if req.ConfirmFixedDiskOperation {
		section.Key("confirm_fixed_disk_operation").SetValue("true")
	}
	if len(req.Luks) > 0 {
		luksJSON, _ := json.Marshal(req.Luks)
		section.Key("luks").SetValue(string(luksJSON))
	}
	if len(req.Lvm) > 0 {
		lvmJSON, _ := json.Marshal(req.Lvm)
		section.Key("lvm").SetValue(string(lvmJSON))
	}
	section.Key("request_id").SetValue(jobID)
	section.Key("timestamp").SetValue(time.Now().UTC().Format(time.RFC3339))

	// Write file with restricted permissions
	if err := cfg.SaveTo(configFile); err != nil {
		return fmt.Errorf("failed to write storage.pending.ini: %w", err)
	}

	// Ensure proper file permissions (root-readable only)
	if err := os.Chmod(configFile, 0600); err != nil {
		return fmt.Errorf("failed to set file permissions: %w", err)
	}

	return nil
}

// StorageOperationResult represents a completed format operation from storage.ini
type StorageOperationResult struct {
	DiskID    string
	Status    string // "success" or "error"
	Message   string
	RequestID string
	Timestamp string
}

// readStorageResultsINI reads the /etc/zeropoint/storage.ini file and returns completed operations
// Returns slice of StorageOperationResult for each disk in the file
func readStorageResultsINI() ([]StorageOperationResult, error) {
	configFile := "/etc/zeropoint/storage.ini"

	// If file doesn't exist, no completed operations yet
	if _, err := os.Stat(configFile); os.IsNotExist(err) {
		return []StorageOperationResult{}, nil
	}

	cfg, err := ini.Load(configFile)
	if err != nil {
		return nil, fmt.Errorf("failed to read storage.ini: %w", err)
	}

	var results []StorageOperationResult
	for _, section := range cfg.Sections() {
		if section.Name() == ini.DefaultSection {
			continue
		}

		result := StorageOperationResult{
			DiskID:    section.Name(),
			Status:    section.Key("status").String(),
			Message:   section.Key("message").String(),
			RequestID: section.Key("request_id").String(),
			Timestamp: section.Key("timestamp").String(),
		}
		results = append(results, result)
	}

	return results, nil
}

// readStoragePendingINI reads the /etc/zeropoint/storage.pending.ini file and returns pending operations
func readStoragePendingINI() (map[string]string, error) {
	configFile := "/etc/zeropoint/storage.pending.ini"

	// If file doesn't exist, no pending operations
	if _, err := os.Stat(configFile); os.IsNotExist(err) {
		return make(map[string]string), nil
	}

	cfg, err := ini.Load(configFile)
	if err != nil {
		return nil, fmt.Errorf("failed to read storage.pending.ini: %w", err)
	}

	pending := make(map[string]string)
	for _, section := range cfg.Sections() {
		if section.Name() == ini.DefaultSection {
			continue
		}
		requestID := section.Key("request_id").String()
		if requestID != "" {
			pending[requestID] = section.Name() // map requestID -> diskID
		}
	}

	return pending, nil
}

// ProcessStorageResults processes completed format operations and updates job status
// This is called during agent startup to mark boot-time format jobs as complete/error
// Also logs any operations that are still pending (haven't executed yet)
func (h *Handlers) ProcessStorageResults() error {
	// Get completed results
	results, err := readStorageResultsINI()
	if err != nil {
		h.logger.Error("failed to read storage results", "error", err)
		return err
	}

	completedRequestIDs := make(map[string]bool)

	for _, result := range results {
		if result.RequestID == "" {
			continue
		}

		completedRequestIDs[result.RequestID] = true
		h.logger.Info("processing storage result", "disk_id", result.DiskID, "request_id", result.RequestID, "status", result.Status)

		// Add completion event to job
		eventType := "final"
		if result.Status == "error" {
			eventType = "error"
		}

		event := Event{
			Timestamp: time.Now().UTC(),
			Type:      eventType,
			Message:   fmt.Sprintf("Boot-time format execution: %s", result.Message),
		}

		if err := h.manager.AppendEvent(result.RequestID, event); err != nil {
			h.logger.Error("failed to append completion event", "job_id", result.RequestID, "error", err)
		}

		// Mark job as complete or error using UpdateStatus
		completionTime := time.Now().UTC()
		jobResult := map[string]interface{}{
			"disk_id": result.DiskID,
			"status":  "completed_at_boot",
			"message": result.Message,
		}

		if result.Status == "success" {
			if err := h.manager.UpdateStatus(result.RequestID, StatusCompleted, nil, &completionTime, jobResult, ""); err != nil {
				h.logger.Error("failed to mark job complete", "job_id", result.RequestID, "error", err)
			}
		} else if result.Status == "error" {
			if err := h.manager.UpdateStatus(result.RequestID, StatusFailed, nil, &completionTime, jobResult, result.Message); err != nil {
				h.logger.Error("failed to mark job error", "job_id", result.RequestID, "error", err)
			}
		}
	}

	// Log any operations that are still pending (in storage.pending.ini but not in storage.ini yet)
	pending, err := readStoragePendingINI()
	if err != nil {
		h.logger.Warn("failed to read pending storage operations", "error", err)
	} else if len(pending) > 0 {
		for requestID, diskID := range pending {
			if !completedRequestIDs[requestID] {
				h.logger.Info("format operation still pending (will execute on next reboot)",
					"request_id", requestID, "disk_id", diskID)
			}
		}
	}

	if len(results) == 0 && len(pending) == 0 {
		h.logger.Debug("no boot-time format operations to process")
	}

	return nil
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
	if bundle.Links != nil && len(bundle.Links) > 0 {
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
	if bundle.Exposures != nil && len(bundle.Exposures) > 0 {
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
