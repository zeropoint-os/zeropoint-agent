package apps

import (
	"context"
	"fmt"

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

// App represents an installed application managed by zeropoint-agent.
// State is discovered from filesystem + Terraform outputs + Docker API.
type App struct {
	ID            string               `json:"id"`                       // App identifier (directory name)
	ModulePath    string               `json:"module_path"`              // Path to Terraform module (e.g., "apps/ollama")
	State         string               `json:"state"`                    // Runtime state: "running" | "stopped" | "crashed" | "unknown"
	ContainerID   string               `json:"container_id,omitempty"`   // Docker container ID (for main container)
	ContainerName string               `json:"container_name,omitempty"` // Docker container name (for main container)
	IPAddress     string               `json:"ip_address,omitempty"`     // Container IP address (for main container)
	Containers    map[string]Container `json:"containers,omitempty"`     // App containers with their ports (from {container}_ports outputs)
}

// App states
const (
	StateRunning = "running"
	StateStopped = "stopped"
	StateCrashed = "crashed"
	StateUnknown = "unknown"
)

// Directory paths
const (
	AppsDir = "./apps"      // Default apps directory
	DataDir = "./data/apps" // Default data directory for bind mounts
)

// GetContainerStatus queries Docker to get the container's runtime state
func (a *App) GetContainerStatus(ctx context.Context, docker *client.Client) error {
	containerName := fmt.Sprintf("%s-main", a.ID)

	containers, err := docker.ContainerList(ctx, client.ContainerListOptions{All: true})
	if err != nil {
		return err
	}

	for _, c := range containers.Items {
		for _, name := range c.Names {
			if name == "/"+containerName || name == containerName {
				a.ContainerID = c.ID[:12]
				a.ContainerName = containerName
				a.State = string(c.State)

				// Get IP address if running
				if c.State == "running" {
					inspect, err := docker.ContainerInspect(ctx, c.ID, client.ContainerInspectOptions{})
					if err == nil {
						for _, network := range inspect.Container.NetworkSettings.Networks {
							if network.IPAddress.IsValid() {
								a.IPAddress = network.IPAddress.String()
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
	a.State = StateUnknown
	return nil
}
