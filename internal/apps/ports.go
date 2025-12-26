package apps

import (
	"encoding/json"
	"fmt"
	"path/filepath"
	"strings"

	"zeropoint-agent/internal/terraform"
)

// LoadContainers reads all {container}_ports and {container}_mounts outputs from a Terraform module
// and returns a map of container configurations
func LoadContainers(modulePath string, appID string) (map[string]Container, error) {
	executor, err := terraform.NewExecutor(modulePath)
	if err != nil {
		return nil, fmt.Errorf("failed to create terraform executor: %w", err)
	}

	outputs, err := executor.Output()
	if err != nil {
		return nil, fmt.Errorf("failed to read terraform outputs: %w", err)
	}

	containers := make(map[string]Container)

	// First pass: find all container names from _ports outputs
	containerNames := make(map[string]bool)
	for outputName := range outputs {
		if strings.HasSuffix(outputName, "_ports") {
			containerName := strings.TrimSuffix(outputName, "_ports")
			containerNames[containerName] = true
		}
	}

	// Second pass: load ports and mounts for each container
	for containerName := range containerNames {
		container := Container{}

		// Load ports
		portsOutputName := containerName + "_ports"
		if portsOutput, exists := outputs[portsOutputName]; exists {
			var portsValue map[string]interface{}
			if jsonData, ok := portsOutput.Value.(json.RawMessage); ok {
				if err := json.Unmarshal(jsonData, &portsValue); err != nil {
					return nil, fmt.Errorf("failed to unmarshal %s: %w", portsOutputName, err)
				}
			} else if m, ok := portsOutput.Value.(map[string]interface{}); ok {
				portsValue = m
			} else {
				return nil, fmt.Errorf("%s value has unexpected type: %T", portsOutputName, portsOutput.Value)
			}

			ports, err := parsePorts(portsValue)
			if err != nil {
				return nil, fmt.Errorf("failed to parse %s: %w", portsOutputName, err)
			}
			container.Ports = ports
		}

		// Load mounts (optional)
		mountsOutputName := containerName + "_mounts"
		if mountsOutput, exists := outputs[mountsOutputName]; exists {
			var mountsValue map[string]interface{}
			if jsonData, ok := mountsOutput.Value.(json.RawMessage); ok {
				if err := json.Unmarshal(jsonData, &mountsValue); err != nil {
					return nil, fmt.Errorf("failed to unmarshal %s: %w", mountsOutputName, err)
				}
			} else if m, ok := mountsOutput.Value.(map[string]interface{}); ok {
				mountsValue = m
			} else {
				return nil, fmt.Errorf("%s value has unexpected type: %T", mountsOutputName, mountsOutput.Value)
			}

			mounts, err := parseMounts(mountsValue, appID, containerName)
			if err != nil {
				return nil, fmt.Errorf("failed to parse %s: %w", mountsOutputName, err)
			}
			container.Mounts = mounts
		}

		containers[containerName] = container
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

// parseMounts converts raw Terraform output map to Mount structs with host paths
func parseMounts(raw map[string]interface{}, appID string, containerName string) (map[string]Mount, error) {
	mounts := make(map[string]Mount)

	for mountName, mountConfigRaw := range raw {
		mountConfig, ok := mountConfigRaw.(map[string]interface{})
		if !ok {
			return nil, fmt.Errorf("mount '%s' has invalid configuration", mountName)
		}

		containerPath, ok := mountConfig["container_path"].(string)
		if !ok {
			return nil, fmt.Errorf("mount '%s' missing or invalid 'container_path' field", mountName)
		}

		description, ok := mountConfig["description"].(string)
		if !ok {
			return nil, fmt.Errorf("mount '%s' missing or invalid 'description' field", mountName)
		}

		// Read-only is optional, defaults to false
		readOnly := false
		if ro, ok := mountConfig["read_only"].(bool); ok {
			readOnly = ro
		}

		// Generate host path: /data/apps/{app_id}/{container}/{mount_name}
		hostPath := filepath.Join(GetDataDir(), appID, containerName, mountName)

		mounts[mountName] = Mount{
			ContainerPath: containerPath,
			HostPath:      hostPath,
			Description:   description,
			ReadOnly:      readOnly,
		}
	}

	return mounts, nil
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
