package validator

import (
	"fmt"
	"strings"
	"unicode"

	"zeropoint-agent/internal/hcl"
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
	// Parse HCL to extract outputs
	outputs, err := hcl.ParseModuleOutputs(modulePath)
	if err != nil {
		return fmt.Errorf("failed to parse module outputs: %w", err)
	}

	// Validate required outputs exist
	var errors []string

	// Check for main container output
	_, hasMain := outputs["main"]
	if !hasMain {
		errors = append(errors, "missing required output: 'main' (must reference docker_container resource)")
	}
	// Note: output value may be nil if it references a resource (e.g., docker_container.main)
	// This is expected and OK - we just need the output to exist

	// Check for main_ports output
	mainPortsOutput, hasMainPorts := outputs["main_ports"]
	if !hasMainPorts {
		errors = append(errors, "missing required output: 'main_ports' (must be a map of port definitions for main container)")
	} else {
		// Validate ports structure
		portsMap, ok := mainPortsOutput.Value.(map[string]interface{})
		if !ok {
			errors = append(errors, "output 'main_ports' must be a map of port configurations")
		} else {
			if portErrors := ValidateContainerPorts(portsMap); len(portErrors) > 0 {
				for _, err := range portErrors {
					errors = append(errors, fmt.Sprintf("main_ports: %s", err))
				}
			}
		}
	}

	// Validate additional container outputs (pattern: {container}_ports)
	for outputName := range outputs {
		if strings.HasSuffix(outputName, "_ports") && outputName != "main_ports" {
			// Validate this container's ports
			portsOutput := outputs[outputName]
			portsMap, ok := portsOutput.Value.(map[string]interface{})
			if !ok {
				errors = append(errors, fmt.Sprintf("output '%s' must be a map of port configurations", outputName))
			} else {
				if portErrors := ValidateContainerPorts(portsMap); len(portErrors) > 0 {
					for _, err := range portErrors {
						errors = append(errors, fmt.Sprintf("%s: %s", outputName, err))
					}
				}
			}
		}
	}

	if len(errors) > 0 {
		return fmt.Errorf("module validation failed:\n  - %s", strings.Join(errors, "\n  - "))
	}

	return nil
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

// ValidateContainerMounts validates the structure of a {container}_mounts output
func ValidateContainerMounts(mounts map[string]interface{}) []string {
	var errors []string

	if len(mounts) == 0 {
		return []string{"container mounts is empty (at least one mount must be defined if output exists)"}
	}

	for mountName, mountConfig := range mounts {
		mountMap, ok := mountConfig.(map[string]interface{})
		if !ok {
			errors = append(errors, fmt.Sprintf("mount '%s' is not a valid configuration map", mountName))
			continue
		}

		// Validate required fields
		containerPath, hasContainerPath := mountMap["container_path"].(string)
		if !hasContainerPath {
			errors = append(errors, fmt.Sprintf("mount '%s' missing required field: 'container_path'", mountName))
		} else if containerPath == "" {
			errors = append(errors, fmt.Sprintf("mount '%s' has empty 'container_path'", mountName))
		} else if !strings.HasPrefix(containerPath, "/") {
			errors = append(errors, fmt.Sprintf("mount '%s' container_path must be absolute (start with /): '%s'", mountName, containerPath))
		}

		if _, hasDesc := mountMap["description"]; !hasDesc {
			errors = append(errors, fmt.Sprintf("mount '%s' missing required field: 'description'", mountName))
		}

		// Validate optional read_only field
		if readOnly, hasReadOnly := mountMap["read_only"]; hasReadOnly {
			if _, ok := readOnly.(bool); !ok {
				errors = append(errors, fmt.Sprintf("mount '%s' has invalid 'read_only' (must be boolean)", mountName))
			}
		}

		// Validate mount name is a valid identifier (alphanumeric + underscores/hyphens)
		if !isValidMountName(mountName) {
			errors = append(errors, fmt.Sprintf("mount name '%s' is not valid (use alphanumeric, hyphens, underscores)", mountName))
		}
	}

	return errors
}

// isValidMountName checks if a string is a valid mount name
func isValidMountName(s string) bool {
	if len(s) == 0 || len(s) > 63 {
		return false
	}
	for i, ch := range s {
		if i == 0 && !unicode.IsLetter(ch) {
			return false // Must start with letter
		}
		if !unicode.IsLetter(ch) && !unicode.IsDigit(ch) && ch != '-' && ch != '_' {
			return false
		}
	}
	return true
}
