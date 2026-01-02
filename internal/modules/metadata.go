package apps

import (
	"encoding/json"
	"os"
	"path/filepath"
	"time"
)

// Metadata represents the source information for an installed module
type Metadata struct {
	Source   string    `json:"source"`        // Git URL or local path
	Ref      string    `json:"ref,omitempty"` // Git branch/tag if cloned from git
	ClonedAt time.Time `json:"cloned_at"`     // When the module was installed
	ModuleID string    `json:"module_id"`     // Unique module identifier
}

const metadataFileName = ".zeropoint.json"

// SaveMetadata writes module metadata to .zeropoint.json in the module directory
func SaveMetadata(modulePath string, metadata *Metadata) error {
	data, err := json.MarshalIndent(metadata, "", "  ")
	if err != nil {
		return err
	}

	metaPath := filepath.Join(modulePath, metadataFileName)
	return os.WriteFile(metaPath, data, 0644)
}

// LoadMetadata reads app metadata from .zeropoint.json
func LoadMetadata(modulePath string) (*Metadata, error) {
	metaPath := filepath.Join(modulePath, metadataFileName)

	data, err := os.ReadFile(metaPath)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil // No metadata file (locally installed app)
		}
		return nil, err
	}

	var metadata Metadata
	if err := json.Unmarshal(data, &metadata); err != nil {
		return nil, err
	}

	return &metadata, nil
}
