package catalog

import (
	"fmt"
	"log/slog"
	"os"
	"os/exec"
	"path/filepath"
	"sync"

	internalPaths "zeropoint-agent/internal"

	"gopkg.in/yaml.v3"
)

const (
	defaultCatalogURL = "https://github.com/zeropoint-os/catalog.git"
	catalogDir        = "./catalog"
	modulesDir        = "modules"
	bundlesDir        = "bundles"
)

// Store manages the local catalog repository and provides access to modules and bundles
type Store struct {
	catalogPath string
	logger      *slog.Logger
	mutex       sync.RWMutex
}

// NewStore creates a new catalog store
func NewStore(logger *slog.Logger) *Store {
	return &Store{
		catalogPath: filepath.Join(internalPaths.GetStorageRoot(), catalogDir),
		logger:      logger,
	}
}

// Update clones or pulls the latest catalog from the remote repository
func (s *Store) Update() error {
	s.mutex.Lock()
	defer s.mutex.Unlock()

	s.logger.Info("updating catalog", "path", s.catalogPath)

	// Check if catalog directory exists
	if _, err := os.Stat(s.catalogPath); os.IsNotExist(err) {
		// Clone the catalog
		s.logger.Info("cloning catalog repository", "url", defaultCatalogURL)
		cmd := exec.Command("git", "clone", defaultCatalogURL, s.catalogPath)
		cmd.Stdout = os.Stdout
		cmd.Stderr = os.Stderr

		if err := cmd.Run(); err != nil {
			return fmt.Errorf("failed to clone catalog: %w", err)
		}
	} else {
		// Pull latest changes
		s.logger.Info("pulling catalog updates")
		cmd := exec.Command("git", "pull")
		cmd.Dir = s.catalogPath
		cmd.Stdout = os.Stdout
		cmd.Stderr = os.Stderr

		if err := cmd.Run(); err != nil {
			return fmt.Errorf("failed to pull catalog updates: %w", err)
		}
	}

	s.logger.Info("catalog updated successfully")
	return nil
}

// GetModules returns all modules from the catalog
func (s *Store) GetModules() ([]CatalogModule, error) {
	s.mutex.RLock()
	defer s.mutex.RUnlock()

	modulesPath := filepath.Join(s.catalogPath, modulesDir)
	entries, err := os.ReadDir(modulesPath)
	if err != nil {
		if os.IsNotExist(err) {
			// Catalog not initialized yet - return empty list instead of error
			s.logger.Debug("modules directory not found, returning empty list", "path", modulesPath)
			return []CatalogModule{}, nil
		}
		return nil, fmt.Errorf("failed to read modules directory: %w", err)
	}

	var modules []CatalogModule
	for _, entry := range entries {
		if !entry.IsDir() && filepath.Ext(entry.Name()) == ".yaml" {
			modulePath := filepath.Join(modulesPath, entry.Name())
			module, err := s.parseModule(modulePath)
			if err != nil {
				s.logger.Warn("failed to parse module", "file", entry.Name(), "error", err)
				continue
			}
			modules = append(modules, module)
		}
	}

	return modules, nil
}

// GetModule returns a specific module by name
func (s *Store) GetModule(name string) (*CatalogModule, error) {
	s.mutex.RLock()
	defer s.mutex.RUnlock()

	modulePath := filepath.Join(s.catalogPath, modulesDir, name+".yaml")
	if _, err := os.Stat(modulePath); os.IsNotExist(err) {
		return nil, fmt.Errorf("module '%s' not found in catalog", name)
	}

	module, err := s.parseModule(modulePath)
	if err != nil {
		return nil, fmt.Errorf("failed to parse module '%s': %w", name, err)
	}

	return &module, nil
}

// GetBundles returns all bundles from the catalog
func (s *Store) GetBundles() ([]CatalogBundle, error) {
	s.mutex.RLock()
	defer s.mutex.RUnlock()

	bundlesPath := filepath.Join(s.catalogPath, bundlesDir)
	entries, err := os.ReadDir(bundlesPath)
	if err != nil {
		if os.IsNotExist(err) {
			// Catalog not initialized yet - return empty list instead of error
			s.logger.Debug("bundles directory not found, returning empty list", "path", bundlesPath)
			return []CatalogBundle{}, nil
		}
		return nil, fmt.Errorf("failed to read bundles directory: %w", err)
	}

	var bundles []CatalogBundle
	for _, entry := range entries {
		if !entry.IsDir() && filepath.Ext(entry.Name()) == ".yaml" {
			bundlePath := filepath.Join(bundlesPath, entry.Name())
			bundle, err := s.parseBundle(bundlePath)
			if err != nil {
				s.logger.Warn("failed to parse bundle", "file", entry.Name(), "error", err)
				continue
			}
			bundles = append(bundles, bundle)
		}
	}

	return bundles, nil
}

// GetBundle returns a specific bundle by name
func (s *Store) GetBundle(name string) (*CatalogBundle, error) {
	s.mutex.RLock()
	defer s.mutex.RUnlock()

	bundlePath := filepath.Join(s.catalogPath, bundlesDir, name+".yaml")
	if _, err := os.Stat(bundlePath); os.IsNotExist(err) {
		return nil, fmt.Errorf("bundle '%s' not found in catalog", name)
	}

	bundle, err := s.parseBundle(bundlePath)
	if err != nil {
		return nil, fmt.Errorf("failed to parse bundle '%s': %w", name, err)
	}

	return &bundle, nil
}

// GetStats returns statistics about the catalog
func (s *Store) GetStats() (int, int, error) {
	modules, err := s.GetModules()
	if err != nil {
		return 0, 0, err
	}

	bundles, err := s.GetBundles()
	if err != nil {
		return 0, 0, err
	}

	return len(modules), len(bundles), nil
}

// parseModule parses a module YAML file
func (s *Store) parseModule(filePath string) (CatalogModule, error) {
	data, err := os.ReadFile(filePath)
	if err != nil {
		return CatalogModule{}, err
	}

	var module CatalogModule
	if err := yaml.Unmarshal(data, &module); err != nil {
		return CatalogModule{}, err
	}

	return module, nil
}

// parseBundle parses a bundle YAML file
func (s *Store) parseBundle(filePath string) (CatalogBundle, error) {
	data, err := os.ReadFile(filePath)
	if err != nil {
		return CatalogBundle{}, err
	}

	var bundle CatalogBundle
	if err := yaml.Unmarshal(data, &bundle); err != nil {
		return CatalogBundle{}, err
	}

	return bundle, nil
}
