package api

import (
	"encoding/json"
	"fmt"
	"log/slog"
	"os"
	"path/filepath"
	"sync"
	"time"

	internalPaths "zeropoint-agent/internal"
)

const bundlesFileName = "bundles.json"

// BundleComponentStatus represents the status of a bundle component
type BundleComponentStatus struct {
	ID     string `json:"id"`
	Status string `json:"status"` // "completed", "failed", "pending", "running"
	Error  string `json:"error,omitempty"`
}

// BundleComponents tracks which components were created as part of the bundle
type BundleComponents struct {
	Modules   []BundleComponentStatus `json:"modules,omitempty"`
	Links     []BundleComponentStatus `json:"links,omitempty"`
	Exposures []BundleComponentStatus `json:"exposures,omitempty"`
}

// BundleRecord represents an installed bundle with its status and components
type BundleRecord struct {
	ID          string           `json:"id"`
	Name        string           `json:"name"`
	Status      string           `json:"status"` // "running", "completed", "failed", "partially_completed"
	InstalledAt time.Time        `json:"installed_at"`
	CompletedAt *time.Time       `json:"completed_at,omitempty"`
	Components  BundleComponents `json:"components"`
	JobID       string           `json:"job_id,omitempty"` // Reference to the bundle_install job
}

// BundleStore manages installed bundles with persistent storage
type BundleStore struct {
	bundles     map[string]*BundleRecord // keyed by bundle ID
	mutex       sync.RWMutex
	storagePath string
	logger      *slog.Logger
}

// NewBundleStore creates a new bundle store
func NewBundleStore(logger *slog.Logger) (*BundleStore, error) {
	storageRoot := internalPaths.GetStorageRoot()

	// Ensure storage directory exists
	if err := os.MkdirAll(storageRoot, 0755); err != nil {
		return nil, fmt.Errorf("failed to create storage directory: %w", err)
	}

	storagePath := filepath.Join(storageRoot, bundlesFileName)

	store := &BundleStore{
		bundles:     make(map[string]*BundleRecord),
		storagePath: storagePath,
		logger:      logger,
	}

	// Load existing bundles from disk
	if err := store.load(); err != nil {
		logger.Warn("failed to load bundles, starting fresh", "error", err)
	}

	return store, nil
}

// CreateBundle creates a new bundle record (called at start of installation)
func (s *BundleStore) CreateBundle(bundleID, bundleName, jobID string) interface{} {
	s.mutex.Lock()
	defer s.mutex.Unlock()

	bundle := &BundleRecord{
		ID:          bundleID,
		Name:        bundleName,
		Status:      "running",
		InstalledAt: time.Now(),
		Components: BundleComponents{
			Modules:   make([]BundleComponentStatus, 0),
			Links:     make([]BundleComponentStatus, 0),
			Exposures: make([]BundleComponentStatus, 0),
		},
		JobID: jobID,
	}

	s.bundles[bundleID] = bundle
	_ = s.save() // Best effort save

	return bundle
}

// AddModuleComponent adds a module to the bundle's components
func (s *BundleStore) AddModuleComponent(bundleID, moduleID string, status, errMsg string) error {
	s.mutex.Lock()
	defer s.mutex.Unlock()

	bundle, ok := s.bundles[bundleID]
	if !ok {
		return fmt.Errorf("bundle not found: %s", bundleID)
	}

	component := BundleComponentStatus{
		ID:     moduleID,
		Status: status,
	}
	if errMsg != "" {
		component.Error = errMsg
	}

	bundle.Components.Modules = append(bundle.Components.Modules, component)
	return s.save()
}

// AddLinkComponent adds a link to the bundle's components
func (s *BundleStore) AddLinkComponent(bundleID, linkID string, status, errMsg string) error {
	s.mutex.Lock()
	defer s.mutex.Unlock()

	bundle, ok := s.bundles[bundleID]
	if !ok {
		return fmt.Errorf("bundle not found: %s", bundleID)
	}

	component := BundleComponentStatus{
		ID:     linkID,
		Status: status,
	}
	if errMsg != "" {
		component.Error = errMsg
	}

	bundle.Components.Links = append(bundle.Components.Links, component)
	return s.save()
}

// AddExposureComponent adds an exposure to the bundle's components
func (s *BundleStore) AddExposureComponent(bundleID, exposureID string, status, errMsg string) error {
	s.mutex.Lock()
	defer s.mutex.Unlock()

	bundle, ok := s.bundles[bundleID]
	if !ok {
		return fmt.Errorf("bundle not found: %s", bundleID)
	}

	component := BundleComponentStatus{
		ID:     exposureID,
		Status: status,
	}
	if errMsg != "" {
		component.Error = errMsg
	}

	bundle.Components.Exposures = append(bundle.Components.Exposures, component)
	return s.save()
}

// UpdateModuleComponentStatus updates a module component's status
func (s *BundleStore) UpdateModuleComponentStatus(bundleID, moduleID, status, errMsg string) error {
	s.mutex.Lock()
	defer s.mutex.Unlock()

	bundle, ok := s.bundles[bundleID]
	if !ok {
		return fmt.Errorf("bundle not found: %s", bundleID)
	}

	for i, comp := range bundle.Components.Modules {
		if comp.ID == moduleID {
			bundle.Components.Modules[i].Status = status
			bundle.Components.Modules[i].Error = errMsg
			return s.save()
		}
	}

	return fmt.Errorf("module component not found: %s", moduleID)
}

// UpdateLinkComponentStatus updates a link component's status
func (s *BundleStore) UpdateLinkComponentStatus(bundleID, linkID, status, errMsg string) error {
	s.mutex.Lock()
	defer s.mutex.Unlock()

	bundle, ok := s.bundles[bundleID]
	if !ok {
		return fmt.Errorf("bundle not found: %s", bundleID)
	}

	for i, comp := range bundle.Components.Links {
		if comp.ID == linkID {
			bundle.Components.Links[i].Status = status
			bundle.Components.Links[i].Error = errMsg
			return s.save()
		}
	}

	return fmt.Errorf("link component not found: %s", linkID)
}

// UpdateExposureComponentStatus updates an exposure component's status
func (s *BundleStore) UpdateExposureComponentStatus(bundleID, exposureID, status, errMsg string) error {
	s.mutex.Lock()
	defer s.mutex.Unlock()

	bundle, ok := s.bundles[bundleID]
	if !ok {
		return fmt.Errorf("bundle not found: %s", bundleID)
	}

	for i, comp := range bundle.Components.Exposures {
		if comp.ID == exposureID {
			bundle.Components.Exposures[i].Status = status
			bundle.Components.Exposures[i].Error = errMsg
			return s.save()
		}
	}

	return fmt.Errorf("exposure component not found: %s", exposureID)
}

// CompleteBundleInstallation marks the bundle as completed or failed
func (s *BundleStore) CompleteBundleInstallation(bundleID string, success bool) error {
	s.mutex.Lock()
	defer s.mutex.Unlock()

	bundle, ok := s.bundles[bundleID]
	if !ok {
		return fmt.Errorf("bundle not found: %s", bundleID)
	}

	now := time.Now()
	bundle.CompletedAt = &now

	if success {
		bundle.Status = "completed"
	} else {
		// Check if any components failed
		hasFailed := false
		for _, comp := range bundle.Components.Modules {
			if comp.Status == "failed" {
				hasFailed = true
				break
			}
		}
		if !hasFailed {
			for _, comp := range bundle.Components.Links {
				if comp.Status == "failed" {
					hasFailed = true
					break
				}
			}
		}
		if !hasFailed {
			for _, comp := range bundle.Components.Exposures {
				if comp.Status == "failed" {
					hasFailed = true
					break
				}
			}
		}

		if hasFailed {
			bundle.Status = "partially_completed"
		} else {
			bundle.Status = "completed"
		}
	}

	return s.save()
}

// GetBundle retrieves a bundle by ID
func (s *BundleStore) GetBundle(bundleID string) (interface{}, error) {
	s.mutex.RLock()
	defer s.mutex.RUnlock()

	bundle, ok := s.bundles[bundleID]
	if !ok {
		return nil, fmt.Errorf("bundle not found: %s", bundleID)
	}

	return bundle, nil
}

// ListBundles returns all installed bundles
func (s *BundleStore) ListBundles() []*BundleRecord {
	s.mutex.RLock()
	defer s.mutex.RUnlock()

	bundles := make([]*BundleRecord, 0, len(s.bundles))
	for _, bundle := range s.bundles {
		bundles = append(bundles, bundle)
	}

	return bundles
}

// DeleteBundle removes a bundle record
func (s *BundleStore) DeleteBundle(bundleID string) error {
	s.mutex.Lock()
	defer s.mutex.Unlock()

	if _, ok := s.bundles[bundleID]; !ok {
		return fmt.Errorf("bundle not found: %s", bundleID)
	}

	delete(s.bundles, bundleID)
	return s.save()
}

// save writes bundles to disk
func (s *BundleStore) save() error {
	data, err := json.MarshalIndent(s.bundles, "", "  ")
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

// load reads bundles from disk
func (s *BundleStore) load() error {
	data, err := os.ReadFile(s.storagePath)
	if err != nil {
		if os.IsNotExist(err) {
			return nil // No file yet, start fresh
		}
		return err
	}

	return json.Unmarshal(data, &s.bundles)
}
