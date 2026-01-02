package api

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"path/filepath"
	"runtime"
	"strconv"

	"zeropoint-agent/internal/modules"
	"zeropoint-agent/internal/network"
	"zeropoint-agent/internal/system"
	"zeropoint-agent/internal/terraform"

	"github.com/gorilla/mux"
	"github.com/moby/moby/client"
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

// LinkHandlers holds HTTP handlers for app linking
type LinkHandlers struct {
	appsDir        string
	linkStore      *LinkStore
	networkManager *network.Manager
	logger         *slog.Logger
}

// NewLinkHandlers creates a new link handlers instance
func NewLinkHandlers(appsDir string, linkStore *LinkStore, logger *slog.Logger) *LinkHandlers {
	return &LinkHandlers{
		appsDir:        appsDir,
		linkStore:      linkStore,
		networkManager: linkStore.GetNetworkManager(),
		logger:         logger,
	}
}

// ErrorResponse represents an API error response
type ErrorResponse struct {
	Error   string `json:"error"`
	Message string `json:"message,omitempty"`
}

// LinkRequest represents the request to link multiple apps (legacy)
type LinkRequest struct {
	Apps map[string]map[string]interface{} `json:"apps"`
}

// CreateLinkRequest represents the request to create/update a link
type CreateLinkRequest struct {
	Apps map[string]map[string]interface{} `json:"apps"`
}

// LinksResponse represents the response from listing links
type LinksResponse struct {
	Links []*Link `json:"links"`
}

// AppReference represents a reference to another app's output
type AppReference struct {
	FromApp string `json:"from_app"`
	Output  string `json:"output"`
}

// LinkResponse represents the response from linking apps
type LinkResponse struct {
	Success      bool              `json:"success"`
	Message      string            `json:"message,omitempty"`
	AppliedOrder []string          `json:"applied_order,omitempty"`
	Errors       map[string]string `json:"errors,omitempty"`
}

// AppsResponse encapsulates a list of apps
type AppsResponse struct {
	Apps []apps.App `json:"apps"`
}

// RegisterRoutes registers the link-related routes
func (h *LinkHandlers) RegisterRoutes(router *mux.Router) {
	router.HandleFunc("/links", h.ListLinks).Methods("GET")
	router.HandleFunc("/links/{id}", h.GetLink).Methods("GET")
	router.HandleFunc("/links/{id}", h.CreateOrUpdateLink).Methods("POST")
	router.HandleFunc("/links/{id}", h.DeleteLink).Methods("DELETE")
}

// ListLinks handles GET /links
// @Summary List all links
// @Description Returns all active app links
// @Tags links
// @Produce json
// @Success 200 {object} LinksResponse
// @Router /links [get]
func (h *LinkHandlers) ListLinks(w http.ResponseWriter, r *http.Request) {
	links := h.linkStore.ListLinks()

	response := LinksResponse{Links: links}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(response)
}

// GetLink handles GET /links/{id}
// @Summary Get link details
// @Description Returns details for a specific link
// @Tags links
// @Param id path string true "Link ID"
// @Produce json
// @Success 200 {object} Link
// @Failure 404 {object} ErrorResponse
// @Router /links/{id} [get]
func (h *LinkHandlers) GetLink(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	linkID := vars["id"]

	link, err := h.linkStore.GetLink(linkID)
	if err != nil {
		http.Error(w, "Link not found", http.StatusNotFound)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(link)
}

// CreateOrUpdateLink handles POST /links/{id}
// @Summary Create or update a link
// @Description Create or update a link between multiple apps
// @Tags links
// @Param id path string true "Link ID"
// @Accept json
// @Produce json
// @Param request body CreateLinkRequest true "Link configuration"
// @Success 200 {object} LinkResponse
// @Failure 400 {object} ErrorResponse
// @Failure 500 {object} ErrorResponse
// @Router /links/{id} [post]
func (h *LinkHandlers) CreateOrUpdateLink(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	linkID := vars["id"]

	var req CreateLinkRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		h.logger.Error("Failed to decode link request", "error", err)
		http.Error(w, "Invalid JSON in request body", http.StatusBadRequest)
		return
	}

	h.logger.Info("Creating/updating link", "link_id", linkID, "apps", getAppNames(req.Apps))

	// Use the existing linking logic
	response := h.linkApps(linkID, req.Apps)

	w.Header().Set("Content-Type", "application/json")
	if response.Success {
		w.WriteHeader(http.StatusOK)
	} else {
		w.WriteHeader(http.StatusInternalServerError)
	}
	json.NewEncoder(w).Encode(response)
}

// DeleteLink handles DELETE /links/{id}
// @Summary Delete a link
// @Description Remove a link and clean up associated resources
// @Tags links
// @Param id path string true "Link ID"
// @Success 204
// @Failure 404 {object} ErrorResponse
// @Failure 500 {object} ErrorResponse
// @Router /links/{id} [delete]
func (h *LinkHandlers) DeleteLink(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	linkID := vars["id"]

	if err := h.linkStore.DeleteLink(r.Context(), linkID); err != nil {
		if err.Error() == "link not found" {
			http.Error(w, "Link not found", http.StatusNotFound)
			return
		}
		h.logger.Error("Failed to delete link", "link_id", linkID, "error", err)
		http.Error(w, "Failed to delete link", http.StatusInternalServerError)
		return
	}

	w.WriteHeader(http.StatusNoContent)
}

// linkApps contains the core linking logic (refactored from LinkApps)
func (h *LinkHandlers) linkApps(linkID string, apps map[string]map[string]interface{}) LinkResponse {

	// Step 1: Validate all apps exist
	if err := h.validateAppsExist(apps); err != nil {
		h.logger.Error("App validation failed", "error", err)
		return LinkResponse{
			Success: false,
			Message: err.Error(),
		}
	}

	// Step 2: Analyze dependencies and determine order
	graph, err := AnalyzeDependencies(apps)
	if err != nil {
		h.logger.Error("Dependency analysis failed", "error", err)
		return LinkResponse{
			Success: false,
			Message: fmt.Sprintf("Dependency analysis failed: %v", err),
		}
	}

	order, err := graph.TopologicalSort()
	if err != nil {
		h.logger.Error("Topological sort failed", "error", err)
		return LinkResponse{
			Success: false,
			Message: fmt.Sprintf("Dependency resolution failed: %v", err),
		}
	}

	h.logger.Info("Determined app order", "order", order)

	// Step 3: Backup states
	stateManager := NewStateManager(h.appsDir)
	backup, err := stateManager.BackupStates(order)
	if err != nil {
		h.logger.Error("State backup failed", "error", err)
		return LinkResponse{
			Success: false,
			Message: fmt.Sprintf("State backup failed: %v", err),
		}
	}

	// Step 4: Apply configurations in dependency order
	errors := make(map[string]string)
	appliedApps := []string{}

	for _, appName := range order {
		config, exists := apps[appName]
		if !exists {
			continue // App not in this link request
		}

		h.logger.Info("Applying configuration", "app", appName, "config", config)

		if err := h.applyAppConfiguration(appName, config); err != nil {
			errors[appName] = err.Error()
			h.logger.Error("Failed to apply configuration", "app", appName, "error", err)

			// Rollback on first failure
			h.logger.Info("Rolling back states due to failure")
			if restoreErr := stateManager.RestoreStates(backup); restoreErr != nil {
				h.logger.Error("Failed to restore states", "error", restoreErr)
				errors["rollback"] = restoreErr.Error()
			}

			return LinkResponse{
				Success:      false,
				Message:      fmt.Sprintf("Configuration failed for app %s", appName),
				AppliedOrder: appliedApps,
				Errors:       errors,
			}
		}

		appliedApps = append(appliedApps, appName)

		// Create shared networks for any apps this app references
		if err := h.createSharedNetworksForReferences(appName, config); err != nil {
			h.logger.Warn("Failed to create shared networks", "app", appName, "error", err)
			// Don't fail the entire operation for network creation failures
		}
	}

	// Success - cleanup backup files
	if err := stateManager.CleanupBackup(backup); err != nil {
		h.logger.Warn("Failed to cleanup backup files", "error", err)
	}

	// Step 5: Collect references and networks, then store the successful link
	references := make(map[string]map[string]string)
	var sharedNetworks []string

	// Parse references from app configurations and collect network names
	networkNames := make(map[string]bool)
	for appName, config := range apps {
		appRefs := make(map[string]string)
		for inputName, value := range config {
			if ref, isRef := parseAppReference(value); isRef {
				appRefs[inputName] = fmt.Sprintf("%s.%s", ref.FromApp, ref.Output)

				// Generate the network name for this reference
				linkApps := []string{ref.FromApp, appName}
				if linkApps[0] > linkApps[1] {
					linkApps[0], linkApps[1] = linkApps[1], linkApps[0]
				}
				networkName := fmt.Sprintf("zeropoint-link-%s-%s", linkApps[0], linkApps[1])
				networkNames[networkName] = true
			}
		}
		if len(appRefs) > 0 {
			references[appName] = appRefs
		}
	}

	// Convert network names map to slice
	for networkName := range networkNames {
		sharedNetworks = append(sharedNetworks, networkName)
	}

	if _, err := h.linkStore.CreateOrUpdateLink(context.Background(), linkID, apps, references, sharedNetworks, order); err != nil {
		h.logger.Warn("Failed to store link", "error", err)
		// Don't fail the operation for storage failures
	}

	return LinkResponse{
		Success:      true,
		Message:      "All apps linked successfully",
		AppliedOrder: appliedApps,
	}
}

// Helper function to extract app names from request
func getAppNames(apps map[string]map[string]interface{}) []string {
	names := make([]string, 0, len(apps))
	for name := range apps {
		names = append(names, name)
	}
	return names
}

// validateAppsExist checks that all referenced apps exist on disk
func (h *LinkHandlers) validateAppsExist(apps map[string]map[string]interface{}) error {
	for appName := range apps {
		appDir := filepath.Join(h.appsDir, appName)
		if _, err := os.Stat(appDir); os.IsNotExist(err) {
			return fmt.Errorf("app %s does not exist", appName)
		}
	}

	// Also validate referenced apps in app references
	for appName, config := range apps {
		for inputName, value := range config {
			if ref, isRef := parseAppReference(value); isRef {
				refAppDir := filepath.Join(h.appsDir, ref.FromApp)
				if _, err := os.Stat(refAppDir); os.IsNotExist(err) {
					return fmt.Errorf("app %s references non-existent app %s in input %s", appName, ref.FromApp, inputName)
				}
			}
		}
	}

	return nil
}

// applyAppConfiguration applies configuration to a single app
func (h *LinkHandlers) applyAppConfiguration(appName string, config map[string]interface{}) error {
	h.logger.Info("Applying configuration to app", "app", appName)

	// Resolve app references to actual values
	resolvedConfig, err := h.resolveAppReferences(config)
	if err != nil {
		return fmt.Errorf("failed to resolve references: %w", err)
	}

	// Inject system variables (same as installer does)
	variables, err := h.prepareSystemVariables(appName)
	if err != nil {
		return fmt.Errorf("failed to prepare system variables: %w", err)
	}

	// Add user-provided variables (resolved)
	for key, value := range resolvedConfig {
		// Convert value to string, handling different types properly
		var strValue string
		switch v := value.(type) {
		case string:
			strValue = v
		case []byte:
			strValue = string(v)
		case json.RawMessage:
			// Handle JSON raw message by converting to string and unquoting
			strValue = string(v)
			// If it's a quoted JSON string, unquote it
			if len(strValue) >= 2 && strValue[0] == '"' && strValue[len(strValue)-1] == '"' {
				if unquoted, err := strconv.Unquote(strValue); err == nil {
					strValue = unquoted
				}
			}
		default:
			strValue = fmt.Sprintf("%v", v)
		}
		h.logger.Info("Converting value for terraform", "key", key, "original_value", value, "original_type", fmt.Sprintf("%T", value), "string_value", strValue)
		variables[key] = strValue
	}

	// Apply configuration using Terraform
	appDir := filepath.Join(h.appsDir, appName)
	executor, err := terraform.NewExecutor(appDir)
	if err != nil {
		return fmt.Errorf("failed to create terraform executor: %w", err)
	}

	if err := executor.Apply(variables); err != nil {
		return fmt.Errorf("terraform apply failed: %w", err)
	}

	h.logger.Info("Configuration applied successfully", "app", appName)
	return nil
}

// resolveAppReferences resolves app references to actual output values
func (h *LinkHandlers) resolveAppReferences(config map[string]interface{}) (map[string]interface{}, error) {
	resolved := make(map[string]interface{})

	for key, value := range config {
		if ref, isRef := parseAppReference(value); isRef {
			// Get the actual output value from the referenced app
			resolvedValue, err := h.getAppOutput(ref.FromApp, ref.Output)
			if err != nil {
				return nil, fmt.Errorf("failed to resolve reference %s.%s: %w", ref.FromApp, ref.Output, err)
			}
			h.logger.Info("Resolved app reference", "key", key, "reference", value, "resolved_value", resolvedValue, "type", fmt.Sprintf("%T", resolvedValue))
			resolved[key] = resolvedValue
		} else {
			resolved[key] = value
		}
	}

	return resolved, nil
}

// getAppOutput retrieves an output value from an app's Terraform state
func (h *LinkHandlers) getAppOutput(appName, outputName string) (interface{}, error) {
	appDir := filepath.Join(h.appsDir, appName)

	executor, err := terraform.NewExecutor(appDir)
	if err != nil {
		return nil, fmt.Errorf("failed to create terraform executor for app %s: %w", appName, err)
	}

	outputs, err := executor.Output()
	if err != nil {
		return nil, fmt.Errorf("failed to get terraform outputs for app %s: %w", appName, err)
	}

	output, exists := outputs[outputName]
	if !exists {
		return nil, fmt.Errorf("output %s not found in app %s", outputName, appName)
	}

	return output.Value, nil
}

// prepareSystemVariables creates the standard zp_ variables that all apps need
func (h *LinkHandlers) prepareSystemVariables(appName string) (map[string]string, error) {
	// Create network name using the same convention as installer
	networkName := fmt.Sprintf("zeropoint-app-%s", appName)

	// Get system info
	arch := runtime.GOARCH
	gpuVendor := system.DetectGPU()

	// Prepare base variables (all zp_ prefixed)
	variables := map[string]string{
		"zp_app_id":       appName,
		"zp_network_name": networkName,
		"zp_arch":         arch,
		"zp_gpu_vendor":   gpuVendor,
	}

	// Create app storage directory if needed
	storageRoot := os.Getenv("MODULE_STORAGE_ROOT")
	if storageRoot == "" {
		storageRoot = "./data" // default fallback
	}
	appStoragePath := filepath.Join(storageRoot, "apps", appName)
	if err := os.MkdirAll(appStoragePath, 0755); err != nil {
		return nil, fmt.Errorf("failed to create app storage directory: %w", err)
	}

	// Convert to absolute path for Docker volumes
	absAppStoragePath, err := filepath.Abs(appStoragePath)
	if err != nil {
		return nil, fmt.Errorf("failed to get absolute path: %w", err)
	}

	// Pass app storage root to terraform (must be absolute for Docker)
	variables["zp_app_storage"] = absAppStoragePath

	h.logger.Info("Prepared system variables", "app", appName, "variables", variables)
	return variables, nil
}

// createSharedNetworksForReferences creates shared networks for referenced apps
func (h *LinkHandlers) createSharedNetworksForReferences(targetApp string, config map[string]interface{}) error {
	ctx := context.Background()

	for _, value := range config {
		if ref, isRef := parseAppReference(value); isRef {
			h.logger.Info("Creating shared network for app reference", "from", ref.FromApp, "to", targetApp, "output", ref.Output)

			// Create shared network for direct communication between linked apps
			if err := h.ensureSharedNetwork(ctx, ref.FromApp, targetApp); err != nil {
				h.logger.Warn("Failed to create shared network", "from", ref.FromApp, "to", targetApp, "error", err)
				// Don't return error - network connection failure shouldn't break linking
			}
		}
	}

	return nil
}

// Helper function to get map keys for logging
func getKeys(m map[string]interface{}) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	return keys
}

// ensureSharedNetwork creates a shared network for linked apps and connects both containers
func (h *LinkHandlers) ensureSharedNetwork(ctx context.Context, sourceApp, targetApp string) error {
	h.logger.Info("Creating shared network for linked apps", "source", sourceApp, "target", targetApp)

	// Create a network name that represents the link between these apps
	// Sort the names to ensure consistent network names regardless of link direction
	apps := []string{sourceApp, targetApp}
	if apps[0] > apps[1] {
		apps[0], apps[1] = apps[1], apps[0]
	}
	networkName := fmt.Sprintf("zeropoint-link-%s-%s", apps[0], apps[1])

	h.logger.Info("Using shared network", "network", networkName, "apps", []string{sourceApp, targetApp})

	// Connect both apps to the shared network using networkManager
	if err := h.ensureAppOnSharedNetwork(ctx, sourceApp, networkName); err != nil {
		return fmt.Errorf("failed to connect source app to shared network: %w", err)
	}

	if err := h.ensureAppOnSharedNetwork(ctx, targetApp, networkName); err != nil {
		return fmt.Errorf("failed to connect target app to shared network: %w", err)
	}

	h.logger.Info("Successfully connected apps to shared network", "network", networkName, "apps", []string{sourceApp, targetApp})
	return nil
}

// ensureAppOnSharedNetwork connects an app's container to a shared network
func (h *LinkHandlers) ensureAppOnSharedNetwork(ctx context.Context, appName, networkName string) error {
	containerName := appName + "-main"
	return h.networkManager.ConnectContainerToNetwork(ctx, containerName, networkName)
}

// getAppOutputs retrieves all output values from an app's Terraform state
func (h *LinkHandlers) getAppOutputs(appName string) (map[string]interface{}, error) {
	appDir := filepath.Join(h.appsDir, appName)

	executor, err := terraform.NewExecutor(appDir)
	if err != nil {
		return nil, fmt.Errorf("failed to create terraform executor for app %s: %w", appName, err)
	}

	outputs, err := executor.Output()
	if err != nil {
		return nil, fmt.Errorf("failed to get terraform outputs for app %s: %w", appName, err)
	}

	result := make(map[string]interface{})
	for name, output := range outputs {
		result[name] = output.Value
	}

	return result, nil
}

// AppHandlers holds HTTP handlers for app management
type AppHandlers struct {
	installer   *apps.Installer
	uninstaller *apps.Uninstaller
	docker      *client.Client
	logger      *slog.Logger
}

// NewAppHandlers creates a new app handlers instance
func NewAppHandlers(installer *apps.Installer, uninstaller *apps.Uninstaller, docker *client.Client, logger *slog.Logger) *AppHandlers {
	return &AppHandlers{
		installer:   installer,
		uninstaller: uninstaller,
		docker:      docker,
		logger:      logger,
	}
}

// InstallApp handles POST /apps/{name} with streaming progress updates
// @Summary Install an application
// @Description Installs an application by name with optional configuration
// @Tags apps
// @Param name path string true "App name"
// @Param body body apps.InstallRequest false "Installation configuration"
// @Success 200 {object} apps.ProgressUpdate
// @Failure 400 {string} string "Bad request"
// @Failure 500 {string} string "Internal server error"
// @Router /apps/{name} [post]
func (h *AppHandlers) InstallApp(w http.ResponseWriter, r *http.Request) {
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
	if err := h.installer.Install(req, progressCallback); err != nil {
		h.logger.Error("installation failed", "app_id", req.AppID, "error", err)
		json.NewEncoder(w).Encode(apps.ProgressUpdate{
			Status:  "failed",
			Message: "Installation failed",
			Error:   err.Error(),
		})
		flusher.Flush()
		return
	}
}

// UninstallApp handles DELETE /apps/{name} with streaming progress updates
// @Summary Uninstall an application
// @Description Uninstalls an application by name with streaming progress updates
// @Tags apps
// @Param name path string true "App name"
// @Success 200 {object} apps.ProgressUpdate
// @Failure 400 {string} string "Bad request"
// @Failure 500 {string} string "Internal server error"
// @Router /apps/{name} [delete]
func (h *AppHandlers) UninstallApp(w http.ResponseWriter, r *http.Request) {
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
	if err := h.uninstaller.Uninstall(req, progressCallback); err != nil {
		h.logger.Error("uninstallation failed", "app_id", req.AppID, "error", err)
		json.NewEncoder(w).Encode(apps.ProgressUpdate{
			Status:  "failed",
			Message: "Uninstallation failed",
			Error:   err.Error(),
		})
		flusher.Flush()
		return
	}
}

// ListApps handles GET /apps
// @Summary List installed apps
// @Description Returns installed apps metadata
// @Tags apps
// @Produce json
// @Success 200 {object} AppsResponse
// @Router /apps [get]
func (h *AppHandlers) ListApps(w http.ResponseWriter, r *http.Request) {
	// Discover apps from filesystem
	list, err := h.discoverApps(r.Context())
	if err != nil {
		http.Error(w, "failed to discover apps", http.StatusInternalServerError)
		return
	}
	resp := AppsResponse{Apps: list}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(resp)
}

// discoverApps scans the apps/ directory for installed app modules
func (h *AppHandlers) discoverApps(ctx context.Context) ([]apps.App, error) {
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
		if err := app.GetContainerStatus(ctx, h.docker); err != nil {
			h.logger.Warn("failed to get container status", "app_id", appID, "error", err)
		}

		// Load containers with ports and mounts from Terraform outputs
		if containers, err := apps.LoadContainers(modulePath, appID); err != nil {
			h.logger.Warn("failed to load containers", "app_id", appID, "error", err)
		} else {
			app.Containers = containers
		}

		result = append(result, app)
	}

	return result, nil
}
