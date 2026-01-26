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

	"zeropoint-agent/internal/network"
	"zeropoint-agent/internal/system"
	"zeropoint-agent/internal/terraform"

	"github.com/gorilla/mux"
)

// ErrorResponse represents an API error response
type ErrorResponse struct {
	Error   string `json:"error"`
	Message string `json:"message,omitempty"`
}

// LinkRequest represents the request to link multiple modules (legacy)
type LinkRequest struct {
	Modules map[string]map[string]interface{} `json:"modules"`
}

// CreateLinkRequest represents the request to create/update a link
type CreateLinkRequest struct {
	Modules map[string]map[string]interface{} `json:"modules"`
	Tags    []string                          `json:"tags,omitempty"`
}

// LinksResponse represents the response from listing links
type LinksResponse struct {
	Links []*Link `json:"links"`
}

// AppReference represents a reference to another module's output
type AppReference struct {
	FromModule string `json:"from_module"`
	Output     string `json:"output"`
}

// LinkResponse represents the response from linking modules
type LinkResponse struct {
	Success      bool              `json:"success"`
	Message      string            `json:"message,omitempty"`
	AppliedOrder []string          `json:"applied_order,omitempty"`
	Errors       map[string]string `json:"errors,omitempty"`
}

// ModulesResponse encapsulates a list of modules
type ModulesResponse struct {
	Modules []Module `json:"modules"`
}

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

// RegisterRoutes registers the link-related routes
func (h *LinkHandlers) RegisterRoutes(router *mux.Router) {
	router.HandleFunc("/links", h.ListLinks).Methods("GET")
	router.HandleFunc("/links/{id}", h.GetLink).Methods("GET")
	router.HandleFunc("/links/{id}", h.CreateOrUpdateLink).Methods("POST")
	router.HandleFunc("/links/{id}", h.DeleteLinkHTTP).Methods("DELETE")
}

// ListLinks handles GET /links
// @ID listLinks
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
// @ID getLink
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
// @ID createOrUpdateLink
// @Summary Create or update a link
// @Description Create or update a link between multiple modules
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

	h.logger.Info("Creating/updating link", "link_id", linkID, "modules", getAppNames(req.Modules))

	// Use the existing linking logic
	response := h.linkApps(linkID, req.Modules, req.Tags)

	w.Header().Set("Content-Type", "application/json")
	if response.Success {
		w.WriteHeader(http.StatusOK)
	} else {
		w.WriteHeader(http.StatusInternalServerError)
	}
	json.NewEncoder(w).Encode(response)
}

// CreateLink creates a link between multiple modules (for job queue)
func (h *LinkHandlers) CreateLink(ctx context.Context, linkID string, modules map[string]map[string]interface{}, tags []string) error {
	response := h.linkApps(linkID, modules, tags)
	if !response.Success {
		return fmt.Errorf("link validation failed: %s", response.Message)
	}
	return nil
}

// DeleteLink removes a link and cleans up associated resources
func (h *LinkHandlers) DeleteLink(ctx context.Context, id string) error {
	return h.linkStore.DeleteLink(ctx, id)
}

// DeleteLinkHTTP handles DELETE /links/{id}
// @ID deleteLink
// @Summary Delete a link
// @Description Remove a link and clean up associated resources
// @Tags links
// @Param id path string true "Link ID"
// @Success 204
// @Failure 404 {object} ErrorResponse
// @Failure 500 {object} ErrorResponse
// @Router /links/{id} [delete]
func (h *LinkHandlers) DeleteLinkHTTP(w http.ResponseWriter, r *http.Request) {
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

// linkApps contains the core linking logic
func (h *LinkHandlers) linkApps(linkID string, modules map[string]map[string]interface{}, tags []string) LinkResponse {

	// Step 1: Validate all modules exist
	if err := h.validateAppsExist(modules); err != nil {
		h.logger.Error("Module validation failed", "error", err)
		return LinkResponse{
			Success: false,
			Message: err.Error(),
		}
	}

	// Step 2: Analyze dependencies and determine order
	graph, err := AnalyzeDependencies(modules)
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

	h.logger.Info("Determined module order", "order", order)

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
	appliedModules := []string{}

	for _, moduleName := range order {
		config, exists := modules[moduleName]
		if !exists {
			continue // Module not in this link request
		}

		h.logger.Info("Applying configuration", "module", moduleName, "config", config)

		if err := h.applyModuleConfiguration(moduleName, config); err != nil {
			errors[moduleName] = err.Error()
			h.logger.Error("Failed to apply configuration", "module", moduleName, "error", err)

			// Rollback on first failure
			h.logger.Info("Rolling back states due to failure")
			if restoreErr := stateManager.RestoreStates(backup); restoreErr != nil {
				h.logger.Error("Failed to restore states", "error", restoreErr)
				errors["rollback"] = restoreErr.Error()
			}

			return LinkResponse{
				Success:      false,
				Message:      fmt.Sprintf("Configuration failed for module %s", moduleName),
				AppliedOrder: appliedModules,
				Errors:       errors,
			}
		}

		appliedModules = append(appliedModules, moduleName)

		// Create shared networks for any modules this module references
		if err := h.createSharedNetworksForReferences(moduleName, config); err != nil {
			h.logger.Warn("Failed to create shared networks", "module", moduleName, "error", err)
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

	// Parse references from module configurations and collect network names
	networkNames := make(map[string]bool)
	for moduleName, config := range modules {
		appRefs := make(map[string]string)
		for inputName, value := range config {
			if ref, isRef := parseAppReference(value); isRef {
				appRefs[inputName] = fmt.Sprintf("%s.%s", ref.FromModule, ref.Output)

				// Generate the network name for this reference
				linkModules := []string{ref.FromModule, moduleName}
				if linkModules[0] > linkModules[1] {
					linkModules[0], linkModules[1] = linkModules[1], linkModules[0]
				}
				networkName := fmt.Sprintf("zeropoint-link-%s-%s", linkModules[0], linkModules[1])
				networkNames[networkName] = true
			}
		}
		if len(appRefs) > 0 {
			references[moduleName] = appRefs
		}
	}

	// Convert network names map to slice
	for networkName := range networkNames {
		sharedNetworks = append(sharedNetworks, networkName)
	}

	if _, err := h.linkStore.CreateOrUpdateLink(context.Background(), linkID, modules, references, sharedNetworks, order, tags); err != nil {
		h.logger.Warn("Failed to store link", "error", err)
		// Don't fail the operation for storage failures
	}

	return LinkResponse{
		Success:      true,
		Message:      "All modules linked successfully",
		AppliedOrder: appliedModules,
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

	// Also validate referenced modules in module references
	for moduleName, config := range apps {
		for inputName, value := range config {
			if ref, isRef := parseAppReference(value); isRef {
				refModuleDir := filepath.Join(h.appsDir, ref.FromModule)
				if _, err := os.Stat(refModuleDir); os.IsNotExist(err) {
					return fmt.Errorf("module %s references non-existent module %s in input %s", moduleName, ref.FromModule, inputName)
				}
			}
		}
	}

	return nil
}

// applyModuleConfiguration applies configuration to a single module
func (h *LinkHandlers) applyModuleConfiguration(moduleName string, config map[string]interface{}) error {
	h.logger.Info("Applying configuration to module", "module", moduleName)

	// Resolve app references to actual values
	resolvedConfig, err := h.resolveAppReferences(config)
	if err != nil {
		return fmt.Errorf("failed to resolve references: %w", err)
	}

	// Inject system variables (same as installer does)
	variables, err := h.prepareSystemVariables(moduleName)
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
	appDir := filepath.Join(h.appsDir, moduleName)
	executor, err := terraform.NewExecutor(appDir)
	if err != nil {
		return fmt.Errorf("failed to create terraform executor: %w", err)
	}

	if err := executor.Apply(variables); err != nil {
		return fmt.Errorf("terraform apply failed: %w", err)
	}

	h.logger.Info("Configuration applied successfully", "module", moduleName)
	return nil
}

// resolveAppReferences resolves module references to actual output values
func (h *LinkHandlers) resolveAppReferences(config map[string]interface{}) (map[string]interface{}, error) {
	resolved := make(map[string]interface{})

	for key, value := range config {
		if ref, isRef := parseAppReference(value); isRef {
			// Get the actual output value from the referenced module
			resolvedValue, err := h.getAppOutput(ref.FromModule, ref.Output)
			if err != nil {
				return nil, fmt.Errorf("failed to resolve reference %s.%s: %w", ref.FromModule, ref.Output, err)
			}
			h.logger.Info("Resolved module reference", "key", key, "reference", value, "resolved_value", resolvedValue, "type", fmt.Sprintf("%T", resolvedValue))
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

// prepareSystemVariables creates the standard zp_ variables that all modules need
func (h *LinkHandlers) prepareSystemVariables(moduleName string) (map[string]string, error) {
	// Create network name using the same convention as installer
	networkName := fmt.Sprintf("zeropoint-module-%s", moduleName)

	// Get system info
	arch := runtime.GOARCH
	gpuVendor := system.DetectGPU()

	// Prepare base variables (all zp_ prefixed)
	variables := map[string]string{
		"zp_module_id":    moduleName,
		"zp_network_name": networkName,
		"zp_arch":         arch,
		"zp_gpu_vendor":   gpuVendor,
	}

	// Create app storage directory if needed
	storageRoot := os.Getenv("MODULE_STORAGE_ROOT")
	if storageRoot == "" {
		storageRoot = "./data" // default fallback
	}
	appStoragePath := filepath.Join(storageRoot, "modules", moduleName)
	if err := os.MkdirAll(appStoragePath, 0755); err != nil {
		return nil, fmt.Errorf("failed to create app storage directory: %w", err)
	}

	// Convert to absolute path for Docker volumes
	absAppStoragePath, err := filepath.Abs(appStoragePath)
	if err != nil {
		return nil, fmt.Errorf("failed to get absolute path: %w", err)
	}

	// Pass app storage root to terraform (must be absolute for Docker)
	variables["zp_module_storage"] = absAppStoragePath

	h.logger.Info("Prepared system variables", "module", moduleName, "variables", variables)
	return variables, nil
}

// createSharedNetworksForReferences creates shared networks for referenced modules
func (h *LinkHandlers) createSharedNetworksForReferences(targetModule string, config map[string]interface{}) error {
	ctx := context.Background()

	for _, value := range config {
		if ref, isRef := parseAppReference(value); isRef {
			h.logger.Info("Creating shared network for module reference", "from", ref.FromModule, "to", targetModule, "output", ref.Output)

			// Create shared network for direct communication between linked modules
			if err := h.ensureSharedNetwork(ctx, ref.FromModule, targetModule); err != nil {
				h.logger.Warn("Failed to create shared network", "from", ref.FromModule, "to", targetModule, "error", err)
				// Don't return error - network connection failure shouldn't break linking
			}
		}
	}

	return nil
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
