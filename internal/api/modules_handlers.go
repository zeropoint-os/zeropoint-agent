package api

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"path/filepath"

	internalPaths "zeropoint-agent/internal"
	"zeropoint-agent/internal/modules"

	"github.com/gorilla/mux"
	"github.com/moby/moby/client"
)

type ModuleHandlers struct {
	installer   *Installer
	uninstaller *Uninstaller
	docker      *client.Client
	logger      *slog.Logger
}

// NewModuleHandlers creates a new module handlers instance
func NewModuleHandlers(installer *Installer, uninstaller *Uninstaller, docker *client.Client, logger *slog.Logger) *ModuleHandlers {
	return &ModuleHandlers{
		installer:   installer,
		uninstaller: uninstaller,
		docker:      docker,
		logger:      logger,
	}
}

// RegisterRoutes registers the module-related routes
func (h *ModuleHandlers) RegisterRoutes(router *mux.Router) {
	router.HandleFunc("/modules", h.ListModules).Methods("GET")
	router.HandleFunc("/modules/{name}", h.InstallModule).Methods("POST")
	router.HandleFunc("/modules/{name}", h.UninstallModule).Methods("DELETE")
}

// InstallModule handles POST /modules/{name} with streaming progress updates
// @ID installModule
// @Summary Install a module
// @Description Installs a module by name with optional configuration
// @Tags modules
// @Accept json
// @Produce application/x-ndjson,text/event-stream
// @Param name path string true "Module name"
// @Param body body modules.InstallRequest false "Installation configuration"
// @Success 200 {string} string "Installation progress stream"
// @Failure 400 {string} string "Bad request"
// @Failure 500 {string} string "Internal server error"
// @Router /modules/{name} [post]
func (h *ModuleHandlers) InstallModule(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	// Get module name from URL path
	vars := mux.Vars(r)
	moduleName := vars["name"]
	if moduleName == "" {
		http.Error(w, "module name is required", http.StatusBadRequest)
		return
	}

	// Parse optional request body
	var req InstallRequest
	if r.Body != nil && r.ContentLength > 0 {
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			http.Error(w, "invalid request body", http.StatusBadRequest)
			return
		}
	}

	// Use path parameter as module_id
	req.ModuleID = moduleName

	// Check if module already exists (must have main.tf to be valid)
	modulesDir := internalPaths.GetModulesDir()
	modulePath := filepath.Join(modulesDir, moduleName)
	mainTfPath := filepath.Join(modulePath, "main.tf")
	if _, err := os.Stat(mainTfPath); err == nil {
		http.Error(w, fmt.Sprintf("module '%s' already exists", moduleName), http.StatusConflict)
		return
	}

	// Validate request
	if req.Source == "" && req.LocalPath == "" {
		http.Error(w, "either source or local_path is required in request body", http.StatusBadRequest)
		return
	}

	// Setup streaming response
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Transfer-Encoding", "chunked")
	w.Header().Set("X-Content-Type-Options", "nosniff")

	flusher, ok := w.(http.Flusher)
	if !ok {
		http.Error(w, "streaming not supported", http.StatusInternalServerError)
		return
	}

	// Stream progress updates
	progressCallback := func(update ProgressUpdate) {
		json.NewEncoder(w).Encode(update)
		flusher.Flush()
	}

	// Run installation with progress streaming
	if err := h.installer.Install(req, progressCallback); err != nil {
		h.logger.Error("installation failed", "module_id", req.ModuleID, "error", err)
		json.NewEncoder(w).Encode(ProgressUpdate{
			Status:  "failed",
			Message: "Installation failed",
			Error:   err.Error(),
		})
		flusher.Flush()
		return
	}
}

// UninstallModule handles DELETE /modules/{name} with streaming progress updates
// @ID uninstallModule
// @Summary Uninstall a module
// @Description Uninstalls a module by name with streaming progress updates
// @Tags modules
// @Produce application/x-ndjson,text/event-stream
// @Param name path string true "Module name"
// @Success 200 {string} string "Uninstallation progress stream"
// @Failure 400 {string} string "Bad request"
// @Failure 500 {string} string "Internal server error"
// @Router /modules/{name} [delete]
func (h *ModuleHandlers) UninstallModule(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodDelete {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	// Get module name from URL path
	vars := mux.Vars(r)
	moduleName := vars["name"]
	if moduleName == "" {
		http.Error(w, "module name is required", http.StatusBadRequest)
		return
	}

	req := UninstallRequest{
		ModuleID: moduleName,
	}

	// Setup streaming response
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Transfer-Encoding", "chunked")
	w.Header().Set("X-Content-Type-Options", "nosniff")

	flusher, ok := w.(http.Flusher)
	if !ok {
		http.Error(w, "streaming not supported", http.StatusInternalServerError)
		return
	}

	// Stream progress updates
	progressCallback := func(update ProgressUpdate) {
		json.NewEncoder(w).Encode(update)
		flusher.Flush()
	}

	// Run uninstallation with progress streaming
	if err := h.uninstaller.Uninstall(req, progressCallback); err != nil {
		h.logger.Error("uninstallation failed", "module_id", req.ModuleID, "error", err)
		json.NewEncoder(w).Encode(ProgressUpdate{
			Status:  "failed",
			Message: "Uninstallation failed",
			Error:   err.Error(),
		})
		flusher.Flush()
		return
	}
}

// ListModules handles GET /modules
// @ID listModules
// @Summary List installed modules
// @Description Returns installed modules metadata
// @Tags modules
// @Produce json
// @Success 200 {object} ModulesResponse
// @Router /modules [get]
func (h *ModuleHandlers) ListModules(w http.ResponseWriter, r *http.Request) {
	// Discover modules from filesystem
	list, err := h.discoverModules(r.Context())
	if err != nil {
		http.Error(w, "failed to discover modules", http.StatusInternalServerError)
		return
	}
	resp := ModulesResponse{Modules: list}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(resp)
}

// discoverModules scans the modules/ directory for installed modules
func (h *ModuleHandlers) discoverModules(ctx context.Context) ([]Module, error) {
	modulesDir := internalPaths.GetModulesDir()
	var result []Module

	entries, err := os.ReadDir(modulesDir)
	if err != nil {
		if os.IsNotExist(err) {
			return result, nil // No modules directory yet
		}
		return nil, err
	}

	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}

		moduleID := entry.Name()
		modulePath := filepath.Join(modulesDir, moduleID)

		// Check if main.tf exists
		mainTfPath := filepath.Join(modulePath, "main.tf")
		if _, err := os.Stat(mainTfPath); err != nil {
			continue // Not a valid module
		}

		module := Module{
			ID:         moduleID,
			ModulePath: modulePath,
			State:      modules.StateUnknown,
		}

		// Load metadata (including tags) from .zeropoint.json
		if metadata, err := modules.LoadMetadata(modulePath); err != nil {
			h.logger.Warn("failed to load metadata", "module_id", moduleID, "error", err)
		} else if metadata != nil {
			module.Tags = metadata.Tags
		}

		// Query Docker for runtime status
		if err := module.GetContainerStatus(ctx, h.docker); err != nil {
			h.logger.Warn("failed to get container status", "module_id", moduleID, "error", err)
		}

		// Load containers with ports and mounts from Terraform outputs
		if containers, err := modules.LoadContainers(modulePath, moduleID); err != nil {
			h.logger.Warn("failed to load containers", "module_id", moduleID, "error", err)
		} else {
			module.Containers = containers
		}

		result = append(result, module)
	}

	return result, nil
}
