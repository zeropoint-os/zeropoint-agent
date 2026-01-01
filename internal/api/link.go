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

	"zeropoint-agent/internal/system"
	"zeropoint-agent/internal/terraform"

	"github.com/gorilla/mux"
)

// LinkHandlers holds HTTP handlers for app linking
type LinkHandlers struct {
	appsDir       string
	exposureStore *ExposureStore
	logger        *slog.Logger
}

// NewLinkHandlers creates a new link handlers instance
func NewLinkHandlers(appsDir string, exposureStore *ExposureStore, logger *slog.Logger) *LinkHandlers {
	return &LinkHandlers{
		appsDir:       appsDir,
		exposureStore: exposureStore,
		logger:        logger,
	}
}

// ErrorResponse represents an API error response
type ErrorResponse struct {
	Error   string `json:"error"`
	Message string `json:"message,omitempty"`
}

// LinkRequest represents the request to link multiple apps
type LinkRequest struct {
	Apps map[string]map[string]interface{} `json:"apps"`
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

// RegisterRoutes registers the link-related routes
func (h *LinkHandlers) RegisterRoutes(router *mux.Router) {
	router.HandleFunc("/link", h.LinkApps).Methods("POST")
}

// LinkApps handles POST /link
// @Summary Link multiple apps with cross-references
// @Description Apply configuration to multiple apps where inputs can reference other apps' outputs
// @Tags apps
// @Accept json
// @Produce json
// @Param request body LinkRequest true "Link configuration"
// @Success 200 {object} LinkResponse
// @Failure 400 {object} ErrorResponse
// @Failure 500 {object} ErrorResponse
// @Router /link [post]
func (h *LinkHandlers) LinkApps(w http.ResponseWriter, r *http.Request) {
	var req LinkRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		h.logger.Error("Failed to decode link request", "error", err)
		http.Error(w, "Invalid JSON in request body", http.StatusBadRequest)
		return
	}

	h.logger.Info("Linking apps", "apps", getAppNames(req.Apps))

	// Step 1: Validate all apps exist
	if err := h.validateAppsExist(req.Apps); err != nil {
		h.logger.Error("App validation failed", "error", err)
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	// Step 2: Analyze dependencies and determine order
	graph, err := AnalyzeDependencies(req.Apps)
	if err != nil {
		h.logger.Error("Dependency analysis failed", "error", err)
		http.Error(w, fmt.Sprintf("Dependency analysis failed: %v", err), http.StatusBadRequest)
		return
	}

	order, err := graph.TopologicalSort()
	if err != nil {
		h.logger.Error("Topological sort failed", "error", err)
		http.Error(w, fmt.Sprintf("Dependency resolution failed: %v", err), http.StatusBadRequest)
		return
	}

	h.logger.Info("Determined app order", "order", order)

	// Step 3: Backup states
	stateManager := NewStateManager(h.appsDir)
	backup, err := stateManager.BackupStates(order)
	if err != nil {
		h.logger.Error("State backup failed", "error", err)
		http.Error(w, fmt.Sprintf("State backup failed: %v", err), http.StatusInternalServerError)
		return
	}

	// Step 4: Apply configurations in dependency order
	errors := make(map[string]string)
	appliedApps := []string{}

	for _, appName := range order {
		config, exists := req.Apps[appName]
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

			response := LinkResponse{
				Success:      false,
				Message:      fmt.Sprintf("Configuration failed for app %s", appName),
				AppliedOrder: appliedApps,
				Errors:       errors,
			}

			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusInternalServerError)
			json.NewEncoder(w).Encode(response)
			return
		}

		appliedApps = append(appliedApps, appName)

		// Create exposures for any apps this app references
		if err := h.createExposuresForReferences(appName, config); err != nil {
			h.logger.Warn("Failed to create automatic exposures", "app", appName, "error", err)
			// Don't fail the entire operation for exposure creation failures
		}
	}

	// Success - cleanup backup files
	if err := stateManager.CleanupBackup(backup); err != nil {
		h.logger.Warn("Failed to cleanup backup files", "error", err)
	}

	response := LinkResponse{
		Success:      true,
		Message:      "All apps linked successfully",
		AppliedOrder: appliedApps,
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(response)
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
	appStoragePath := filepath.Join("/workspaces/zeropoint-agent/data/apps", appName)
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

// createExposuresForReferences automatically creates exposures for referenced apps
func (h *LinkHandlers) createExposuresForReferences(targetApp string, config map[string]interface{}) error {
	ctx := context.Background()

	for _, value := range config {
		if ref, isRef := parseAppReference(value); isRef {
			h.logger.Info("Creating automatic exposure", "from", ref.FromApp, "to", targetApp, "output", ref.Output)

			// For linked apps, ensure they're on the same network for direct communication
			// No need for envoy routing - they can reach each other via container names
			if err := h.ensureSharedNetwork(ctx, ref.FromApp, targetApp); err != nil {
				h.logger.Warn("Failed to create shared network", "from", ref.FromApp, "to", targetApp, "error", err)
				// Don't return error - network connection failure shouldn't break linking
			}
		}
	}

	return nil
}

// createHTTPExposureFromPorts creates an HTTP exposure based on an app's port configuration
func (h *LinkHandlers) createHTTPExposureFromPorts(ctx context.Context, appName string) error {
	h.logger.Info("Starting exposure creation", "app", appName)

	// Get the app's port configuration
	outputs, err := h.getAppOutputs(appName)
	if err != nil {
		h.logger.Error("Failed to get outputs", "app", appName, "error", err)
		return fmt.Errorf("failed to get outputs for app %s: %w", appName, err)
	}
	h.logger.Info("Retrieved outputs", "app", appName, "output_keys", getKeys(outputs))

	mainPorts, exists := outputs["main_ports"]
	if !exists {
		h.logger.Error("main_ports output not found", "app", appName, "available_outputs", getKeys(outputs))
		return fmt.Errorf("main_ports output not found for app %s", appName)
	}
	h.logger.Info("Found main_ports", "app", appName, "main_ports_value", mainPorts, "main_ports_type", fmt.Sprintf("%T", mainPorts))

	// Parse the ports structure to find the default API port
	var portsMap map[string]interface{}

	// Handle different types for main_ports
	switch v := mainPorts.(type) {
	case map[string]interface{}:
		portsMap = v
	case json.RawMessage:
		// Unmarshal JSON raw message into map
		if err := json.Unmarshal(v, &portsMap); err != nil {
			h.logger.Error("Failed to unmarshal main_ports JSON", "app", appName, "error", err)
			return fmt.Errorf("failed to unmarshal main_ports JSON for app %s: %w", appName, err)
		}
	default:
		h.logger.Error("main_ports is not a supported type", "app", appName, "actual_type", fmt.Sprintf("%T", mainPorts))
		return fmt.Errorf("main_ports is not a supported type for app %s: %T", appName, mainPorts)
	}

	h.logger.Info("Parsed ports map", "app", appName, "port_names", getKeys(portsMap))

	// Look for the default port (usually "api")
	for portName, portInfo := range portsMap {
		h.logger.Info("Examining port", "app", appName, "port_name", portName, "port_info", portInfo, "port_info_type", fmt.Sprintf("%T", portInfo))

		portMap, ok := portInfo.(map[string]interface{})
		if !ok {
			h.logger.Warn("Port info is not a map", "app", appName, "port_name", portName, "actual_type", fmt.Sprintf("%T", portInfo))
			continue
		}

		// Check if this is the default port
		if defaultPort, exists := portMap["default"]; exists && defaultPort == true {
			h.logger.Info("Found default port", "app", appName, "port_name", portName, "port_config", portMap)

			port, exists := portMap["port"]
			if !exists {
				h.logger.Warn("No port number in default port config", "app", appName, "port_name", portName)
				continue
			}

			var portNum uint32
			switch v := port.(type) {
			case float64:
				portNum = uint32(v)
			case int:
				portNum = uint32(v)
			case string:
				if parsed, err := strconv.ParseUint(v, 10, 32); err == nil {
					portNum = uint32(parsed)
				} else {
					h.logger.Warn("Failed to parse port number", "app", appName, "port_name", portName, "port_value", v, "error", err)
					continue
				}
			default:
				h.logger.Warn("Unsupported port number type", "app", appName, "port_name", portName, "port_value", v, "type", fmt.Sprintf("%T", v))
				continue
			}

			// Create the exposure
			hostname := fmt.Sprintf("%s-%s", appName, portName)
			protocol := "http" // Default to http, could be extracted from portMap["protocol"]

			h.logger.Info("Creating exposure", "app", appName, "hostname", hostname, "port", portNum, "protocol", protocol)

			_, _, err := h.exposureStore.CreateExposure(ctx, appName, protocol, hostname, portNum)
			if err != nil {
				h.logger.Error("Failed to create exposure", "app", appName, "hostname", hostname, "error", err)
				return fmt.Errorf("failed to create exposure %s: %w", hostname, err)
			}

			h.logger.Info("Successfully created exposure", "app", appName, "hostname", hostname, "port", portNum)
			return nil // Created exposure for the default port
		}
	}

	h.logger.Error("No default port found in main_ports", "app", appName, "available_ports", getKeys(portsMap))
	return fmt.Errorf("no default port found in main_ports for app %s", appName)
}

// Helper function to get map keys for logging
func getKeys(m map[string]interface{}) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	return keys
}

// ensureConsumingAppNetwork connects a consuming app to zeropoint-network
func (h *LinkHandlers) ensureConsumingAppNetwork(ctx context.Context, appName string) error {
	h.logger.Info("Connecting consuming app to zeropoint-network", "app", appName)

	// Use the exposureStore's ensureNetwork function to connect the app
	if err := h.exposureStore.EnsureNetwork(ctx, appName); err != nil {
		return fmt.Errorf("failed to connect app to zeropoint-network: %w", err)
	}

	h.logger.Info("Successfully connected consuming app to zeropoint-network", "app", appName)
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

	// Connect both apps to the shared network using exposureStore's docker client
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
	// We'll need access to docker client - let's use the exposureStore's client
	return h.exposureStore.EnsureAppOnNetwork(ctx, appName, networkName)
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
