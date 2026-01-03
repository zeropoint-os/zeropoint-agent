package modules

import (
	"context"
	"fmt"
	"os"
	"path/filepath"

	"github.com/moby/moby/client"
)

// Port represents a port exposed by a container
type Port struct {
	Port        int    `json:"port"`              // Port number
	Protocol    string `json:"protocol"`          // Application protocol: http, grpc, tcp
	Transport   string `json:"transport"`         // Transport protocol: tcp, udp
	Description string `json:"description"`       // Human-readable description
	IsDefault   bool   `json:"default,omitempty"` // Is this the default/primary port?
}

// Mount represents a bind-mount for persistent storage
type Mount struct {
	ContainerPath string `json:"container_path"` // Path inside the container
	HostPath      string `json:"host_path"`      // Path on the host (managed by zeropoint)
	Description   string `json:"description"`    // Human-readable description
	ReadOnly      bool   `json:"read_only"`      // Mount as read-only
}

// Container represents a container and its exposed ports and mounts
type Container struct {
	Ports  map[string]Port  `json:"ports,omitempty"`  // Port configurations (from {container}_ports output)
	Mounts map[string]Mount `json:"mounts,omitempty"` // Mount configurations (from {container}_mounts output)
}

// Module represents an installed module managed by zeropoint-agent.
// State is discovered from filesystem + Terraform outputs + Docker API.
type Module struct {
	ID            string               `json:"id"`                       // Module identifier (directory name)
	ModulePath    string               `json:"module_path"`              // Path to Terraform module (e.g., "modules/ollama")
	State         string               `json:"state"`                    // Runtime state: "running" | "stopped" | "crashed" | "unknown"
	ContainerID   string               `json:"container_id,omitempty"`   // Docker container ID (for main container)
	ContainerName string               `json:"container_name,omitempty"` // Docker container name (for main container)
	IPAddress     string               `json:"ip_address,omitempty"`     // Container IP address (for main container)
	Containers    map[string]Container `json:"containers,omitempty"`     // Module containers with their ports (from {container}_ports outputs)
}

// Module states
const (
	StateRunning = "running"
	StateStopped = "stopped"
	StateCrashed = "crashed"
	StateUnknown = "unknown"
)

// GetStorageRoot returns the storage root directory from environment or default
func GetStorageRoot() string {
	root := os.Getenv("MODULE_STORAGE_ROOT")
	if root == "" {
		return "."
	}
	return root
}

// GetModulesDir returns the modules directory path
func GetModulesDir() string {
	return filepath.Join(GetStorageRoot(), "modules")
}

// GetDataDir returns the data directory path for module storage
func GetDataDir() string {
	return filepath.Join(GetStorageRoot(), "data", "modules")
}

// GetContainerStatus queries Docker to get the container's runtime state
func (m *Module) GetContainerStatus(ctx context.Context, docker *client.Client) error {
	containerName := fmt.Sprintf("%s-main", m.ID)

	containers, err := docker.ContainerList(ctx, client.ContainerListOptions{All: true})
	if err != nil {
		return err
	}

	for _, c := range containers.Items {
		for _, name := range c.Names {
			if name == "/"+containerName || name == containerName {
				m.ContainerID = c.ID[:12]
				m.ContainerName = containerName
				m.State = string(c.State)

				// Get IP address if running
				if c.State == "running" {
					inspect, err := docker.ContainerInspect(ctx, c.ID, client.ContainerInspectOptions{})
					if err == nil {
						for _, network := range inspect.Container.NetworkSettings.Networks {
							if network.IPAddress.IsValid() {
								m.IPAddress = network.IPAddress.String()
								break
							}
						}
					}
				}
				return nil
			}
		}
	}

	// Container not found
	m.State = StateUnknown
	return nil
}
