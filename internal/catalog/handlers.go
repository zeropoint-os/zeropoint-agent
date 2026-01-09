package catalog

import (
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"strconv"
	"time"

	"zeropoint-agent/internal/modules"

	"github.com/gorilla/mux"
)

type (
	InstallRequest = modules.InstallRequest
)

// Handlers provides HTTP handlers for catalog operations
type Handlers struct {
	store    *Store
	resolver *Resolver
	logger   *slog.Logger
}

// NewHandlers creates new catalog handlers
func NewHandlers(store *Store, resolver *Resolver, logger *slog.Logger) *Handlers {
	return &Handlers{
		store:    store,
		resolver: resolver,
		logger:   logger,
	}
}

// HandleUpdateCatalog handles POST /catalogs/update
// @Summary Update catalog
// @Description Updates the local catalog by cloning/pulling from the remote repository
// @Tags catalog
// @Produce json
// @Success 200 {object} UpdateResponse "Catalog updated successfully"
// @Failure 500 {string} string "Internal server error"
// @Router /catalogs/update [post]
func (h *Handlers) HandleUpdateCatalog(w http.ResponseWriter, r *http.Request) {
	h.logger.Info("updating catalog via API")

	if err := h.store.Update(); err != nil {
		h.logger.Error("failed to update catalog", "error", err)
		http.Error(w, fmt.Sprintf("Failed to update catalog: %v", err), http.StatusInternalServerError)
		return
	}

	moduleCount, bundleCount, err := h.store.GetStats()
	if err != nil {
		h.logger.Error("failed to get catalog stats", "error", err)
		http.Error(w, fmt.Sprintf("Failed to get catalog stats: %v", err), http.StatusInternalServerError)
		return
	}

	response := UpdateResponse{
		Status:      "success",
		Message:     "Catalog updated successfully",
		ModuleCount: moduleCount,
		BundleCount: bundleCount,
		Timestamp:   time.Now(),
	}

	w.Header().Set("Content-Type", "application/json")
	if err := json.NewEncoder(w).Encode(response); err != nil {
		h.logger.Error("failed to encode response", "error", err)
		http.Error(w, "Failed to encode response", http.StatusInternalServerError)
	}
}

// HandleListModules handles GET /catalogs/modules
// @Summary List catalog modules
// @Description Returns all available modules from the catalog with their metadata
// @Tags catalog
// @Produce json
// @Param limit query int false "Maximum number of modules to return" default(50)
// @Success 200 {array} ModuleResponse "List of modules with metadata and install requests"
// @Failure 500 {string} string "Internal server error"
// @Router /catalogs/modules [get]
func (h *Handlers) HandleListModules(w http.ResponseWriter, r *http.Request) {
	h.logger.Info("listing catalog modules")

	// Parse query parameters
	limit := 50 // default
	if limitStr := r.URL.Query().Get("limit"); limitStr != "" {
		if parsedLimit, err := strconv.Atoi(limitStr); err == nil && parsedLimit > 0 {
			limit = parsedLimit
		}
	}

	modules, err := h.store.GetModules()
	if err != nil {
		h.logger.Error("failed to get modules", "error", err)
		http.Error(w, fmt.Sprintf("Failed to get modules: %v", err), http.StatusInternalServerError)
		return
	}

	// Apply limit
	if len(modules) > limit {
		modules = modules[:limit]
	}

	// Convert to module responses
	var responses []ModuleResponse
	for _, module := range modules {
		request, err := h.resolver.ResolveModuleToRequest(module.Name)
		if err != nil {
			h.logger.Warn("failed to resolve module", "module", module.Name, "error", err)
			continue
		}
		responses = append(responses, ModuleResponse{
			Module:         module,
			InstallRequest: request,
			InstallPath:    fmt.Sprintf("/modules/%s", module.Name),
		})
	}

	w.Header().Set("Content-Type", "application/json")
	if err := json.NewEncoder(w).Encode(responses); err != nil {
		h.logger.Error("failed to encode response", "error", err)
		http.Error(w, "Failed to encode response", http.StatusInternalServerError)
	}
}

// HandleListBundles handles GET /catalogs/bundles
// @Summary List catalog bundles
// @Description Returns all available bundles from the catalog with their metadata
// @Tags catalog
// @Produce json
// @Param limit query int false "Maximum number of bundles to return" default(50)
// @Success 200 {array} BundleResponse "List of bundles with metadata and install plans"
// @Failure 500 {string} string "Internal server error"
// @Router /catalogs/bundles [get]
func (h *Handlers) HandleListBundles(w http.ResponseWriter, r *http.Request) {
	h.logger.Info("listing catalog bundles")

	// Parse query parameters
	limit := 50 // default
	if limitStr := r.URL.Query().Get("limit"); limitStr != "" {
		if parsedLimit, err := strconv.Atoi(limitStr); err == nil && parsedLimit > 0 {
			limit = parsedLimit
		}
	}

	bundles, err := h.store.GetBundles()
	if err != nil {
		h.logger.Error("failed to get bundles", "error", err)
		http.Error(w, fmt.Sprintf("Failed to get bundles: %v", err), http.StatusInternalServerError)
		return
	}

	// Apply limit
	if len(bundles) > limit {
		bundles = bundles[:limit]
	}

	// Convert to bundle responses
	var responses []BundleResponse
	for _, bundle := range bundles {
		plan, err := h.resolver.ResolveBundleToInstallPlan(bundle.Name)
		if err != nil {
			h.logger.Warn("failed to resolve bundle", "bundle", bundle.Name, "error", err)
			continue
		}
		responses = append(responses, BundleResponse{
			Bundle:      bundle,
			InstallPlan: plan,
			InstallPath: fmt.Sprintf("/bundles/%s", bundle.Name),
		})
	}

	w.Header().Set("Content-Type", "application/json")
	if err := json.NewEncoder(w).Encode(responses); err != nil {
		h.logger.Error("failed to encode response", "error", err)
		http.Error(w, "Failed to encode response", http.StatusInternalServerError)
	}
}

// HandleGetModule handles GET /catalogs/modules/{module_name}
// @Summary Get specific catalog module
// @Description Returns a specific module from the catalog with its metadata
// @Tags catalog
// @Produce json
// @Param module_name path string true "Module name"
// @Success 200 {object} ModuleResponse "Module with metadata and install request"
// @Failure 404 {string} string "Module not found"
// @Failure 500 {string} string "Internal server error"
// @Router /catalogs/modules/{module_name} [get]
func (h *Handlers) HandleGetModule(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	moduleName := vars["module_name"]
	h.logger.Info("getting catalog module", "module", moduleName)

	module, err := h.store.GetModule(moduleName)
	if err != nil {
		h.logger.Error("failed to get module", "module", moduleName, "error", err)
		http.Error(w, fmt.Sprintf("Module not found: %v", err), http.StatusNotFound)
		return
	}

	// Get the install request
	request, err := h.resolver.ResolveModuleToRequest(moduleName)
	if err != nil {
		h.logger.Error("failed to resolve module to request", "module", moduleName, "error", err)
		http.Error(w, fmt.Sprintf("Failed to resolve module: %v", err), http.StatusInternalServerError)
		return
	}

	response := ModuleResponse{
		Module:         *module,
		InstallRequest: request,
		InstallPath:    fmt.Sprintf("/modules/%s", moduleName),
	}

	w.Header().Set("Content-Type", "application/json")
	if err := json.NewEncoder(w).Encode(response); err != nil {
		h.logger.Error("failed to encode response", "error", err)
		http.Error(w, "Failed to encode response", http.StatusInternalServerError)
	}
}

// HandleGetBundle handles GET /catalogs/bundles/{bundle_name}
// @Summary Get specific catalog bundle
// @Description Returns a specific bundle from the catalog with its metadata
// @Tags catalog
// @Produce json
// @Param bundle_name path string true "Bundle name"
// @Success 200 {object} BundleResponse "Bundle with metadata and install plan"
// @Failure 404 {string} string "Bundle not found"
// @Failure 500 {string} string "Internal server error"
// @Router /catalogs/bundles/{bundle_name} [get]
func (h *Handlers) HandleGetBundle(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	bundleName := vars["bundle_name"]
	h.logger.Info("getting catalog bundle", "bundle", bundleName)

	bundle, err := h.store.GetBundle(bundleName)
	if err != nil {
		h.logger.Error("failed to get bundle", "bundle", bundleName, "error", err)
		http.Error(w, fmt.Sprintf("Bundle not found: %v", err), http.StatusNotFound)
		return
	}

	// Get the install plan
	plan, err := h.resolver.ResolveBundleToInstallPlan(bundleName)
	if err != nil {
		h.logger.Error("failed to resolve bundle to install plan", "bundle", bundleName, "error", err)
		http.Error(w, fmt.Sprintf("Failed to resolve bundle: %v", err), http.StatusInternalServerError)
		return
	}

	response := BundleResponse{
		Bundle:      *bundle,
		InstallPlan: plan,
		InstallPath: fmt.Sprintf("/bundles/%s", bundleName),
	}

	w.Header().Set("Content-Type", "application/json")
	if err := json.NewEncoder(w).Encode(response); err != nil {
		h.logger.Error("failed to encode response", "error", err)
		http.Error(w, "Failed to encode response", http.StatusInternalServerError)
	}
}
