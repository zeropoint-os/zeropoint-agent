package validator

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"

	"zeropoint-agent/internal/terraform"
)

// ValidationError represents a contract violation
type ValidationError struct {
	Field   string
	Message string
}

func (e *ValidationError) Error() string {
	return fmt.Sprintf("%s: %s", e.Field, e.Message)
}

// ValidateAppModule validates that a Terraform module conforms to the zeropoint app contract
func ValidateAppModule(modulePath, appID string) error {
	// Step 1: Initialize Terraform
	executor, err := terraform.NewExecutor(modulePath)
	if err != nil {
		return fmt.Errorf("failed to create terraform executor: %w", err)
	}

	if err := executor.Init(); err != nil {
		return fmt.Errorf("terraform init failed: %w", err)
	}

	// Step 2: Create plan with dummy variables (validates variables exist)
	planFile := "plan.tfplan"                            // Relative to module directory
	defer os.Remove(filepath.Join(modulePath, planFile)) // Clean up with full path

	variables := map[string]string{
		"app_id":       appID,
		"network_name": fmt.Sprintf("zeropoint-app-%s", appID),
		"arch":         "amd64",
		"gpu_vendor":   "",
	}

	if err := executor.Plan(planFile, variables); err != nil {
		return fmt.Errorf("terraform plan failed (check required variables): %w", err)
	}

	// Step 3: Show plan as JSON and validate resources
	planJSON, err := executor.Show(planFile)
	if err != nil {
		return fmt.Errorf("terraform show failed: %w", err)
	}

	if err := validateResources(planJSON, appID); err != nil {
		return err
	}

	return nil
}

// validateResources checks the plan JSON for contract compliance
func validateResources(planJSON []byte, appID string) error {
	var plan struct {
		ResourceChanges []struct {
			Type   string `json:"type"`
			Name   string `json:"name"`
			Change struct {
				After map[string]interface{} `json:"after"`
			} `json:"change"`
		} `json:"resource_changes"`
	}

	if err := json.Unmarshal(planJSON, &plan); err != nil {
		return fmt.Errorf("failed to parse plan JSON: %w", err)
	}

	foundMain := false

	for _, rc := range plan.ResourceChanges {
		// Check 1: Reject docker_network resources (zeropoint manages networks)
		if rc.Type == "docker_network" {
			return &ValidationError{
				Field:   "resources",
				Message: "app modules must not create docker_network resources (network is provided via var.network_name)",
			}
		}

		// Check 2: Validate main container
		if rc.Type == "docker_container" && rc.Name == appID+"_main" {
			foundMain = true

			// Validate runtime container name matches ${app_id}-main
			if name, ok := rc.Change.After["name"].(string); ok {
				expected := appID + "-main"
				if name != expected {
					return &ValidationError{
						Field:   "docker_container." + rc.Name,
						Message: fmt.Sprintf("container name must be '%s', got '%s'", expected, name),
					}
				}
			} else {
				return &ValidationError{
					Field:   "docker_container." + rc.Name,
					Message: "container name not found in plan",
				}
			}

			// Validate no host port bindings
			if ports, ok := rc.Change.After["ports"].([]interface{}); ok && len(ports) > 0 {
				return &ValidationError{
					Field:   "docker_container." + rc.Name,
					Message: "host port bindings not allowed (use service discovery instead)",
				}
			}
		}
	}

	// Check 3: Ensure main container exists
	if !foundMain {
		return &ValidationError{
			Field:   "resources",
			Message: fmt.Sprintf("missing required resource: docker_container.%s_main", appID),
		}
	}

	return nil
}
