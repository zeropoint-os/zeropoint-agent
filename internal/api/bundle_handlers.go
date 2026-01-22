package api

import (
	"encoding/json"
	"log/slog"
	"net/http"
	"os"
	"path/filepath"

	internalPaths "zeropoint-agent/internal"
	"zeropoint-agent/internal/catalog"
	"zeropoint-agent/internal/modules"

	"github.com/gorilla/mux"
	"github.com/moby/moby/client"
)

// BundleHandlers handles HTTP requests for bundle operations
type BundleHandlers struct {
	installer     *modules.Installer
	uninstaller   *modules.Uninstaller
	exposureStore *ExposureStore
	linkHandlers  *LinkHandlers
	dockerClient  *client.Client
	catalogStore  *catalog.Store
	logger        *slog.Logger
}

// NewBundleHandlers creates a new bundle handlers instance
func NewBundleHandlers(
	installer *modules.Installer,
	uninstaller *modules.Uninstaller,
	exposureStore *ExposureStore,
	linkHandlers *LinkHandlers,
	dockerClient *client.Client,
	catalogStore *catalog.Store,
	logger *slog.Logger,
) *BundleHandlers {
	return &BundleHandlers{
		installer:     installer,
		uninstaller:   uninstaller,
		exposureStore: exposureStore,
		linkHandlers:  linkHandlers,
		dockerClient:  dockerClient,
		catalogStore:  catalogStore,
		logger:        logger,
	}
}

// BundleResponse represents an installed bundle
type BundleResponse struct {
	ID          string                    `json:"id"`
	Name        string                    `json:"name"`
	Description string                    `json:"description,omitempty"`
	Modules     []string                  `json:"modules"`
	Links       map[string][]BundleLink   `json:"links,omitempty"`
	Exposures   map[string]BundleExposure `json:"exposures,omitempty"`
	Status      string                    `json:"status"` // "complete", "partial", "degraded"
	InstalledAt int64                     `json:"installed_at,omitempty"`
}

type BundleLink struct {
	Module string            `json:"module"`
	Bind   map[string]string `json:"bind,omitempty"`
}

type BundleExposure struct {
	Module     string `json:"module"`
	Protocol   string `json:"protocol"`
	ModulePort int    `json:"module_port"`
}

// ListBundles handles GET /api/bundles - lists installed bundles
// @ID listBundles
// @Summary List installed bundles
// @Description List all installed bundles (identified by bundle tags on modules)
// @Tags bundles
// @Produce json
// @Success 200 {array} BundleResponse "List of installed bundles"
// @Failure 500 {string} string "Internal server error"
// @Router /bundles [get]
func (h *BundleHandlers) ListBundles(w http.ResponseWriter, r *http.Request) {
	bundlesByID := make(map[string]*BundleResponse)

	// Get all modules to find bundle tags
	modulesDir := internalPaths.GetModulesDir()
	entries, err := os.ReadDir(modulesDir)
	if err != nil {
		if os.IsNotExist(err) {
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode([]BundleResponse{})
			return
		}
		h.logger.Error("failed to read modules dir", "error", err)
		http.Error(w, "failed to read modules", http.StatusInternalServerError)
		return
	}

	// Scan modules for bundle tags
	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}

		metadataPath := filepath.Join(modulesDir, entry.Name(), "metadata.json")
		if data, err := os.ReadFile(metadataPath); err == nil {
			var metadata struct {
				ID   string   `json:"id"`
				Tags []string `json:"tags,omitempty"`
			}
			if err := json.Unmarshal(data, &metadata); err == nil {
				// Look for bundle:<bundle-id> tags
				for _, tag := range metadata.Tags {
					if len(tag) > 7 && tag[:7] == "bundle:" {
						bundleID := tag[7:]
						if bundlesByID[bundleID] == nil {
							bundlesByID[bundleID] = &BundleResponse{
								ID:        bundleID,
								Modules:   []string{},
								Links:     make(map[string][]BundleLink),
								Exposures: make(map[string]BundleExposure),
							}
						}
						bundlesByID[bundleID].Modules = append(bundlesByID[bundleID].Modules, metadata.ID)
					}
				}
			}
		}
	}

	// Convert map to slice
	bundles := make([]BundleResponse, 0, len(bundlesByID))
	for _, bundle := range bundlesByID {
		// Try to get bundle definition from catalog for description
		if catalogBundle, err := h.catalogStore.GetBundle(bundle.ID); err == nil {
			bundle.Name = catalogBundle.Name
			bundle.Description = catalogBundle.Description
			bundle.Links = make(map[string][]BundleLink)
			for linkID, links := range catalogBundle.Links {
				for _, link := range links {
					bundle.Links[linkID] = append(bundle.Links[linkID], BundleLink{
						Module: link.Module,
						Bind:   link.Bind,
					})
				}
			}
			bundle.Exposures = make(map[string]BundleExposure)
			for expID, exp := range catalogBundle.Exposures {
				bundle.Exposures[expID] = BundleExposure{
					Module:     exp.Module,
					Protocol:   exp.Protocol,
					ModulePort: exp.ModulePort,
				}
			}
		}
		bundle.Status = "complete" // TODO: check if all expected components are installed
		bundles = append(bundles, *bundle)
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
// @Failure 500 {string} string "Internal server error"
// @Router /bundles/{bundle-id} [get]
func (h *BundleHandlers) GetBundle(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	bundleID := vars["bundle-id"]

	// List all bundles and find the matching one
	modulesDir := internalPaths.GetModulesDir()
	entries, err := os.ReadDir(modulesDir)
	if err != nil {
		h.logger.Error("failed to read modules dir", "error", err)
		http.Error(w, "failed to read modules", http.StatusInternalServerError)
		return
	}

	bundle := &BundleResponse{
		ID:        bundleID,
		Modules:   []string{},
		Links:     make(map[string][]BundleLink),
		Exposures: make(map[string]BundleExposure),
	}

	found := false
	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}

		metadataPath := filepath.Join(modulesDir, entry.Name(), "metadata.json")
		if data, err := os.ReadFile(metadataPath); err == nil {
			var metadata struct {
				ID   string   `json:"id"`
				Tags []string `json:"tags,omitempty"`
			}
			if err := json.Unmarshal(data, &metadata); err == nil {
				// Look for bundle:<bundle-id> tag
				for _, tag := range metadata.Tags {
					if tag == "bundle:"+bundleID {
						bundle.Modules = append(bundle.Modules, metadata.ID)
						found = true
					}
				}
			}
		}
	}

	if !found {
		http.Error(w, "bundle not found", http.StatusNotFound)
		return
	}

	// Try to get bundle definition from catalog for description
	if catalogBundle, err := h.catalogStore.GetBundle(bundleID); err == nil {
		bundle.Name = catalogBundle.Name
		bundle.Description = catalogBundle.Description
		for linkID, links := range catalogBundle.Links {
			for _, link := range links {
				bundle.Links[linkID] = append(bundle.Links[linkID], BundleLink{
					Module: link.Module,
					Bind:   link.Bind,
				})
			}
		}
		for expID, exp := range catalogBundle.Exposures {
			bundle.Exposures[expID] = BundleExposure{
				Module:     exp.Module,
				Protocol:   exp.Protocol,
				ModulePort: exp.ModulePort,
			}
		}
	}

	bundle.Status = "complete" // TODO: check if all expected components are installed

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(bundle)
}


