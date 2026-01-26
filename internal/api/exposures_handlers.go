package api

import (
	"context"
	"encoding/json"
	"log/slog"
	"net/http"

	"github.com/gorilla/mux"
)

// CreateExposureRequest represents the request body for creating an exposure
type CreateExposureRequest struct {
	ModuleID      string   `json:"module_id"`
	Protocol      string   `json:"protocol"`
	Hostname      string   `json:"hostname,omitempty"`
	ContainerPort uint32   `json:"container_port"`
	Tags          []string `json:"tags,omitempty"`
}

// ExposureResponse represents the response for an exposure
type ExposureResponse struct {
	ID            string   `json:"id"`
	ModuleID      string   `json:"module_id"`
	Protocol      string   `json:"protocol"`
	Hostname      string   `json:"hostname,omitempty"`
	ContainerPort uint32   `json:"container_port"`
	HostPort      uint32   `json:"host_port,omitempty"`
	Status        string   `json:"status"` // "available" or "unavailable"
	CreatedAt     string   `json:"created_at"`
	Tags          []string `json:"tags,omitempty"`
}

// ListExposuresResponse represents the response for listing exposures
type ListExposuresResponse struct {
	Exposures []ExposureResponse `json:"exposures"`
}

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

// CreateExposureHTTP handles POST /exposures/{exposure_id}
// @ID createExposure
// @Summary Create an exposure for an application
// @Description Exposes an application externally via Envoy reverse proxy
// @Tags exposures
// @Param exposure_id path string true "Exposure ID"
// @Param body body CreateExposureRequest true "Exposure configuration"
// @Success 201 {object} ExposureResponse
// @Success 200 {object} ExposureResponse "Exposure already exists"
// @Failure 400 {string} string "Bad request"
// @Router /exposures/{exposure_id} [post]
func (h *ExposureHandlers) CreateExposureHTTP(w http.ResponseWriter, r *http.Request) {
	// Get exposure_id from URL path
	vars := mux.Vars(r)
	exposureID := vars["exposure_id"]
	if exposureID == "" {
		http.Error(w, "exposure_id is required", http.StatusBadRequest)
		return
	}

	// Parse request body for configuration
	var req CreateExposureRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "invalid request body", http.StatusBadRequest)
		return
	}

	// Validate required fields
	if req.ModuleID == "" {
		http.Error(w, "module_id is required in request body", http.StatusBadRequest)
		return
	}
	if req.Protocol == "" {
		http.Error(w, "protocol is required in request body", http.StatusBadRequest)
		return
	}
	if req.ContainerPort == 0 {
		http.Error(w, "container_port is required in request body", http.StatusBadRequest)
		return
	}

	exposure, created, err := h.store.CreateExposure(r.Context(), exposureID, req.ModuleID, req.Protocol, req.Hostname, req.ContainerPort, req.Tags)
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
// @ID listExposures
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

// GetExposure handles GET /exposures/{exposure_id}
// @ID getExposure
// @Summary Get exposure for an application
// @Description Returns the exposure details for a specific exposure
// @Tags exposures
// @Param exposure_id path string true "Exposure ID"
// @Success 200 {object} ExposureResponse
// @Failure 404 {string} string "Exposure not found"
// @Router /exposures/{exposure_id} [get]
func (h *ExposureHandlers) GetExposure(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	exposureID := vars["exposure_id"]

	exposure, err := h.store.GetExposure(exposureID)
	if err != nil {
		http.Error(w, "exposure not found", http.StatusNotFound)
		return
	}

	resp := toExposureResponse(exposure, h.store)

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(resp)
}

// CreateExposure creates an exposure (for job queue)
func (h *ExposureHandlers) CreateExposure(ctx context.Context, exposureID, moduleID, protocol, hostname string, containerPort uint32, tags []string) error {
	_, _, err := h.store.CreateExposure(ctx, exposureID, moduleID, protocol, hostname, containerPort, tags)
	return err
}

// DeleteExposure removes an exposure (for job queue)
func (h *ExposureHandlers) DeleteExposure(ctx context.Context, exposureID string) error {
	return h.store.DeleteExposure(ctx, exposureID)
}

// DeleteExposureHTTP handles DELETE /exposures/{exposure_id}
// @ID deleteExposure
// @Summary Delete an exposure
// @Description Removes external access for an exposure
// @Tags exposures
// @Param exposure_id path string true "Exposure ID"
// @Success 204 "No content"
// @Failure 404 {string} string "Exposure not found"
// @Router /exposures/{exposure_id} [delete]
func (h *ExposureHandlers) DeleteExposureHTTP(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	exposureID := vars["exposure_id"]

	if err := h.store.DeleteExposure(r.Context(), exposureID); err != nil {
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
		ModuleID:      exp.ModuleID,
		Protocol:      exp.Protocol,
		ContainerPort: exp.ContainerPort,
		Status:        store.getContainerStatus(exp.ModuleID),
		CreatedAt:     exp.CreatedAt.Format("2006-01-02T15:04:05Z07:00"),
		Tags:          exp.Tags,
	}

	if exp.Hostname != "" {
		resp.Hostname = exp.Hostname
	}

	if exp.HostPort != 0 {
		resp.HostPort = exp.HostPort
	}

	return resp
}
