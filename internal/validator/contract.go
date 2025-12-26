package validator

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"unicode"

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

	// Step 4: Validate outputs (after apply, we'd read actual values)
	// For now, validate they exist in the module definition
	if err := validateOutputs(modulePath); err != nil {
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

// validateOutputs validates that required outputs exist and conform to contract
func validateOutputs(modulePath string) error {
	// Read the module files to check for output declarations
	// This is a basic check - full validation happens after terraform apply
	mainTf := filepath.Join(modulePath, "main.tf")
	data, err := os.ReadFile(mainTf)
	if err != nil {
		return fmt.Errorf("failed to read main.tf: %w", err)
	}

	content := string(data)

	// Check for required outputs (basic string search)
	if !containsOutput(content, "main") {
		return &ValidationError{
			Field:   "outputs",
			Message: "missing required output: 'main' (must reference docker_container resource)",
		}
	}

	if !containsOutput(content, "main_ports") {
		return &ValidationError{
			Field:   "outputs",
			Message: "missing required output: 'main_ports' (must be a map of port definitions for main container)",
		}
	}

	return nil
}

// containsOutput checks if an output declaration exists in the terraform content
func containsOutput(content, outputName string) bool {
	// Simple check for output "name" { pattern
	return len(content) > 0 &&
		(jsonContains(content, fmt.Sprintf(`output "%s"`, outputName)) ||
			jsonContains(content, fmt.Sprintf("output \"%s\"", outputName)))
}

func jsonContains(s, substr string) bool {
	for i := 0; i <= len(s)-len(substr); i++ {
		if s[i:i+len(substr)] == substr {
			return true
		}
	}
	return false
}

// ValidateContainerPorts validates the structure of a {container}_ports output
func ValidateContainerPorts(ports map[string]interface{}) []string {
	var errors []string

	if len(ports) == 0 {
		return []string{"container ports is empty (at least one port must be defined)"}
	}

	validProtocols := map[string]bool{"http": true, "grpc": true, "tcp": true}
	validTransports := map[string]bool{"tcp": true, "udp": true}
	defaultCount := 0

	for portName, portConfig := range ports {
		portMap, ok := portConfig.(map[string]interface{})
		if !ok {
			errors = append(errors, fmt.Sprintf("port '%s' is not a valid configuration map", portName))
			continue
		}

		// Validate required fields
		if _, hasPort := portMap["port"]; !hasPort {
			errors = append(errors, fmt.Sprintf("port '%s' missing required field: 'port'", portName))
		}

		protocol, hasProtocol := portMap["protocol"].(string)
		if !hasProtocol {
			errors = append(errors, fmt.Sprintf("port '%s' missing required field: 'protocol'", portName))
		} else if !validProtocols[protocol] {
			errors = append(errors,
				fmt.Sprintf("port '%s' has invalid protocol: '%s' (must be http, grpc, or tcp)",
					portName, protocol))
		}

		if _, hasDesc := portMap["description"]; !hasDesc {
			errors = append(errors, fmt.Sprintf("port '%s' missing required field: 'description'", portName))
		}

		// Validate optional transport field
		if transport, hasTransport := portMap["transport"].(string); hasTransport {
			if !validTransports[transport] {
				errors = append(errors,
					fmt.Sprintf("port '%s' has invalid transport: '%s' (must be tcp or udp)",
						portName, transport))
			}
		}

		// Track default ports
		if isDefault, ok := portMap["default"].(bool); ok && isDefault {
			defaultCount++
		}

		// Validate port name is a valid DNS label
		if !isValidDNSLabel(portName) {
			errors = append(errors,
				fmt.Sprintf("port name '%s' is not a valid DNS label (alphanumeric + hyphens, start with letter)",
					portName))
		}
	}

	if defaultCount > 1 {
		errors = append(errors, "multiple ports have default=true (only one allowed)")
	}

	return errors
}

// isValidDNSLabel checks if a string is a valid DNS label
func isValidDNSLabel(s string) bool {
	if len(s) == 0 || len(s) > 63 {
		return false
	}
	if !unicode.IsLetter(rune(s[0])) {
		return false
	}
	for _, ch := range s {
		if !unicode.IsLetter(ch) && !unicode.IsDigit(ch) && ch != '-' {
			return false
		}
	}
	return true
}
