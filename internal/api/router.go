package api

import (
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"path/filepath"
	"strings"

	internalPaths "zeropoint-agent/internal"
	"zeropoint-agent/internal/boot"
	"zeropoint-agent/internal/catalog"
	"zeropoint-agent/internal/modules"
	"zeropoint-agent/internal/xds"

	"github.com/gorilla/mux"
	"github.com/moby/moby/client"
)

type apiEnv struct {
	docker    *client.Client
	modules   *ModuleHandlers
	exposures *ExposureHandlers
	inspect   *InspectHandlers
	catalog   *catalog.Handlers
	boot      *BootHandlers
	logger    *slog.Logger
}

// HealthResponse is returned by GET /health
type HealthResponse struct {
	Status string `json:"status"`
	Error  string `json:"error,omitempty"`
}

func NewRouter(dockerClient *client.Client, xdsServer *xds.Server, mdnsService MDNSService, bootMonitor *boot.BootMonitor, logger *slog.Logger) (http.Handler, error) {
	modulesDir := internalPaths.GetModulesDir()

	installer := modules.NewInstaller(dockerClient, modulesDir, logger)
	uninstaller := modules.NewUninstaller(dockerClient, modulesDir, logger)

	// Initialize exposure store
	exposureStore, err := NewExposureStore(dockerClient, xdsServer, mdnsService, logger)
	if err != nil {
		return nil, err
	}

	// Initialize link store
	linkStore, err := NewLinkStore(dockerClient, logger)
	if err != nil {
		return nil, fmt.Errorf("failed to initialize link store: %w", err)
	}

	// Initialize catalog
	catalogStore := catalog.NewStore(logger)
	catalogResolver := catalog.NewResolver(catalogStore)
	catalogHandlers := catalog.NewHandlers(catalogStore, catalogResolver, logger)

	moduleHandlers := NewModuleHandlers(installer, uninstaller, dockerClient, logger)
	exposureHandlers := NewExposureHandlers(exposureStore, logger)
	inspectHandlers := NewInspectHandlers(modulesDir, logger)
	linkHandlers := NewLinkHandlers(modulesDir, linkStore, logger)
	bootHandlers := NewBootHandlers(bootMonitor)

	env := &apiEnv{
		docker:    dockerClient,
		modules:   moduleHandlers,
		exposures: exposureHandlers,
		inspect:   inspectHandlers,
		catalog:   catalogHandlers,
		boot:      bootHandlers,
		logger:    logger,
	}

	r := mux.NewRouter()

	// Middleware to check boot completion for non-boot APIs
	bootCheckMiddleware := func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			// Always allow health, boot endpoints, and static files/index
			if r.URL.Path == "/api/health" ||
				strings.HasPrefix(r.URL.Path, "/api/boot/") ||
				r.URL.Path == "/api/boot" ||
				r.URL.Path == "/" ||
				r.URL.Path == "/index.html" ||
				!strings.HasPrefix(r.URL.Path, "/api/") {
				next.ServeHTTP(w, r)
				return
			}

			// For all other APIs, check if boot is complete
			if !bootMonitor.IsComplete() {
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(http.StatusServiceUnavailable)
				json.NewEncoder(w).Encode(map[string]string{
					"error":   "system_booting",
					"message": "System is still booting. Please wait for boot to complete before accessing this API.",
				})
				return
			}

			next.ServeHTTP(w, r)
		})
	}

	// API routes MUST be registered before the static file server
	// Health endpoint
	r.HandleFunc("/api/health", env.healthHandler).Methods(http.MethodGet)

	// Boot monitoring endpoints (always available)
	r.HandleFunc("/api/boot/status", bootHandlers.HandleBootStatus).Methods(http.MethodGet)
	r.HandleFunc("/api/boot/logs", bootHandlers.HandleBootLogs).Methods(http.MethodGet)
	r.HandleFunc("/api/boot/stream", bootHandlers.HandleBootStream)
	// Per-service and marker endpoints
	r.HandleFunc("/api/boot/status/{service}", bootHandlers.HandleBootService).Methods(http.MethodGet)
	r.HandleFunc("/api/boot/status/{service}/{marker}", bootHandlers.HandleBootMarker).Methods(http.MethodGet)

	// Module endpoints
	r.HandleFunc("/api/modules", moduleHandlers.ListModules).Methods(http.MethodGet)
	r.HandleFunc("/api/modules/{name}", moduleHandlers.InstallModule).Methods(http.MethodPost)
	r.HandleFunc("/api/modules/{name}", moduleHandlers.UninstallModule).Methods(http.MethodDelete)
	r.HandleFunc("/api/modules/{module_id}/inspect", inspectHandlers.InspectModule).Methods(http.MethodGet)

	// Link endpoints
	r.HandleFunc("/api/links", linkHandlers.ListLinks).Methods(http.MethodGet)
	r.HandleFunc("/api/links/{id}", linkHandlers.GetLink).Methods(http.MethodGet)
	r.HandleFunc("/api/links/{id}", linkHandlers.CreateOrUpdateLink).Methods(http.MethodPost)
	r.HandleFunc("/api/links/{id}", linkHandlers.DeleteLink).Methods(http.MethodDelete)

	// Exposure endpoints
	r.HandleFunc("/api/exposures", exposureHandlers.ListExposures).Methods(http.MethodGet)
	r.HandleFunc("/api/exposures/{exposure_id}", exposureHandlers.CreateExposure).Methods(http.MethodPost)
	r.HandleFunc("/api/exposures/{exposure_id}", exposureHandlers.GetExposure).Methods(http.MethodGet)
	r.HandleFunc("/api/exposures/{exposure_id}", exposureHandlers.DeleteExposure).Methods(http.MethodDelete)

	// Catalog endpoints
	r.HandleFunc("/api/catalogs/update", catalogHandlers.HandleUpdateCatalog).Methods(http.MethodPost)
	r.HandleFunc("/api/catalogs/modules", catalogHandlers.HandleListModules).Methods(http.MethodGet)
	r.HandleFunc("/api/catalogs/modules/{module_name}", catalogHandlers.HandleGetModule).Methods(http.MethodGet)
	r.HandleFunc("/api/catalogs/bundles", catalogHandlers.HandleListBundles).Methods(http.MethodGet)
	r.HandleFunc("/api/catalogs/bundles/{bundle_name}", catalogHandlers.HandleGetBundle).Methods(http.MethodGet)

	// Web UI - serve static files as fallback after API routes
	webDir := getWebDir()
	if webDir != "" {
		r.PathPrefix("/").Handler(http.FileServer(http.Dir(webDir)))
	}

	// Wrap router with boot check middleware
	return bootCheckMiddleware(r), nil
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

// getWebDir finds the web UI directory
func getWebDir() string {
	// Try relative to executable
	if webDir := "web"; fileExists(webDir) {
		return webDir
	}

	// Try relative to working directory
	if webDir := filepath.Join(".", "web"); fileExists(webDir) {
		return webDir
	}

	// Try standard installation location
	if webDir := filepath.Join("/app", "web"); fileExists(webDir) {
		return webDir
	}

	return ""
}

// fileExists checks if a directory exists
func fileExists(path string) bool {
	info, err := os.Stat(path)
	return err == nil && info.IsDir()
}
