package api

import (
	"encoding/json"
	"log/slog"
	"net/http"

	"github.com/gorilla/mux"
)

// BundleHandlers handles HTTP requests for bundle operations
type BundleHandlers struct {
	bundleStore   *BundleStore
	exposureStore *ExposureStore
	linkHandlers  *LinkHandlers
	logger        *slog.Logger
}

// NewBundleHandlers creates a new bundle handlers instance
func NewBundleHandlers(bundleStore *BundleStore, exposureStore *ExposureStore, linkHandlers *LinkHandlers, logger *slog.Logger) *BundleHandlers {
	return &BundleHandlers{
		bundleStore:   bundleStore,
		exposureStore: exposureStore,
		linkHandlers:  linkHandlers,
		logger:        logger,
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
