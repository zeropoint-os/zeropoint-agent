package api

import (
	"encoding/json"
	"log/slog"
	"net/http"

	"github.com/gorilla/mux"
)

// CreateExposureRequest represents the request body for creating an exposure
type CreateExposureRequest struct {
	AppID         string `json:"app_id"`
	Protocol      string `json:"protocol"`
	Hostname      string `json:"hostname,omitempty"`
	ContainerPort uint32 `json:"container_port"`
}

// ExposureResponse represents the response for an exposure
type ExposureResponse struct {
	ID            string `json:"id"`
	AppID         string `json:"app_id"`
	Protocol      string `json:"protocol"`
	Hostname      string `json:"hostname,omitempty"`
	ContainerPort uint32 `json:"container_port"`
	HostPort      uint32 `json:"host_port,omitempty"`
	Status        string `json:"status"` // "available" or "unavailable"
	CreatedAt     string `json:"created_at"`
}

// ListExposuresResponse represents the response for listing exposures
type ListExposuresResponse struct {
	Exposures []ExposureResponse `json:"exposures"`
}

// ExposureHandlers holds HTTP handlers for exposure endpoints
type ExposureHandlers struct {
	store  *ExposureStore
	logger *slog.Logger
}

// NewExposureHandlers creates a new exposure handlers instance
func NewExposureHandlers(store *ExposureStore, logger *slog.Logger) *ExposureHandlers {
	return &ExposureHandlers{
		store:  store,
		logger: logger,
	}
}

// CreateExposure handles POST /exposures/{app_id}
// @Summary Create an exposure for an application
// @Description Exposes an application externally via Envoy reverse proxy
// @Tags exposures
// @Param app_id path string true "App ID"
// @Param body body CreateExposureRequest true "Exposure configuration"
// @Success 201 {object} ExposureResponse
// @Success 200 {object} ExposureResponse "Exposure already exists"
// @Failure 400 {string} string "Bad request"
// @Router /exposures/{app_id} [post]
func (h *ExposureHandlers) CreateExposure(w http.ResponseWriter, r *http.Request) {
	// Get app_id from URL path
	vars := mux.Vars(r)
	appID := vars["app_id"]
	if appID == "" {
		http.Error(w, "app_id is required", http.StatusBadRequest)
		return
	}

	// Parse optional request body for additional config
	var req CreateExposureRequest
	if r.Body != nil && r.ContentLength > 0 {
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			http.Error(w, "invalid request body", http.StatusBadRequest)
			return
		}
	}

	// Use path parameter as app_id
	req.AppID = appID

	// Validate required fields
	if req.Protocol == "" {
		http.Error(w, "protocol is required in request body", http.StatusBadRequest)
		return
	}
	if req.ContainerPort == 0 {
		http.Error(w, "container_port is required in request body", http.StatusBadRequest)
		return
	}

	exposure, created, err := h.store.CreateExposure(r.Context(), req.AppID, req.Protocol, req.Hostname, req.ContainerPort)
	if err != nil {
		h.logger.Error("failed to create exposure", "error", err)
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	resp := toExposureResponse(exposure, h.store)

	w.Header().Set("Content-Type", "application/json")
	if created {
		w.WriteHeader(http.StatusCreated)
	} else {
		w.WriteHeader(http.StatusOK)
	}
	json.NewEncoder(w).Encode(resp)
}

// ListExposures handles GET /exposures
// @Summary List all exposures
// @Description Returns all active exposures
// @Tags exposures
// @Success 200 {object} ListExposuresResponse
// @Router /exposures [get]
func (h *ExposureHandlers) ListExposures(w http.ResponseWriter, r *http.Request) {
	exposures := h.store.ListExposures()

	resp := ListExposuresResponse{
		Exposures: make([]ExposureResponse, len(exposures)),
	}

	for i, exp := range exposures {
		resp.Exposures[i] = toExposureResponse(exp, h.store)
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(resp)
}

// GetExposure handles GET /exposures/{app_id}
// @Summary Get exposure for an application
// @Description Returns the exposure details for a specific application
// @Tags exposures
// @Param app_id path string true "App ID"
// @Success 200 {object} ExposureResponse
// @Failure 404 {string} string "Exposure not found"
// @Router /exposures/{app_id} [get]
func (h *ExposureHandlers) GetExposure(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	appID := vars["app_id"]

	exposure := h.store.GetExposureByAppID(appID)
	if exposure == nil {
		http.Error(w, "exposure not found", http.StatusNotFound)
		return
	}

	resp := toExposureResponse(exposure, h.store)

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(resp)
}

// DeleteExposure handles DELETE /exposures/{app_id}
// @Summary Delete an exposure
// @Description Removes external access for an application
// @Tags exposures
// @Param app_id path string true "App ID"
// @Success 204 "No content"
// @Failure 404 {string} string "Exposure not found"
// @Router /exposures/{app_id} [delete]
func (h *ExposureHandlers) DeleteExposure(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	appID := vars["app_id"]

	if err := h.store.DeleteExposureByAppID(r.Context(), appID); err != nil {
		h.logger.Error("failed to delete exposure", "error", err)
		http.Error(w, err.Error(), http.StatusNotFound)
		return
	}

	w.WriteHeader(http.StatusNoContent)
}

// toExposureResponse converts an Exposure to ExposureResponse
func toExposureResponse(exp *Exposure, store *ExposureStore) ExposureResponse {
	resp := ExposureResponse{
		ID:            exp.ID,
		AppID:         exp.AppID,
		Protocol:      exp.Protocol,
		ContainerPort: exp.ContainerPort,
		Status:        store.getContainerStatus(exp.AppID),
		CreatedAt:     exp.CreatedAt.Format("2006-01-02T15:04:05Z07:00"),
	}

	if exp.Hostname != "" {
		resp.Hostname = exp.Hostname
	}

	if exp.HostPort != 0 {
		resp.HostPort = exp.HostPort
	}

	return resp
}
