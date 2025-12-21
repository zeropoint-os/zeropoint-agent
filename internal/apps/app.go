package apps

import (
	"context"
	"fmt"

	"github.com/moby/moby/client"
)

// App represents an installed application managed by zeropoint-agent.
// State is discovered from filesystem + Terraform outputs + Docker API.
type App struct {
	ID            string `json:"id"`                       // App identifier (directory name)
	ModulePath    string `json:"module_path"`              // Path to Terraform module (e.g., "apps/ollama")
	State         string `json:"state"`                    // Runtime state: "running" | "stopped" | "crashed" | "unknown"
	ContainerID   string `json:"container_id,omitempty"`   // Docker container ID
	ContainerName string `json:"container_name,omitempty"` // Docker container name
	IPAddress     string `json:"ip_address,omitempty"`     // Container IP address
}

// App states
const (
	StateRunning = "running"
	StateStopped = "stopped"
	StateCrashed = "crashed"
	StateUnknown = "unknown"
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
