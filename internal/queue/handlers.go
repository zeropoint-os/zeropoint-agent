package queue

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"os/exec"
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
	DiskID    string   `json:"disk_id"`
	SysPath   string   `json:"sys_path"`
	ByID      string   `json:"by_id,omitempty"`
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
// @Description Enqueue a disk format operation to be executed by the job worker. Progress will be recorded to job events. Requires `confirm:true` and the same safety rules as the immediate formatter.
// @Tags jobs
// @Accept json
// @Produce json
// @Param body body EnqueueFormatRequest true "Format request"
// @Success 201 {object} JobResponse "Job enqueued successfully"
// @Failure 400 {string} string "Bad request"
// @Router /jobs/enqueue_format [post]
func (h *Handlers) EnqueueFormat(w http.ResponseWriter, r *http.Request) {
	var req EnqueueFormatRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "invalid request body", http.StatusBadRequest)
		return
	}

	if req.SysPath == "" {
		http.Error(w, "sys_path is required", http.StatusBadRequest)
		return
	}

	// Require explicit confirmation
	if !req.Confirm {
		http.Error(w, "confirm must be true for destructive operation", http.StatusBadRequest)
		return
	}

	// Build command args map
	args := map[string]interface{}{
		"disk_id":                      req.DiskID,
		"sys_path":                     req.SysPath,
		"by_id":                        req.ByID,
		"filesystem":                   req.Filesystem,
		"label":                        req.Label,
		"wipefs":                       req.Wipefs,
		"luks":                         req.Luks,
		"lvm":                          req.Lvm,
		"confirm":                      req.Confirm,
		"confirm_fixed_disk_operation": req.ConfirmFixedDiskOperation,
		"auto_partition":               req.AutoPartition,
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

// helper to stream SSE event
func sseEvent(w http.ResponseWriter, kind, msg string) error {
	_, err := io.WriteString(w, "event: job:event\n")
	if err != nil {
		return err
	}
	payload := map[string]string{"kind": kind, "msg": msg}
	b, _ := json.Marshal(payload)
	_, err = io.WriteString(w, "data: "+string(b)+"\n\n")
	if err != nil {
		return err
	}
	if f, ok := w.(http.Flusher); ok {
		f.Flush()
	}
	return nil
}

// runCmdStream runs a command and streams stdout/stderr as SSE logs
func runCmdStream(ctx context.Context, w http.ResponseWriter, name string, args ...string) error {
	cmd := exec.CommandContext(ctx, name, args...)
	stdout, _ := cmd.StdoutPipe()
	stderr, _ := cmd.StderrPipe()
	if err := cmd.Start(); err != nil {
		sseEvent(w, "error", "failed to start command: "+err.Error())
		return err
	}

	stream := func(r io.Reader, streamName string) {
		scanner := bufio.NewScanner(r)
		for scanner.Scan() {
			sseEvent(w, "log", streamName+": "+scanner.Text())
		}
	}
	go stream(stdout, "stdout")
	go stream(stderr, "stderr")

	if err := cmd.Wait(); err != nil {
		sseEvent(w, "error", "command failed: "+err.Error())
		return err
	}
	return nil
}

// FormatNow handles POST /api/storage/disks/format (immediate formatting with streaming)
// @ID formatDiskNow
// @Summary Format disk or partition now (streams events)
// @Description Immediately run destructive formatting operations on a disk or partition. Streams progress as server-sent events (text/event-stream). Requires `confirm:true`. If targeting a whole-disk, include `auto_partition:true` to create a single GPT partition.
// @Tags storage
// @Accept json
// @Produce text/event-stream
// @Param body body EnqueueFormatRequest true "Format request"
// @Success 200 {string} string "Event stream"
// @Failure 400 {object} map[string]string
// @Router /api/storage/disks/format [post]
// Note: This endpoint performs destructive operations. Use carefully.
func (h *Handlers) FormatNow(w http.ResponseWriter, r *http.Request) {
	var req EnqueueFormatRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "invalid request body", http.StatusBadRequest)
		return
	}

	if req.SysPath == "" {
		http.Error(w, "sys_path is required", http.StatusBadRequest)
		return
	}

	// Require explicit confirmation for destructive ops
	if !req.Confirm {
		http.Error(w, "confirm must be true for destructive operation", http.StatusBadRequest)
		return
	}

	// If device transport is not USB, require an additional explicit confirmation
	// to avoid accidental formatting of fixed/internal drives.
	// Query lsblk for transport of the device
	transport := ""
	if req.SysPath != "" {
		ctxT, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		cmd := exec.CommandContext(ctxT, "lsblk", "-dn", "-o", "TRAN", req.SysPath)
		out, err := cmd.Output()
		if err == nil {
			transport = strings.TrimSpace(string(out))
		}
	}

	if transport != "usb" && transport != "" && !req.ConfirmFixedDiskOperation {
		http.Error(w, "confirm_fixed_disk_operation must be true for non-USB devices", http.StatusBadRequest)
		return
	}

	// Determine device type (disk vs partition). If the path is a whole disk and
	// neither auto-partition nor explicit partition_layout are provided, refuse.
	devType := ""
	{
		ctxT, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		defer cancel()
		cmd := exec.CommandContext(ctxT, "lsblk", "-dn", "-o", "TYPE", req.SysPath)
		out, err := cmd.Output()
		if err != nil {
			http.Error(w, "unable to determine device type or device not found", http.StatusBadRequest)
			return
		}
		devType = strings.TrimSpace(string(out))
		if devType == "" {
			http.Error(w, "unable to determine device type for path: "+req.SysPath, http.StatusBadRequest)
			return
		}
	}

	if devType == "disk" && !req.AutoPartition {
		http.Error(w, "refusing to format whole-disk without explicit partition intent (auto_partition)", http.StatusBadRequest)
		return
	}

	// Setup SSE
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	if f, ok := w.(http.Flusher); ok {
		f.Flush()
	}

	ctx := r.Context()

	// Step 1: dry-run wipefs
	sseEvent(w, "step", "dry-run: wipefs -n")
	if err := runCmdStream(ctx, w, "wipefs", "-n", req.SysPath); err != nil {
		sseEvent(w, "final", "failed dry-run: "+err.Error())
		return
	}

	// Step 2: perform wipefs
	if req.Wipefs {
		sseEvent(w, "step", "running: wipefs -a")
		if err := runCmdStream(ctx, w, "wipefs", "-a", req.SysPath); err != nil {
			sseEvent(w, "final", "wipefs failed: "+err.Error())
			return
		}
	}
	// If auto-partition requested and we're operating on a whole disk, create a single GPT partition
	targetPath := req.SysPath
	if req.AutoPartition && devType == "disk" {
		sseEvent(w, "step", "creating GPT partition table (zap)")
		if err := runCmdStream(ctx, w, "sgdisk", "--zap-all", req.SysPath); err != nil {
			sseEvent(w, "final", "sgdisk zap failed: "+err.Error())
			return
		}

		sseEvent(w, "step", "creating single partition to fill disk")
		if err := runCmdStream(ctx, w, "sgdisk", "--new=1:1MiB:0", "--typecode=1:8300", req.SysPath); err != nil {
			sseEvent(w, "final", "sgdisk create failed: "+err.Error())
			return
		}

		// Inform kernel and udev
		_ = runCmdStream(ctx, w, "partprobe", req.SysPath)
		_ = runCmdStream(ctx, w, "udevadm", "settle")

		// Detect first partition device
		var partPath string
		found := false
		for i := 0; i < 10; i++ {
			ctxT, cancel := context.WithTimeout(context.Background(), 2*time.Second)
			cmd := exec.CommandContext(ctxT, "lsblk", "-ln", "-o", "KNAME,TYPE", req.SysPath)
			out, _ := cmd.Output()
			cancel()
			lines := strings.Split(strings.TrimSpace(string(out)), "\n")
			for _, ln := range lines {
				f := strings.Fields(ln)
				if len(f) >= 2 && f[1] == "part" {
					partPath = "/dev/" + f[0]
					found = true
					break
				}
			}
			if found {
				break
			}
			time.Sleep(500 * time.Millisecond)
		}
		if !found {
			sseEvent(w, "final", "failed to detect created partition device")
			return
		}
		sseEvent(w, "step", "detected partition: "+partPath)
		targetPath = partPath
	}

	// Step 3: mkfs on targetPath
	fs := req.Filesystem
	if fs == "" {
		fs = "ext4"
	}
	sseEvent(w, "step", "mkfs: "+fs+" on "+targetPath)
	if fs == "ext4" {
		if err := runCmdStream(ctx, w, "mkfs.ext4", "-F", "-L", req.Label, targetPath); err != nil {
			sseEvent(w, "final", "mkfs failed: "+err.Error())
			return
		}
	} else if fs == "xfs" {
		if err := runCmdStream(ctx, w, "mkfs.xfs", "-f", "-L", req.Label, targetPath); err != nil {
			sseEvent(w, "final", "mkfs failed: "+err.Error())
			return
		}
	} else {
		sseEvent(w, "final", "unsupported filesystem: "+fs)
		return
	}

	// Step 4: report blkid on the target path and include device + UUID in final event
	sseEvent(w, "step", "reading blkid on "+targetPath)
	// Attempt to read UUID
	blkidOut := ""
	if out, err := exec.CommandContext(ctx, "blkid", "-s", "UUID", "-o", "value", targetPath).Output(); err == nil {
		blkidOut = strings.TrimSpace(string(out))
	}

	finalMsg := fmt.Sprintf("{\"device\": \"%s\", \"uuid\": \"%s\"}", targetPath, blkidOut)
	sseEvent(w, "final", finalMsg)
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
