package api

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"

	"zeropoint-agent/internal/modules"

	"github.com/gorilla/mux"
)

// BundleHandlers handles HTTP requests for bundle operations
type BundleHandlers struct {
	bundleStore      *BundleStore
	exposureStore    *ExposureStore
	exposureHandlers *ExposureHandlers
	linkHandlers     *LinkHandlers
	uninstaller      *modules.Uninstaller
	logger           *slog.Logger
}

// NewBundleHandlers creates a new bundle handlers instance
func NewBundleHandlers(bundleStore *BundleStore, exposureStore *ExposureStore, exposureHandlers *ExposureHandlers, linkHandlers *LinkHandlers, uninstaller *modules.Uninstaller, logger *slog.Logger) *BundleHandlers {
	return &BundleHandlers{
		bundleStore:      bundleStore,
		exposureStore:    exposureStore,
		exposureHandlers: exposureHandlers,
		linkHandlers:     linkHandlers,
		uninstaller:      uninstaller,
		logger:           logger,
	}
}

// BundleResponse represents an installed bundle
// swagger:model BundleResponse
type BundleResponse struct {
	// Bundle ID
	// required: true
	ID string `json:"id"`
	// Bundle name
	// required: true
	Name string `json:"name"`
	// Bundle description
	Description string `json:"description,omitempty"`
	// List of module IDs in this bundle
	// required: true
	Modules []string `json:"modules"`
	// Map of link IDs to link details
	Links map[string][]BundleLink `json:"links,omitempty"`
	// Map of exposure IDs to exposure details
	Exposures map[string]BundleExposure `json:"exposures,omitempty"`
	// Bundle status: "queued", "running", "completed", "failed", "partially_completed"
	// required: true
	Status string `json:"status"`
	// Unix timestamp when bundle was installed
	InstalledAt int64 `json:"installed_at,omitempty"`
}

// swagger:model BundleLink
type BundleLink struct {
	// Module ID that this link connects to
	Module string `json:"module"`
	// Port bindings for this link
	Bind map[string]string `json:"bind,omitempty"`
}

// swagger:model BundleExposure
type BundleExposure struct {
	// Module ID that this exposure connects to
	Module string `json:"module"`
	// Protocol (http, https, etc)
	Protocol string `json:"protocol"`
	// Port on the module
	ModulePort int `json:"module_port"`
}

// ListBundles handles GET /api/bundles - lists installed bundles
// @ID listBundles
// @Summary List installed bundles
// @Description List all installed bundles from persistent storage
// @Tags bundles
// @Produce json
// @Success 200 {array} BundleResponse "List of installed bundles"
// @Failure 500 {string} string "Internal server error"
// @Router /bundles [get]
func (h *BundleHandlers) ListBundles(w http.ResponseWriter, r *http.Request) {
	// Get all installed bundles from persistent store
	bundleRecords := h.bundleStore.ListBundles()

	bundles := make([]BundleResponse, 0, len(bundleRecords))
	for _, record := range bundleRecords {
		bundle := BundleResponse{
			ID:          record.ID,
			Name:        record.Name,
			Status:      record.Status,
			InstalledAt: record.InstalledAt.Unix(),
			Modules:     make([]string, 0, len(record.Components.Modules)),
			Links:       make(map[string][]BundleLink),
			Exposures:   make(map[string]BundleExposure),
		}

		// Collect module, link, and exposure IDs from components
		for _, comp := range record.Components.Modules {
			bundle.Modules = append(bundle.Modules, comp.ID)
		}
		for _, comp := range record.Components.Links {
			bundle.Links[comp.ID] = []BundleLink{}
		}
		for _, comp := range record.Components.Exposures {
			bundle.Exposures[comp.ID] = BundleExposure{}
		}

		bundles = append(bundles, bundle)
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(bundles)
}

// GetBundle handles GET /api/bundles/{bundle-id} - gets a specific installed bundle
// @ID getBundle
// @Summary Get bundle details
// @Description Get details of a specific installed bundle
// @Tags bundles
// @Produce json
// @Param bundle-id path string true "Bundle ID"
// @Success 200 {object} BundleResponse "Bundle details"
// @Failure 404 {string} string "Bundle not found"
// @Router /bundles/{bundle-id} [get]
func (h *BundleHandlers) GetBundle(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	bundleID := vars["bundle-id"]

	// Get bundle from persistent store
	recordIface, err := h.bundleStore.GetBundle(bundleID)
	if err != nil {
		http.Error(w, "bundle not found", http.StatusNotFound)
		return
	}

	record, ok := recordIface.(*BundleRecord)
	if !ok {
		http.Error(w, "invalid bundle record", http.StatusInternalServerError)
		return
	}

	bundle := BundleResponse{
		ID:          record.ID,
		Name:        record.Name,
		Status:      record.Status,
		InstalledAt: record.InstalledAt.Unix(),
		Modules:     make([]string, 0, len(record.Components.Modules)),
		Links:       make(map[string][]BundleLink),
		Exposures:   make(map[string]BundleExposure),
	}

	// Collect module, link, and exposure IDs from components
	for _, comp := range record.Components.Modules {
		bundle.Modules = append(bundle.Modules, comp.ID)
	}
	for _, comp := range record.Components.Links {
		bundle.Links[comp.ID] = []BundleLink{}
	}
	for _, comp := range record.Components.Exposures {
		bundle.Exposures[comp.ID] = BundleExposure{}
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(bundle)
}

// DeleteBundle handles DELETE /api/bundles/{bundle-id} - uninstalls all bundle components immediately with streaming updates
// @ID deleteBundle
// @Summary Delete a bundle (immediate uninstall with streaming updates)
// @Description Uninstall all bundle components immediately and remove the bundle. Streams SSE updates for each component.
// @Tags bundles
// @Produce text/event-stream
// @Param bundle-id path string true "Bundle ID"
// @Success 200 "Uninstallation complete, stream of events sent"
// @Failure 404 {string} string "Bundle not found"
// @Router /bundles/{bundle-id} [delete]
func (h *BundleHandlers) DeleteBundle(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	bundleID := vars["bundle-id"]

	// Get bundle from persistent store
	bundleIface, err := h.bundleStore.GetBundle(bundleID)
	if err != nil {
		http.Error(w, "bundle not found", http.StatusNotFound)
		return
	}

	record, ok := bundleIface.(*BundleRecord)
	if !ok {
		http.Error(w, "invalid bundle record", http.StatusInternalServerError)
		return
	}

	// Set up Server-Sent Events
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")

	flusher, ok := w.(http.Flusher)
	if !ok {
		http.Error(w, "streaming not supported", http.StatusInternalServerError)
		return
	}

	ctx := context.Background()

	// Uninstall exposures first (they have no dependencies)
	for _, expComp := range record.Components.Exposures {
		if err := h.exposureHandlers.DeleteExposure(ctx, expComp.ID); err != nil {
			h.logger.Error("failed to delete exposure", "exposure_id", expComp.ID, "error", err)
			fmt.Fprintf(w, "data: {\"component\":\"%s\",\"type\":\"exposure\",\"status\":\"failed\",\"error\":\"%s\"}\n\n", expComp.ID, err.Error())
		} else {
			fmt.Fprintf(w, "data: {\"component\":\"%s\",\"type\":\"exposure\",\"status\":\"completed\"}\n\n", expComp.ID)
		}
		flusher.Flush()
	}

	// Delete links second (modules still exist)
	for _, linkComp := range record.Components.Links {
		if err := h.linkHandlers.DeleteLink(ctx, linkComp.ID); err != nil {
			h.logger.Error("failed to delete link", "link_id", linkComp.ID, "error", err)
			fmt.Fprintf(w, "data: {\"component\":\"%s\",\"type\":\"link\",\"status\":\"failed\",\"error\":\"%s\"}\n\n", linkComp.ID, err.Error())
		} else {
			fmt.Fprintf(w, "data: {\"component\":\"%s\",\"type\":\"link\",\"status\":\"completed\"}\n\n", linkComp.ID)
		}
		flusher.Flush()
	}

	// Uninstall modules last (they're the foundation)
	for _, modComp := range record.Components.Modules {
		// Create a no-op progress callback
		noOpCallback := func(update modules.ProgressUpdate) {}
		if err := h.uninstaller.Uninstall(modules.UninstallRequest{ModuleID: modComp.ID}, noOpCallback); err != nil {
			h.logger.Error("failed to uninstall module", "module_id", modComp.ID, "error", err)
			fmt.Fprintf(w, "data: {\"component\":\"%s\",\"type\":\"module\",\"status\":\"failed\",\"error\":\"%s\"}\n\n", modComp.ID, err.Error())
		} else {
			fmt.Fprintf(w, "data: {\"component\":\"%s\",\"type\":\"module\",\"status\":\"completed\"}\n\n", modComp.ID)
		}
		flusher.Flush()
	}

	// Delete the bundle record
	if err := h.bundleStore.DeleteBundle(bundleID); err != nil {
		h.logger.Error("failed to delete bundle record", "bundle_id", bundleID, "error", err)
		fmt.Fprintf(w, "data: {\"status\":\"error\",\"message\":\"Failed to delete bundle record: %s\"}\n\n", err.Error())
	} else {
		fmt.Fprintf(w, "data: {\"status\":\"bundle_deleted\",\"message\":\"Bundle %s removed successfully\"}\n\n", bundleID)
	}
	flusher.Flush()
}
