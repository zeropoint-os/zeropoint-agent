package queue

import (
	"encoding/json"
	"log/slog"
	"net/http"
	"reflect"

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
