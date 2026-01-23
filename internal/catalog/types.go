package catalog

import (
	"time"

	"zeropoint-agent/internal/modules"
)

// CatalogModule represents a module definition from the catalog
type CatalogModule struct {
	Name        string `yaml:"name" json:"name"`
	Source      string `yaml:"source" json:"source"`
	Type        string `yaml:"type,omitempty" json:"type,omitempty"`
	Description string `yaml:"description,omitempty" json:"description,omitempty"`
}

// CatalogBundle represents a bundle definition from the catalog
type CatalogBundle struct {
	Name        string                    `yaml:"name" json:"name"`
	Description string                    `yaml:"description,omitempty" json:"description,omitempty"`
	Modules     []string                  `yaml:"modules" json:"modules"`
	Links       map[string][]BundleLink   `yaml:"links,omitempty" json:"links,omitempty"`
	Exposures   map[string]BundleExposure `yaml:"exposures,omitempty" json:"exposures,omitempty"`
}

// BundleLink represents a link definition within a bundle
type BundleLink struct {
	Module string            `yaml:"module" json:"module"`
	Bind   map[string]string `yaml:"bind,omitempty" json:"bind,omitempty"`
}

// BundleExposure represents an exposure definition within a bundle
type BundleExposure struct {
	Module      string `yaml:"module" json:"module"`
	Protocol    string `yaml:"protocol" json:"protocol"`
	ModulePort  int    `yaml:"module_port" json:"module_port"`
	Description string `yaml:"description,omitempty" json:"description,omitempty"`
}

// BundleInstallPlan represents the install plan for a bundle
type BundleInstallPlan struct {
	Bundle  CatalogBundle            `json:"bundle"`
	Modules []modules.InstallRequest `json:"modules"`
}

// ModuleResponse represents the response for getting a specific module
type ModuleResponse struct {
	Name        string `json:"name"`
	Source      string `json:"source"`
	Type        string `json:"type,omitempty"`
	Description string `json:"description,omitempty"`
}

// BundleResponse represents the response for getting a specific bundle
type BundleResponse struct {
	Name        string                    `json:"name"`
	Description string                    `json:"description,omitempty"`
	Modules     []string                  `json:"modules"`
	Links       map[string][]BundleLink   `json:"links,omitempty"`
	Exposures   map[string]BundleExposure `json:"exposures,omitempty"`
}

// UpdateResponse represents the response for catalog update
type UpdateResponse struct {
	Status      string    `json:"status"`
	Message     string    `json:"message"`
	ModuleCount int       `json:"modules_count"`
	BundleCount int       `json:"bundles_count"`
	Timestamp   time.Time `json:"timestamp"`
}
