package envoy

import (
	"context"
	"fmt"
	"io"
	"log/slog"
	"os"
	"strconv"

	"github.com/moby/moby/api/types/container"
	"github.com/moby/moby/api/types/network"
	"github.com/moby/moby/client"
)

const (
	containerName = "zeropoint-envoy"
	defaultImage  = "envoyproxy/envoy:v1.31-latest"
)

// Manager handles the lifecycle of the Envoy proxy container
type Manager struct {
	docker    *client.Client
	logger    *slog.Logger
	httpPort  int
	httpsPort int
	xdsPort   int
	image     string
}

// NewManager creates a new Envoy manager
func NewManager(docker *client.Client, logger *slog.Logger) *Manager {
	return &Manager{
		docker:    docker,
		logger:    logger,
		httpPort:  getEnvInt("ZEROPOINT_ENVOY_HTTP_PORT", 80),
		httpsPort: getEnvInt("ZEROPOINT_ENVOY_HTTPS_PORT", 443),
		xdsPort:   getEnvInt("ZEROPOINT_XDS_PORT", 18000),
		image:     getEnvString("ZEROPOINT_ENVOY_IMAGE", defaultImage),
	}
}

// EnsureRunning ensures the Envoy container is running
func (m *Manager) EnsureRunning(ctx context.Context) error {
	m.logger.Info("ensuring envoy container is running")

	// Check if container exists
	result, err := m.docker.ContainerList(ctx, client.ContainerListOptions{
		All: true,
	})
	if err != nil {
		return fmt.Errorf("failed to list containers: %w", err)
	}

	// Find our container by name
	var containerID string
	var containerState string
	for _, c := range result.Items {
		for _, name := range c.Names {
			if name == "/"+containerName || name == containerName {
				containerID = c.ID
				containerState = string(c.State)
				break
			}
		}
		if containerID != "" {
			break
		}
	}

	if containerID != "" {
		// Container exists
		if containerState == "running" {
			m.logger.Info("envoy container already running", "id", containerID[:12])
			return nil
		}

		// Container exists but not running, start it
		m.logger.Info("starting existing envoy container", "id", containerID[:12])
		_, err := m.docker.ContainerStart(ctx, containerID, client.ContainerStartOptions{})
		if err != nil {
			return fmt.Errorf("failed to start envoy container: %w", err)
		}
		m.logger.Info("envoy container started", "id", containerID[:12])
		return nil
	}

	// Container doesn't exist, create it
	return m.createAndStart(ctx)
}

// Stop stops the Envoy container (does not remove it)
func (m *Manager) Stop(ctx context.Context) error {
	m.logger.Info("stopping envoy container")

	result, err := m.docker.ContainerList(ctx, client.ContainerListOptions{
		All: true,
	})
	if err != nil {
		return fmt.Errorf("failed to list containers: %w", err)
	}

	// Find our container
	var containerID string
	var containerState string
	for _, c := range result.Items {
		for _, name := range c.Names {
			if name == "/"+containerName || name == containerName {
				containerID = c.ID
				containerState = string(c.State)
				break
			}
		}
		if containerID != "" {
			break
		}
	}

	if containerID == "" {
		m.logger.Info("envoy container not found, nothing to stop")
		return nil
	}

	if containerState != "running" {
		m.logger.Info("envoy container already stopped", "id", containerID[:12])
		return nil
	}

	_, err = m.docker.ContainerStop(ctx, containerID, client.ContainerStopOptions{})
	if err != nil {
		return fmt.Errorf("failed to stop envoy container: %w", err)
	}

	m.logger.Info("envoy container stopped", "id", containerID[:12])
	return nil
}

func (m *Manager) createAndStart(ctx context.Context) error {
	m.logger.Info("creating envoy container", "image", m.image)

	// Ensure image exists
	if err := m.ensureImage(ctx); err != nil {
		return err
	}

	// Detect the gateway IP for the zeropoint-network
	xdsHost, err := m.getNetworkGateway(ctx, "zeropoint-network")
	if err != nil {
		return fmt.Errorf("failed to get network gateway: %w", err)
	}

	m.logger.Info("detected xDS host", "host", xdsHost)

	// Generate bootstrap config with detected gateway
	bootstrapPath, err := GetBootstrapPath(xdsHost, m.xdsPort)
	if err != nil {
		return fmt.Errorf("failed to get bootstrap path: %w", err)
	}

	m.logger.Info("using bootstrap config", "path", bootstrapPath)

	// Create container
	resp, err := m.docker.ContainerCreate(ctx, client.ContainerCreateOptions{
		Name: containerName,
		Config: &container.Config{
			Image: m.image,
			Cmd:   []string{"-c", "/etc/envoy/bootstrap.yaml"},
			ExposedPorts: network.PortSet{
				network.MustParsePort(fmt.Sprintf("%d/tcp", m.httpPort)):  {},
				network.MustParsePort(fmt.Sprintf("%d/tcp", m.httpsPort)): {},
				network.MustParsePort("9901/tcp"):                         {}, // Admin interface
			},
		},
		HostConfig: &container.HostConfig{
			PortBindings: network.PortMap{
				network.MustParsePort(fmt.Sprintf("%d/tcp", m.httpPort)): []network.PortBinding{
					{HostPort: fmt.Sprintf("%d", m.httpPort)},
				},
				network.MustParsePort(fmt.Sprintf("%d/tcp", m.httpsPort)): []network.PortBinding{
					{HostPort: fmt.Sprintf("%d", m.httpsPort)},
				},
				network.MustParsePort("9901/tcp"): []network.PortBinding{
					{HostPort: "9901"},
				},
			},
			Binds: []string{
				fmt.Sprintf("%s:/etc/envoy/bootstrap.yaml:ro", bootstrapPath),
			},
			RestartPolicy: container.RestartPolicy{
				Name: "unless-stopped",
			},
		},
	})
	if err != nil {
		return fmt.Errorf("failed to create envoy container: %w", err)
	}

	m.logger.Info("envoy container created", "id", resp.ID[:12])

	// Connect to zeropoint-network
	if err := m.ensureZeropointNetwork(ctx, resp.ID); err != nil {
		// Don't fail if network connection fails, log warning
		m.logger.Warn("failed to connect envoy to zeropoint-network", "error", err)
	}

	// Start container
	_, err = m.docker.ContainerStart(ctx, resp.ID, client.ContainerStartOptions{})
	if err != nil {
		return fmt.Errorf("failed to start envoy container: %w", err)
	}

	m.logger.Info("envoy container started", "id", resp.ID[:12], "http_port", m.httpPort, "https_port", m.httpsPort, "admin_port", 9901)
	return nil
}

func (m *Manager) ensureImage(ctx context.Context) error {
	// Check if image exists
	result, err := m.docker.ImageList(ctx, client.ImageListOptions{})
	if err != nil {
		return fmt.Errorf("failed to list images: %w", err)
	}

	// Check if our image exists
	for _, img := range result.Items {
		for _, tag := range img.RepoTags {
			if tag == m.image {
				m.logger.Info("envoy image already exists", "image", m.image)
				return nil
			}
		}
	}

	// Pull image
	m.logger.Info("pulling envoy image", "image", m.image)
	reader, err := m.docker.ImagePull(ctx, m.image, client.ImagePullOptions{})
	if err != nil {
		return fmt.Errorf("failed to pull envoy image: %w", err)
	}
	defer reader.Close()

	// Stream output to discard (could log if needed)
	if _, err := io.Copy(io.Discard, reader); err != nil {
		return fmt.Errorf("failed to read image pull output: %w", err)
	}

	m.logger.Info("envoy image pulled successfully", "image", m.image)
	return nil
}

func getEnvInt(key string, defaultValue int) int {
	if val := os.Getenv(key); val != "" {
		if intVal, err := strconv.Atoi(val); err == nil {
			return intVal
		}
	}
	return defaultValue
}

func getEnvString(key, defaultValue string) string {
	if val := os.Getenv(key); val != "" {
		return val
	}
	return defaultValue
}

// ensureZeropointNetwork connects Envoy to the zeropoint-network
func (m *Manager) ensureZeropointNetwork(ctx context.Context, containerID string) error {
	networkName := "zeropoint-network"

	// Create network if it doesn't exist
	networkList, err := m.docker.NetworkList(ctx, client.NetworkListOptions{})
	if err != nil {
		return fmt.Errorf("failed to list networks: %w", err)
	}

	networkExists := false
	var networkID string
	for _, network := range networkList.Items {
		if network.Name == networkName {
			networkExists = true
			networkID = network.ID
			break
		}
	}

	if !networkExists {
		resp, err := m.docker.NetworkCreate(ctx, networkName, client.NetworkCreateOptions{
			Driver: "bridge",
		})
		if err != nil {
			return fmt.Errorf("failed to create network: %w", err)
		}
		networkID = resp.ID
		m.logger.Info("created zeropoint-network", "id", networkID)
	}

	// Connect container to network
	_, err = m.docker.NetworkConnect(ctx, networkID, client.NetworkConnectOptions{
		Container: containerID,
	})
	if err != nil && !isAlreadyConnectedError(err) {
		return fmt.Errorf("failed to connect to network: %w", err)
	}

	m.logger.Info("connected envoy to zeropoint-network")
	return nil
}

func isAlreadyConnectedError(err error) bool {
	if err == nil {
		return false
	}
	errStr := err.Error()
	return len(errStr) > 0 && (contains(errStr, "already connected") ||
		contains(errStr, "already exists in network") ||
		contains(errStr, "already attached"))
}

func contains(s, substr string) bool {
	for i := 0; i <= len(s)-len(substr); i++ {
		if s[i:i+len(substr)] == substr {
			return true
		}
	}
	return false
}

// getNetworkGateway inspects a Docker network and returns its gateway IP
func (m *Manager) getNetworkGateway(ctx context.Context, networkName string) (string, error) {
	// Create network if it doesn't exist
	networkList, err := m.docker.NetworkList(ctx, client.NetworkListOptions{})
	if err != nil {
		return "", fmt.Errorf("failed to list networks: %w", err)
	}

	var networkID string
	for _, network := range networkList.Items {
		if network.Name == networkName {
			networkID = network.ID
			break
		}
	}

	if networkID == "" {
		// Network doesn't exist, create it
		resp, err := m.docker.NetworkCreate(ctx, networkName, client.NetworkCreateOptions{
			Driver: "bridge",
		})
		if err != nil {
			return "", fmt.Errorf("failed to create network: %w", err)
		}
		networkID = resp.ID
		m.logger.Info("created network", "name", networkName, "id", networkID)
	}

	// Inspect the network to get its gateway
	networkInfo, err := m.docker.NetworkInspect(ctx, networkID, client.NetworkInspectOptions{})
	if err != nil {
		return "", fmt.Errorf("failed to inspect network: %w", err)
	}

	// Get the gateway from IPAM config
	if len(networkInfo.Network.IPAM.Config) == 0 {
		return "", fmt.Errorf("network has no IPAM configuration")
	}

	gateway := networkInfo.Network.IPAM.Config[0].Gateway
	if !gateway.IsValid() {
		return "", fmt.Errorf("network has no gateway configured")
	}

	return gateway.String(), nil
}
