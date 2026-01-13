package internal

import (
	"os"
	"path/filepath"
)

// GetStorageRoot returns the storage root directory from environment or default
func GetStorageRoot() string {
	root := os.Getenv("MODULE_STORAGE_ROOT")
	if root == "" {
		root = "."
	}
	return filepath.Join(root, "data")
}

// GetModulesDir returns the modules directory path
func GetModulesDir() string {
	return filepath.Join(GetStorageRoot(), "modules")
}

// GetDataDir returns the data directory path for module storage
func GetDataDir() string {
	return filepath.Join(GetStorageRoot(), "modules", "storage")
}
