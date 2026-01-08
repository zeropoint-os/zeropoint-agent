package api

import (
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"

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
	logger    *slog.Logger
}

// HealthResponse is returned by GET /health
type HealthResponse struct {
	Status string `json:"status"`
	Error  string `json:"error,omitempty"`
}

func NewRouter(dockerClient *client.Client, xdsServer *xds.Server, mdnsService MDNSService, logger *slog.Logger) (http.Handler, error) {
	modulesDir := modules.GetModulesDir()

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

	env := &apiEnv{
		docker:    dockerClient,
		modules:   moduleHandlers,
		exposures: exposureHandlers,
		inspect:   inspectHandlers,
		catalog:   catalogHandlers,
		logger:    logger,
	}

	r := mux.NewRouter()

	// Health endpoint
	r.HandleFunc("/health", env.healthHandler).Methods(http.MethodGet)

	// Module endpoints
	r.HandleFunc("/modules", moduleHandlers.ListModules).Methods(http.MethodGet)
	r.HandleFunc("/modules/{name}", moduleHandlers.InstallModule).Methods(http.MethodPost)
	r.HandleFunc("/modules/{name}", moduleHandlers.UninstallModule).Methods(http.MethodDelete)
	r.HandleFunc("/modules/{module_id}/inspect", inspectHandlers.InspectModule).Methods(http.MethodGet)

	// Link endpoints
	linkHandlers.RegisterRoutes(r)

	// Exposure endpoints
	r.HandleFunc("/exposures", exposureHandlers.ListExposures).Methods(http.MethodGet)
	r.HandleFunc("/exposures/{exposure_id}", exposureHandlers.CreateExposure).Methods(http.MethodPost)
	r.HandleFunc("/exposures/{exposure_id}", exposureHandlers.GetExposure).Methods(http.MethodGet)
	r.HandleFunc("/exposures/{exposure_id}", exposureHandlers.DeleteExposure).Methods(http.MethodDelete)

	// Catalog endpoints
	r.HandleFunc("/catalogs/update", catalogHandlers.HandleUpdateCatalog).Methods(http.MethodPost)
	r.HandleFunc("/catalogs/modules", catalogHandlers.HandleListModules).Methods(http.MethodGet)
	r.HandleFunc("/catalogs/modules/{module_name}", catalogHandlers.HandleGetModule).Methods(http.MethodGet)
	r.HandleFunc("/catalogs/bundles", catalogHandlers.HandleListBundles).Methods(http.MethodGet)
	r.HandleFunc("/catalogs/bundles/{bundle_name}", catalogHandlers.HandleGetBundle).Methods(http.MethodGet)

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
