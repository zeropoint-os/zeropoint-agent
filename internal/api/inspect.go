package api

import (
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"

	"zeropoint-agent/internal/hcl"
	"zeropoint-agent/internal/terraform"

	"github.com/gorilla/mux"
)

// InspectHandlers holds HTTP handlers for app inspection
type InspectHandlers struct {
	appsDir string
	logger  *slog.Logger
}

// NewInspectHandlers creates a new inspect handlers instance
func NewInspectHandlers(appsDir string, logger *slog.Logger) *InspectHandlers {
	return &InspectHandlers{
		appsDir: appsDir,
		logger:  logger,
	}
}

// InspectResponse represents the response for module inspection
type InspectResponse struct {
	ModuleID string                  `json:"module_id"`
	Inputs   map[string]InputSchema  `json:"inputs"`
	Outputs  map[string]OutputSchema `json:"outputs"`
}

// InputSchema represents metadata about an input variable
type InputSchema struct {
	Type          string      `json:"type"`
	Description   string      `json:"description,omitempty"`
	DefaultValue  interface{} `json:"default_value,omitempty"`
	CurrentValue  interface{} `json:"current_value,omitempty"`
	Required      bool        `json:"required"`
	SystemManaged bool        `json:"system_managed"` // True for zp_* variables
}

// OutputSchema represents metadata about an output
type OutputSchema struct {
	Description  string      `json:"description,omitempty"`
	CurrentValue interface{} `json:"current_value,omitempty"`
}

// InspectModule handles GET /modules/{module_id}/inspect?source_url=...
// @Summary Inspect a module's inputs and outputs
// @Description Fetch and parse a Terraform module to extract inputs, outputs, and current values
// @Tags modules
// @Param module_id path string true "Module ID"
// @Param source_url query string false "Git repository URL (if not installed)"
// @Success 200 {object} InspectResponse
// @Failure 400 {string} string "Bad request"
// @Failure 500 {string} string "Internal error"
// @Router /modules/{module_id}/inspect [get]
func (h *InspectHandlers) InspectModule(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	moduleID := vars["module_id"]
	sourceURL := r.URL.Query().Get("source_url")

	var modulePath string
	var cleanupFunc func()

	if sourceURL != "" {
		// Clone from source URL
		tmpPath, cleanup, err := h.cloneModule(sourceURL)
		if err != nil {
			http.Error(w, fmt.Sprintf("failed to clone module: %v", err), http.StatusBadRequest)
			return
		}
		modulePath = tmpPath
		cleanupFunc = cleanup
		defer cleanupFunc()
	} else {
		// Use installed module
		modulePath = filepath.Join(h.appsDir, moduleID)
		if _, err := os.Stat(modulePath); os.IsNotExist(err) {
			http.Error(w, fmt.Sprintf("module not installed and no source_url provided"), http.StatusNotFound)
			return
		}
	}

	// Parse inputs
	inputs, err := hcl.ParseModuleInputs(modulePath)
	if err != nil {
		http.Error(w, fmt.Sprintf("failed to parse inputs: %v", err), http.StatusInternalServerError)
		return
	}

	// Parse outputs
	outputs, err := hcl.ParseModuleOutputs(modulePath)
	if err != nil {
		http.Error(w, fmt.Sprintf("failed to parse outputs: %v", err), http.StatusInternalServerError)
		return
	}

	// Get current values if app is installed
	var currentInputs map[string]string
	var currentOutputs map[string]*terraform.OutputMeta

	if sourceURL == "" {
		// Module is installed, try to get current values
		currentInputs = h.getCurrentInputs(moduleID)
		currentOutputs, _ = h.getCurrentOutputs(modulePath)
	}

	// Build response
	response := InspectResponse{
		ModuleID: moduleID,
		Inputs:   make(map[string]InputSchema),
		Outputs:  make(map[string]OutputSchema),
	}

	for name, variable := range inputs {
		schema := InputSchema{
			Type:          variable.Type,
			Description:   variable.Description,
			DefaultValue:  variable.Default,
			Required:      variable.Required,
			SystemManaged: strings.HasPrefix(name, "zp_"), // Mark zp_* variables as system-managed
		}

		// Add current value if available
		if currentVal, ok := currentInputs[name]; ok {
			schema.CurrentValue = currentVal
		}

		response.Inputs[name] = schema
	}

	for name, output := range outputs {
		schema := OutputSchema{
			Description: output.Description,
		}

		// Add current value if available
		if currentOutputs != nil {
			if currentOut, ok := currentOutputs[name]; ok {
				schema.CurrentValue = currentOut.Value
			}
		}

		response.Outputs[name] = schema
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(response)
}

// cloneModule clones a git repository to a temporary directory
func (h *InspectHandlers) cloneModule(sourceURL string) (string, func(), error) {
	tmpDir, err := os.MkdirTemp("", "zeropoint-inspect-*")
	if err != nil {
		return "", nil, fmt.Errorf("failed to create temp dir: %w", err)
	}

	cleanup := func() {
		os.RemoveAll(tmpDir)
	}

	cmd := exec.Command("git", "clone", "--depth", "1", sourceURL, tmpDir)
	if output, err := cmd.CombinedOutput(); err != nil {
		cleanup()
		return "", nil, fmt.Errorf("git clone failed: %w\nOutput: %s", err, output)
	}

	return tmpDir, cleanup, nil
}

// getCurrentInputs retrieves the current input values for an installed module
// TODO: Load from persisted module metadata
func (h *InspectHandlers) getCurrentInputs(moduleID string) map[string]string {
	// For now, return empty - will be populated once we persist installation variables
	return make(map[string]string)
}

// getCurrentOutputs retrieves the current output values for an installed module
func (h *InspectHandlers) getCurrentOutputs(modulePath string) (map[string]*terraform.OutputMeta, error) {
	executor, err := terraform.NewExecutor(modulePath)
	if err != nil {
		return nil, err
	}

	return executor.Output()
}
