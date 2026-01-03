package api

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"os"
	"path/filepath"
	"sync"
	"time"

	"zeropoint-agent/internal/network"

	"github.com/moby/moby/client"
)

const (
	linksFileName = "links.json"
)

// Link represents a group of linked apps with their references
type Link struct {
	ID              string                            `json:"id"`
	Apps            map[string]map[string]interface{} `json:"apps"`            // App configurations with references
	References      map[string]map[string]string      `json:"references"`      // Resolved references for each app
	SharedNetworks  []string                          `json:"shared_networks"` // Networks created for this link
	DependencyOrder []string                          `json:"dependency_order"`
	CreatedAt       time.Time                         `json:"created_at"`
	UpdatedAt       time.Time                         `json:"updated_at"`
}

// LinkStore manages links with persistent storage
type LinkStore struct {
	links          map[string]*Link // keyed by ID
	mutex          sync.RWMutex
	networkManager *network.Manager
	storagePath    string
	logger         *slog.Logger
}

// NewLinkStore creates a new link store
func NewLinkStore(dockerClient *client.Client, logger *slog.Logger) (*LinkStore, error) {
	storageRoot := os.Getenv("MODULE_STORAGE_ROOT")
	if storageRoot == "" {
		storageRoot = filepath.Join(os.Getenv("HOME"), ".zeropoint-agent")
	}

	// Ensure storage directory exists
	if err := os.MkdirAll(storageRoot, 0755); err != nil {
		return nil, fmt.Errorf("failed to create storage directory: %w", err)
	}

	storagePath := filepath.Join(storageRoot, linksFileName)

	store := &LinkStore{
		links:          make(map[string]*Link),
		networkManager: network.NewManager(dockerClient, logger),
		storagePath:    storagePath,
		logger:         logger,
	}

	// Load existing links from disk
	if err := store.load(); err != nil {
		logger.Warn("failed to load links, starting fresh", "error", err)
	}

	return store, nil
}

// CreateOrUpdateLink creates or updates a link
func (s *LinkStore) CreateOrUpdateLink(ctx context.Context, linkID string, apps map[string]map[string]interface{}, references map[string]map[string]string, sharedNetworks []string, dependencyOrder []string) (*Link, error) {
	s.mutex.Lock()
	defer s.mutex.Unlock()

	now := time.Now()

	// Check if link exists
	existingLink, exists := s.links[linkID]

	var link *Link
	if exists {
		// Update existing link
		link = existingLink
		link.Apps = apps
		link.References = references
		link.SharedNetworks = sharedNetworks
		link.DependencyOrder = dependencyOrder
		link.UpdatedAt = now
	} else {
		// Create new link
		link = &Link{
			ID:              linkID,
			Apps:            apps,
			References:      references,
			SharedNetworks:  sharedNetworks,
			DependencyOrder: dependencyOrder,
			CreatedAt:       now,
			UpdatedAt:       now,
		}
	}

	s.links[linkID] = link

	// Save to disk
	if err := s.save(); err != nil {
		delete(s.links, linkID)
		return nil, fmt.Errorf("failed to save links: %w", err)
	}

	return link, nil
}

// GetLink retrieves a link by ID
func (s *LinkStore) GetLink(id string) (*Link, error) {
	s.mutex.RLock()
	defer s.mutex.RUnlock()

	link, ok := s.links[id]
	if !ok {
		return nil, fmt.Errorf("link not found")
	}
	return link, nil
}

// ListLinks returns all links
func (s *LinkStore) ListLinks() []*Link {
	s.mutex.RLock()
	defer s.mutex.RUnlock()

	links := make([]*Link, 0, len(s.links))
	for _, link := range s.links {
		links = append(links, link)
	}
	return links
}

// DeleteLink removes a link and cleans up its networks
func (s *LinkStore) DeleteLink(ctx context.Context, id string) error {
	s.mutex.Lock()
	defer s.mutex.Unlock()

	if _, ok := s.links[id]; !ok {
		return fmt.Errorf("link not found")
	}

	// TODO: Clean up shared networks for this link
	// For now, we'll leave networks in place since other links might use them

	delete(s.links, id)

	// Save to disk
	if err := s.save(); err != nil {
		return fmt.Errorf("failed to save links: %w", err)
	}

	s.logger.Info("Deleted link", "link_id", id)
	return nil
}

// GetNetworkManager returns the network manager for use by link handlers
func (s *LinkStore) GetNetworkManager() *network.Manager {
	return s.networkManager
}

// save writes links to disk
func (s *LinkStore) save() error {
	data, err := json.MarshalIndent(s.links, "", "  ")
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

// load reads links from disk
func (s *LinkStore) load() error {
	data, err := os.ReadFile(s.storagePath)
	if err != nil {
		if os.IsNotExist(err) {
			return nil // No file yet, start fresh
		}
		return err
	}

	return json.Unmarshal(data, &s.links)
}
