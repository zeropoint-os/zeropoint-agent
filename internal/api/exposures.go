package api

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"log/slog"
	"os"
	"path/filepath"
	"sync"
	"time"

	"zeropoint-agent/internal/xds"

	"github.com/moby/moby/client"
)

const (
	exposuresFileName = "exposures.json"
	minTCPPort        = 10000
	maxTCPPort        = 60000
)

// Exposure represents a service exposure
type Exposure struct {
	ID            string    `json:"id"`
	AppID         string    `json:"app_id"`         // References App.ID
	Protocol      string    `json:"protocol"`       // "http" or "tcp"
	Hostname      string    `json:"hostname"`       // required for http, optional for tcp
	ContainerPort uint32    `json:"container_port"` // port inside container
	HostPort      uint32    `json:"host_port"`      // auto-allocated for tcp, 0 for http
	CreatedAt     time.Time `json:"created_at"`
}

// ExposureStore manages exposures with persistent storage
type ExposureStore struct {
	exposures    map[string]*Exposure // keyed by ID
	mutex        sync.RWMutex
	xdsServer    *xds.Server
	dockerClient *client.Client
	storagePath  string
	logger       *slog.Logger
}

// NewExposureStore creates a new exposure store
func NewExposureStore(dockerClient *client.Client, xdsServer *xds.Server, logger *slog.Logger) (*ExposureStore, error) {
	storageRoot := os.Getenv("APP_STORAGE_ROOT")
	if storageRoot == "" {
		storageRoot = filepath.Join(os.Getenv("HOME"), ".zeropoint-agent")
	}

	// Ensure storage directory exists
	if err := os.MkdirAll(storageRoot, 0755); err != nil {
		return nil, fmt.Errorf("failed to create storage directory: %w", err)
	}

	storagePath := filepath.Join(storageRoot, exposuresFileName)

	store := &ExposureStore{
		exposures:    make(map[string]*Exposure),
		xdsServer:    xdsServer,
		dockerClient: dockerClient,
		storagePath:  storagePath,
		logger:       logger,
	}

	// Load existing exposures from disk
	if err := store.load(); err != nil {
		logger.Warn("failed to load exposures, starting fresh", "error", err)
	}

	// Reconcile network connections
	if err := store.reconcileNetworks(context.Background()); err != nil {
		logger.Warn("failed to reconcile networks", "error", err)
	}

	// Rebuild xDS snapshot from loaded exposures
	if err := store.updateSnapshot(context.Background()); err != nil {
		return nil, fmt.Errorf("failed to build initial snapshot: %w", err)
	}

	return store, nil
}

// CreateExposure creates or returns existing exposure (idempotent)
func (s *ExposureStore) CreateExposure(ctx context.Context, appID, protocol, hostname string, containerPort uint32) (*Exposure, bool, error) {
	s.mutex.Lock()
	defer s.mutex.Unlock()

	// Validate protocol
	if protocol != "http" && protocol != "tcp" {
		return nil, false, fmt.Errorf("protocol must be 'http' or 'tcp'")
	}

	// Validate hostname for http
	if protocol == "http" && hostname == "" {
		return nil, false, fmt.Errorf("hostname required for http exposures")
	}

	// Check if exposure already exists
	if existing := s.findExposure(appID, protocol, hostname, containerPort); existing != nil {
		return existing, false, nil
	}

	// Verify container exists
	if err := s.verifyContainer(ctx, appID); err != nil {
		return nil, false, err
	}

	// Create new exposure
	exposure := &Exposure{
		ID:            generateID(),
		AppID:         appID,
		Protocol:      protocol,
		Hostname:      hostname,
		ContainerPort: containerPort,
		CreatedAt:     time.Now(),
	}

	// Allocate host port for TCP
	if protocol == "tcp" {
		hostPort, err := s.allocatePort()
		if err != nil {
			return nil, false, err
		}
		exposure.HostPort = hostPort
	}

	// Ensure container is on zeropoint-network
	if err := s.ensureNetwork(ctx, appID); err != nil {
		return nil, false, err
	}

	// Store exposure
	s.exposures[exposure.ID] = exposure

	// Save to disk
	if err := s.save(); err != nil {
		delete(s.exposures, exposure.ID)
		return nil, false, fmt.Errorf("failed to save exposures: %w", err)
	}

	// Update xDS snapshot
	if err := s.updateSnapshot(ctx); err != nil {
		s.logger.Error("failed to update xDS snapshot", "error", err)
		// Don't fail the request, just log
	}

	return exposure, true, nil
}

// GetExposure retrieves an exposure by ID
func (s *ExposureStore) GetExposure(id string) (*Exposure, error) {
	s.mutex.RLock()
	defer s.mutex.RUnlock()

	exposure, ok := s.exposures[id]
	if !ok {
		return nil, fmt.Errorf("exposure not found")
	}
	return exposure, nil
}

// ListExposures returns all exposures
func (s *ExposureStore) ListExposures() []*Exposure {
	s.mutex.RLock()
	defer s.mutex.RUnlock()

	exposures := make([]*Exposure, 0, len(s.exposures))
	for _, exp := range s.exposures {
		exposures = append(exposures, exp)
	}
	return exposures
}

// DeleteExposure removes an exposure
func (s *ExposureStore) DeleteExposure(ctx context.Context, id string) error {
	s.mutex.Lock()
	defer s.mutex.Unlock()

	_, ok := s.exposures[id]
	if !ok {
		return fmt.Errorf("exposure not found")
	}

	delete(s.exposures, id)

	// Save to disk
	if err := s.save(); err != nil {
		return fmt.Errorf("failed to save exposures: %w", err)
	}

	// Update xDS snapshot
	if err := s.updateSnapshot(ctx); err != nil {
		s.logger.Error("failed to update xDS snapshot", "error", err)
	}

	return nil
}

// GetExposureByAppID returns an exposure by app ID
func (s *ExposureStore) GetExposureByAppID(appID string) *Exposure {
	s.mutex.RLock()
	defer s.mutex.RUnlock()

	for _, exp := range s.exposures {
		if exp.AppID == appID {
			return exp
		}
	}
	return nil
}

// DeleteExposureByAppID removes an exposure by app ID
func (s *ExposureStore) DeleteExposureByAppID(ctx context.Context, appID string) error {
	s.mutex.Lock()
	defer s.mutex.Unlock()

	// Find exposure with matching app_id
	var exposureID string
	for id, exp := range s.exposures {
		if exp.AppID == appID {
			exposureID = id
			break
		}
	}

	if exposureID == "" {
		return fmt.Errorf("exposure not found for app_id: %s", appID)
	}

	delete(s.exposures, exposureID)

	// Save to disk
	if err := s.save(); err != nil {
		return fmt.Errorf("failed to save exposures: %w", err)
	}

	// Update xDS snapshot
	if err := s.updateSnapshot(ctx); err != nil {
		s.logger.Error("failed to update xDS snapshot", "error", err)
	}

	return nil
}

// findExposure checks if an exposure already exists
func (s *ExposureStore) findExposure(appID, protocol, hostname string, containerPort uint32) *Exposure {
	for _, exp := range s.exposures {
		if exp.AppID == appID &&
			exp.Protocol == protocol &&
			exp.Hostname == hostname &&
			exp.ContainerPort == containerPort {
			return exp
		}
	}
	return nil
}

// allocatePort finds the next available TCP port
func (s *ExposureStore) allocatePort() (uint32, error) {
	usedPorts := make(map[uint32]bool)
	for _, exp := range s.exposures {
		if exp.Protocol == "tcp" {
			usedPorts[exp.HostPort] = true
		}
	}

	for port := uint32(minTCPPort); port < maxTCPPort; port++ {
		if !usedPorts[port] {
			return port, nil
		}
	}

	return 0, fmt.Errorf("no available ports in range %d-%d", minTCPPort, maxTCPPort)
}

// verifyContainer checks if a container exists for the given app ID
func (s *ExposureStore) verifyContainer(ctx context.Context, appID string) error {
	// Container name is app ID + "-main"
	containerName := appID + "-main"
	_, err := s.dockerClient.ContainerInspect(ctx, containerName, client.ContainerInspectOptions{})
	if err != nil {
		return fmt.Errorf("container not found for app %s: %w", appID, err)
	}
	return nil
}

// getContainerStatus checks if a container exists and is running
func (s *ExposureStore) getContainerStatus(appID string) string {
	containerName := appID + "-main"
	info, err := s.dockerClient.ContainerInspect(context.Background(), containerName, client.ContainerInspectOptions{})
	if err != nil {
		return "unavailable"
	}
	if string(info.Container.State.Status) == "running" {
		return "available"
	}
	return "unavailable"
}

// ensureNetwork connects container to zeropoint-network
func (s *ExposureStore) ensureNetwork(ctx context.Context, appID string) error {
	networkName := "zeropoint-network"
	// Container name is app ID + "-main"
	containerName := appID + "-main"

	// Create network if it doesn't exist
	networkList, err := s.dockerClient.NetworkList(ctx, client.NetworkListOptions{})
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
		resp, err := s.dockerClient.NetworkCreate(ctx, networkName, client.NetworkCreateOptions{
			Driver: "bridge",
		})
		if err != nil {
			return fmt.Errorf("failed to create network: %w", err)
		}
		networkID = resp.ID
		s.logger.Info("created zeropoint-network", "id", networkID)
	}

	// Connect container to network (idempotent)
	_, err = s.dockerClient.NetworkConnect(ctx, networkID, client.NetworkConnectOptions{
		Container: containerName,
	})
	if err != nil && !isAlreadyConnectedError(err) {
		return fmt.Errorf("failed to connect container to network: %w", err)
	}

	return nil
}

// reconcileNetworks ensures all containers are connected to zeropoint-network
func (s *ExposureStore) reconcileNetworks(ctx context.Context) error {
	for _, exp := range s.exposures {
		if err := s.ensureNetwork(ctx, exp.AppID); err != nil {
			s.logger.Warn("failed to reconnect container to network", "app_id", exp.AppID, "error", err)
		}
	}
	return nil
}

// updateSnapshot rebuilds and pushes xDS snapshot
func (s *ExposureStore) updateSnapshot(ctx context.Context) error {
	exposures := make([]*xds.Exposure, 0, len(s.exposures))
	for _, exp := range s.exposures {
		// xDS needs container name, which is appID + "-main"
		xdsExp := &xds.Exposure{
			ID:            exp.ID,
			AppName:       exp.AppID + "-main", // Convert app ID to container name
			Protocol:      exp.Protocol,
			Hostname:      exp.Hostname,
			ContainerPort: exp.ContainerPort,
			HostPort:      exp.HostPort,
		}
		exposures = append(exposures, xdsExp)
	}

	snapshot, err := xds.BuildSnapshotFromExposures(s.xdsServer.NextVersion(), exposures)
	if err != nil {
		return err
	}

	return s.xdsServer.UpdateSnapshot(ctx, snapshot)
}

// save writes exposures to disk
func (s *ExposureStore) save() error {
	data, err := json.MarshalIndent(s.exposures, "", "  ")
	if err != nil {
		return err
	}

	// Atomic write: write to temp file, then rename
	tmpPath := s.storagePath + ".tmp"
	if err := os.WriteFile(tmpPath, data, 0644); err != nil {
		return err
	}

	return os.Rename(tmpPath, s.storagePath)
}

// load reads exposures from disk
func (s *ExposureStore) load() error {
	data, err := os.ReadFile(s.storagePath)
	if err != nil {
		if os.IsNotExist(err) {
			return nil // No file yet, start fresh
		}
		return err
	}

	return json.Unmarshal(data, &s.exposures)
}

// generateID creates a random exposure ID
func generateID() string {
	b := make([]byte, 8)
	rand.Read(b)
	return "exp_" + hex.EncodeToString(b)
}

// isAlreadyConnectedError checks if error is "already connected"
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
	return len(s) >= len(substr) && (s == substr || len(s) > len(substr) && (s[:len(substr)] == substr || s[len(s)-len(substr):] == substr || findInString(s, substr)))
}

func findInString(s, substr string) bool {
	for i := 0; i <= len(s)-len(substr); i++ {
		if s[i:i+len(substr)] == substr {
			return true
		}
	}
	return false
}
