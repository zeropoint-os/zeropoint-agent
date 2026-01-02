package network

import (
	"context"
	"fmt"
	"log/slog"

	"github.com/moby/moby/client"
)

// Manager handles Docker network operations
type Manager struct {
	dockerClient *client.Client
	logger       *slog.Logger
}

// NewManager creates a new network manager
func NewManager(dockerClient *client.Client, logger *slog.Logger) *Manager {
	return &Manager{
		dockerClient: dockerClient,
		logger:       logger,
	}
}

// EnsureNetworkExists creates a network if it doesn't exist and returns its ID
func (m *Manager) EnsureNetworkExists(ctx context.Context, networkName string) (string, error) {
	// Check if network already exists
	networkList, err := m.dockerClient.NetworkList(ctx, client.NetworkListOptions{})
	if err != nil {
		return "", fmt.Errorf("failed to list networks: %w", err)
	}

	for _, network := range networkList.Items {
		if network.Name == networkName {
			return network.ID, nil
		}
	}

	// Create network if it doesn't exist
	resp, err := m.dockerClient.NetworkCreate(ctx, networkName, client.NetworkCreateOptions{
		Driver: "bridge",
	})
	if err != nil {
		return "", fmt.Errorf("failed to create network %s: %w", networkName, err)
	}

	m.logger.Info("Created network", "network", networkName, "id", resp.ID)
	return resp.ID, nil
}

// ConnectContainer connects a container to a network (idempotent)
func (m *Manager) ConnectContainer(ctx context.Context, networkID, containerName string) error {
	_, err := m.dockerClient.NetworkConnect(ctx, networkID, client.NetworkConnectOptions{
		Container: containerName,
	})
	if err != nil && !isAlreadyConnectedError(err) {
		return fmt.Errorf("failed to connect container %s to network: %w", containerName, err)
	}
	return nil
}

// ConnectContainerToNetwork is a convenience method that ensures network exists and connects container
func (m *Manager) ConnectContainerToNetwork(ctx context.Context, containerName, networkName string) error {
	networkID, err := m.EnsureNetworkExists(ctx, networkName)
	if err != nil {
		return err
	}

	return m.ConnectContainer(ctx, networkID, containerName)
}

// isAlreadyConnectedError checks if error indicates container is already connected
func isAlreadyConnectedError(err error) bool {
	if err == nil {
		return false
	}
	errStr := err.Error()
	return containsString(errStr, "already connected") ||
		containsString(errStr, "already exists in network") ||
		containsString(errStr, "already attached")
}

func containsString(s, substr string) bool {
	for i := 0; i <= len(s)-len(substr); i++ {
		if s[i:i+len(substr)] == substr {
			return true
		}
	}
	return false
}