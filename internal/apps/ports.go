package apps

import (
	"encoding/json"
	"fmt"
	"strings"

	"zeropoint-agent/internal/terraform"
)

// LoadContainers reads all {container}_ports outputs from a Terraform module
// and returns a map of container configurations
func LoadContainers(modulePath string) (map[string]Container, error) {
	executor, err := terraform.NewExecutor(modulePath)
	if err != nil {
		return nil, fmt.Errorf("failed to create terraform executor: %w", err)
	}

	outputs, err := executor.Output()
	if err != nil {
		return nil, fmt.Errorf("failed to read terraform outputs: %w", err)
	}

	containers := make(map[string]Container)

	// Find all outputs ending in _ports
	for outputName, outputValue := range outputs {
		if !strings.HasSuffix(outputName, "_ports") {
			continue
		}

		// Extract container name (e.g., "main_ports" -> "main")
		containerName := strings.TrimSuffix(outputName, "_ports")

		// Handle json.RawMessage from terraform-exec
		var portsValue map[string]interface{}
		if jsonData, ok := outputValue.Value.(json.RawMessage); ok {
			if err := json.Unmarshal(jsonData, &portsValue); err != nil {
				return nil, fmt.Errorf("failed to unmarshal %s: %w", outputName, err)
			}
		} else if m, ok := outputValue.Value.(map[string]interface{}); ok {
			portsValue = m
		} else {
			return nil, fmt.Errorf("%s value has unexpected type: %T", outputName, outputValue.Value)
		}

		ports, err := parsePorts(portsValue)
		if err != nil {
			return nil, fmt.Errorf("failed to parse %s: %w", outputName, err)
		}

		containers[containerName] = Container{
			Ports: ports,
		}
	}

	return containers, nil
}

// parsePorts converts raw Terraform output map to Port structs
func parsePorts(raw map[string]interface{}) (map[string]Port, error) {
	ports := make(map[string]Port)

	for portName, portConfigRaw := range raw {
		portConfig, ok := portConfigRaw.(map[string]interface{})
		if !ok {
			return nil, fmt.Errorf("port '%s' has invalid configuration", portName)
		}

		portNum, ok := portConfig["port"].(float64) // JSON numbers are float64
		if !ok {
			return nil, fmt.Errorf("port '%s' missing or invalid 'port' field", portName)
		}

		protocol, ok := portConfig["protocol"].(string)
		if !ok {
			return nil, fmt.Errorf("port '%s' missing or invalid 'protocol' field", portName)
		}

		description, ok := portConfig["description"].(string)
		if !ok {
			return nil, fmt.Errorf("port '%s' missing or invalid 'description' field", portName)
		}

		// Transport is optional, defaults to "tcp"
		transport := "tcp"
		if t, ok := portConfig["transport"].(string); ok {
			transport = t
		}

		// Default is optional
		isDefault := false
		if d, ok := portConfig["default"].(bool); ok {
			isDefault = d
		}

		ports[portName] = Port{
			Port:        int(portNum),
			Protocol:    protocol,
			Transport:   transport,
			Description: description,
			IsDefault:   isDefault,
		}
	}

	return ports, nil
}

// GetDefaultPort returns the default port from a port map, or the first port if no default is set
func GetDefaultPort(ports map[string]Port) (string, Port, error) {
	if len(ports) == 0 {
		return "", Port{}, fmt.Errorf("no ports defined")
	}

	// Look for default port
	for name, port := range ports {
		if port.IsDefault {
			return name, port, nil
		}
	}

	// No default set, return first port (alphabetically for determinism)
	var firstName string
	for name := range ports {
		if firstName == "" || name < firstName {
			firstName = name
		}
	}

	return firstName, ports[firstName], nil
}
