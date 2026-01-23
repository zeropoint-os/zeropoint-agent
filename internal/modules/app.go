package modules

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

// Module represents an installed module managed by zeropoint-agent.
// State is discovered from filesystem + Terraform outputs + Docker API.
// @Description Installed module with runtime status and GPU information
type Module struct {
	// @Description Module identifier (directory name)
	ID string `json:"id"`
	// @Description Path to Terraform module (e.g., "modules/ollama")
	ModulePath string `json:"module_path"`
	// @Description Runtime state (running, stopped, crashed, unknown)
	State string `json:"state"`
	// @Description Docker container ID for main container (omitted if not running)
	ContainerID string `json:"container_id,omitempty"`
	// @Description Docker container name for main container (omitted if not running)
	ContainerName string `json:"container_name,omitempty"`
	// @Description Container IP address (omitted if not running)
	IPAddress string `json:"ip_address,omitempty"`
	// @Description GPU vendor if container runtime is GPU-capable (e.g., "nvidia", empty if not GPU-capable)
	GPUVendor string `json:"gpu_vendor,omitempty"`
	// @Description Whether container has GPU devices allocated
	UsingGPU bool `json:"using_gpu"`
	// @Description Module containers with their ports (from {container}_ports outputs)
	Containers map[string]Container `json:"containers,omitempty"`
	// @Description Optional tags for categorization
	Tags []string `json:"tags,omitempty"`
}

// Module states
const (
	StateRunning = "running"
	StateStopped = "stopped"
	StateCrashed = "crashed"
	StateUnknown = "unknown"
)

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

				// Get IP address and GPU info if running
				if c.State == "running" {
					inspect, err := docker.ContainerInspect(ctx, c.ID, client.ContainerInspectOptions{})
					if err == nil {
						for _, network := range inspect.Container.NetworkSettings.Networks {
							if network.IPAddress.IsValid() {
								m.IPAddress = network.IPAddress.String()
								break
							}
						}

						// Check if container is using NVIDIA GPU
						if inspect.Container.HostConfig.Runtime == "nvidia" {
							m.GPUVendor = "nvidia"
						}

						// Check if container has GPU devices allocated
						if len(inspect.Container.HostConfig.DeviceRequests) > 0 {
							m.UsingGPU = true
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
