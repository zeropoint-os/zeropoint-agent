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

// CreateExposure handles POST /exposures
func (h *ExposureHandlers) CreateExposure(w http.ResponseWriter, r *http.Request) {
	var req CreateExposureRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "invalid request body", http.StatusBadRequest)
		return
	}

	// Validate required fields
	if req.AppID == "" {
		http.Error(w, "app_id is required", http.StatusBadRequest)
		return
	}
	if req.Protocol == "" {
		http.Error(w, "protocol is required", http.StatusBadRequest)
		return
	}
	if req.ContainerPort == 0 {
		http.Error(w, "container_port is required", http.StatusBadRequest)
		return
	}

	exposure, created, err := h.store.CreateExposure(r.Context(), req.AppID, req.Protocol, req.Hostname, req.ContainerPort)
	if err != nil {
		h.logger.Error("failed to create exposure", "error", err)
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	resp := toExposureResponse(exposure)

	w.Header().Set("Content-Type", "application/json")
	if created {
		w.WriteHeader(http.StatusCreated)
	} else {
		w.WriteHeader(http.StatusOK)
	}
	json.NewEncoder(w).Encode(resp)
}

// ListExposures handles GET /exposures
func (h *ExposureHandlers) ListExposures(w http.ResponseWriter, r *http.Request) {
	exposures := h.store.ListExposures()

	resp := ListExposuresResponse{
		Exposures: make([]ExposureResponse, len(exposures)),
	}

	for i, exp := range exposures {
		resp.Exposures[i] = toExposureResponse(exp)
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(resp)
}

// GetExposure handles GET /exposures/{id}
func (h *ExposureHandlers) GetExposure(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	id := vars["id"]

	exposure, err := h.store.GetExposure(id)
	if err != nil {
		http.Error(w, "exposure not found", http.StatusNotFound)
		return
	}

	resp := toExposureResponse(exposure)

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(resp)
}

// DeleteExposure handles DELETE /exposures/{id}
func (h *ExposureHandlers) DeleteExposure(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	id := vars["id"]

	if err := h.store.DeleteExposure(r.Context(), id); err != nil {
		h.logger.Error("failed to delete exposure", "error", err)
		http.Error(w, err.Error(), http.StatusNotFound)
		return
	}

	w.WriteHeader(http.StatusNoContent)
}

// toExposureResponse converts an Exposure to ExposureResponse
func toExposureResponse(exp *Exposure) ExposureResponse {
	resp := ExposureResponse{
		ID:            exp.ID,
		AppID:         exp.AppID,
		Protocol:      exp.Protocol,
		ContainerPort: exp.ContainerPort,
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
