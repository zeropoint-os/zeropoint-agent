package api

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"path/filepath"

	"zeropoint-agent/internal/apps"
	"zeropoint-agent/internal/xds"

	"github.com/gorilla/mux"
	"github.com/moby/moby/client"
)

type apiEnv struct {
	docker      *client.Client
	installer   *apps.Installer
	uninstaller *apps.Uninstaller
	exposures   *ExposureHandlers
	inspect     *InspectHandlers
	logger      *slog.Logger
}

// HealthResponse is returned by GET /health
type HealthResponse struct {
	Status string `json:"status"`
	Error  string `json:"error,omitempty"`
}

// AppsResponse encapsulates a list of apps
type AppsResponse struct {
	Apps []apps.App `json:"apps"`
}

func NewRouter(dockerClient *client.Client, xdsServer *xds.Server, mdnsService MDNSService, logger *slog.Logger) (http.Handler, error) {
	appsDir := apps.GetAppsDir()

	installer := apps.NewInstaller(dockerClient, appsDir, logger)
	uninstaller := apps.NewUninstaller(appsDir, logger)

	// Initialize exposure store
	exposureStore, err := NewExposureStore(dockerClient, xdsServer, mdnsService, logger)
	if err != nil {
		return nil, err
	}

	exposureHandlers := NewExposureHandlers(exposureStore, logger)
	inspectHandlers := NewInspectHandlers(appsDir, logger)

	env := &apiEnv{
		docker:      dockerClient,
		installer:   installer,
		uninstaller: uninstaller,
		exposures:   exposureHandlers,
		inspect:     inspectHandlers,
		logger:      logger,
	}

	r := mux.NewRouter()

	// Health endpoint
	r.HandleFunc("/health", env.healthHandler).Methods(http.MethodGet)

	// Apps endpoints
	r.HandleFunc("/apps", env.appsHandler).Methods(http.MethodGet)
	r.HandleFunc("/apps/{name}", env.installAppHandler).Methods(http.MethodPost)
	r.HandleFunc("/apps/{name}", env.uninstallAppHandler).Methods(http.MethodDelete)
	r.HandleFunc("/apps/{app_id}/inspect", inspectHandlers.InspectApp).Methods(http.MethodGet)

	// Exposure endpoints
	r.HandleFunc("/exposures", exposureHandlers.ListExposures).Methods(http.MethodGet)
	r.HandleFunc("/exposures/{app_id}", exposureHandlers.CreateExposure).Methods(http.MethodPost)
	r.HandleFunc("/exposures/{app_id}", exposureHandlers.GetExposure).Methods(http.MethodGet)
	r.HandleFunc("/exposures/{app_id}", exposureHandlers.DeleteExposure).Methods(http.MethodDelete)

	return r, nil
}

// HealthHandler handles GET /health requests
// @Summary Health check endpoint
// @Description Returns the health status of the API server
// @Tags system
// @Produce json
// @Success 200 {object} HealthResponse "Server is healthy"
// @Failure 503 {object} HealthResponse "Docker unavailable"
// @Router /health [get]
func (e *apiEnv) healthHandler(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	// Basic health: server alive and can reach docker daemon
	resp := HealthResponse{Status: "ok"}
	if e.docker != nil {
		if _, err := e.docker.Ping(ctx, client.PingOptions{}); err != nil {
			w.WriteHeader(http.StatusServiceUnavailable)
			resp.Status = "docker_unavailable"
			resp.Error = err.Error()
			json.NewEncoder(w).Encode(resp)
			return
		}
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(resp)
}

// AppsHandler handles /apps routes
// @Summary List installed apps
// @Summary List all applications
// @Description Returns installed apps metadata
// @Tags apps
// @Produce json
// @Success 200 {object} AppsResponse
// @Router /apps [get]
func (e *apiEnv) appsHandler(w http.ResponseWriter, r *http.Request) {
	switch r.Method {
	case http.MethodGet:
		e.getApps(w, r)
	default:
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
	}
}

func (e *apiEnv) getApps(w http.ResponseWriter, r *http.Request) {
	// Discover apps from filesystem
	list, err := e.discoverApps(r.Context())
	if err != nil {
		http.Error(w, "failed to discover apps", http.StatusInternalServerError)
		return
	}
	resp := AppsResponse{Apps: list}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(resp)
}

// discoverApps scans the apps/ directory for installed app modules
func (e *apiEnv) discoverApps(ctx context.Context) ([]apps.App, error) {
	appsDir := apps.GetAppsDir()
	var result []apps.App

	entries, err := os.ReadDir(appsDir)
	if err != nil {
		if os.IsNotExist(err) {
			return result, nil // No apps directory yet
		}
		return nil, err
	}

	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}

		appID := entry.Name()
		modulePath := filepath.Join(appsDir, appID)

		// Check if main.tf exists
		mainTfPath := filepath.Join(modulePath, "main.tf")
		if _, err := os.Stat(mainTfPath); err != nil {
			continue // Not a valid app module
		}

		app := apps.App{
			ID:         appID,
			ModulePath: modulePath,
			State:      apps.StateUnknown,
		}

		// Query Docker for runtime status
		if err := app.GetContainerStatus(ctx, e.docker); err != nil {
			e.logger.Warn("failed to get container status", "app_id", appID, "error", err)
		}

		// Load containers with ports and mounts from Terraform outputs
		if containers, err := apps.LoadContainers(modulePath, appID); err != nil {
			e.logger.Warn("failed to load containers", "app_id", appID, "error", err)
		} else {
			app.Containers = containers
		}

		result = append(result, app)
	}

	return result, nil
}

// installAppHandler handles POST /apps/{name} with streaming progress updates
// @Summary Install an application
// @Description Installs an application by name with optional configuration
// @Summary Install an application
// @Description Installs an application by name with optional configuration
// @Tags apps
// @Param name path string true "App name"
// @Param body body apps.InstallRequest false "Installation configuration"
// @Success 200 {object} apps.ProgressUpdate
// @Failure 400 {string} string "Bad request"
// @Failure 500 {string} string "Internal server error"
// @Router /apps/{name} [post]
func (e *apiEnv) installAppHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	// Get app name from URL path
	vars := mux.Vars(r)
	appName := vars["name"]
	if appName == "" {
		http.Error(w, "app name is required", http.StatusBadRequest)
		return
	}

	// Parse optional request body
	var req apps.InstallRequest
	if r.Body != nil && r.ContentLength > 0 {
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			http.Error(w, "invalid request body", http.StatusBadRequest)
			return
		}
	}

	// Use path parameter as app_id
	req.AppID = appName

	// Check if app already exists
	appsDir := apps.GetAppsDir()
	appPath := filepath.Join(appsDir, appName)
	if _, err := os.Stat(appPath); err == nil {
		http.Error(w, fmt.Sprintf("app '%s' already exists", appName), http.StatusConflict)
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
	progressCallback := func(update apps.ProgressUpdate) {
		json.NewEncoder(w).Encode(update)
		flusher.Flush()
	}

	// Run installation with progress streaming
	if err := e.installer.Install(req, progressCallback); err != nil {
		e.logger.Error("installation failed", "app_id", req.AppID, "error", err)
		json.NewEncoder(w).Encode(apps.ProgressUpdate{
			Status:  "failed",
			Message: "Installation failed",
			Error:   err.Error(),
		})
		flusher.Flush()
		return
	}
}

// uninstallAppHandler handles DELETE /apps/{name} with streaming progress updates
// @Summary Uninstall an application
// @Description Uninstalls an application by name with streaming progress updates
// @Tags apps
// @Param name path string true "App name"
// @Success 200 {object} apps.ProgressUpdate
// @Failure 400 {string} string "Bad request"
// @Failure 500 {string} string "Internal server error"
// @Router /apps/{name} [delete]
func (e *apiEnv) uninstallAppHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodDelete {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	// Get app name from URL path
	vars := mux.Vars(r)
	appName := vars["name"]
	if appName == "" {
		http.Error(w, "app name is required", http.StatusBadRequest)
		return
	}

	req := apps.UninstallRequest{
		AppID: appName,
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
	progressCallback := func(update apps.ProgressUpdate) {
		json.NewEncoder(w).Encode(update)
		flusher.Flush()
	}

	// Run uninstallation with progress streaming
	if err := e.uninstaller.Uninstall(req, progressCallback); err != nil {
		e.logger.Error("uninstallation failed", "app_id", req.AppID, "error", err)
		json.NewEncoder(w).Encode(apps.ProgressUpdate{
			Status:  "failed",
			Message: "Uninstallation failed",
			Error:   err.Error(),
		})
		flusher.Flush()
		return
	}
}
