package api

import (
	"encoding/json"
	"net/http"

	"zeropoint-agent/internal/apps"
	"zeropoint-agent/internal/docker"
	"zeropoint-agent/internal/storage"
)

type apiEnv struct {
	store storage.Storage
	dock  *docker.Client
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

func NewRouter(store storage.Storage, dock *docker.Client) http.Handler {
	env := &apiEnv{store: store, dock: dock}
	mux := http.NewServeMux()
	mux.HandleFunc("/health", env.healthHandler)
	mux.HandleFunc("/apps", env.appsHandler)
	// TODO: other endpoints: install, start, stop, delete
	return mux
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
	if e.dock != nil {
		if err := e.dock.Ping(ctx); err != nil {
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
	list, err := e.store.GetApps()
	if err != nil {
		http.Error(w, "failed to load apps", http.StatusInternalServerError)
		return
	}
	resp := AppsResponse{Apps: list}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(resp)
}
