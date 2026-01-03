package catalog

import (
	"fmt"

	"zeropoint-agent/internal/modules"
)

// Resolver handles converting catalog modules and bundles into install requests/plans
type Resolver struct {
	store *Store
}

// NewResolver creates a new catalog resolver
func NewResolver(store *Store) *Resolver {
	return &Resolver{
		store: store,
	}
}

// ResolveModuleToRequest converts a catalog module into an install request
func (r *Resolver) ResolveModuleToRequest(moduleName string) (*modules.InstallRequest, error) {
	module, err := r.store.GetModule(moduleName)
	if err != nil {
		return nil, err
	}

	// Convert catalog module to install request
	// The source is already in the format: url@ref
	request := &modules.InstallRequest{
		Source:   module.Source,
		ModuleID: module.Name,
	}

	return request, nil
}

// ResolveBundleToInstallPlan converts a catalog bundle into an install plan
func (r *Resolver) ResolveBundleToInstallPlan(bundleName string) (*BundleInstallPlan, error) {
	bundle, err := r.store.GetBundle(bundleName)
	if err != nil {
		return nil, err
	}

	plan := &BundleInstallPlan{
		Bundle:  *bundle,
		Modules: make([]modules.InstallRequest, len(bundle.Modules)),
	}

	// Resolve each module in the bundle
	for i, moduleName := range bundle.Modules {
		request, err := r.ResolveModuleToRequest(moduleName)
		if err != nil {
			return nil, fmt.Errorf("failed to resolve module '%s' in bundle '%s': %w", moduleName, bundleName, err)
		}
		plan.Modules[i] = *request
	}

	return plan, nil
}

// ValidateModule checks if a module is available in the catalog
func (r *Resolver) ValidateModule(moduleName string) error {
	_, err := r.store.GetModule(moduleName)
	return err
}

// ValidateBundle checks if a bundle is available in the catalog
func (r *Resolver) ValidateBundle(bundleName string) error {
	bundle, err := r.store.GetBundle(bundleName)
	if err != nil {
		return err
	}

	// Validate all modules in the bundle exist
	for _, moduleName := range bundle.Modules {
		if err := r.ValidateModule(moduleName); err != nil {
			return fmt.Errorf("bundle '%s' contains invalid module '%s': %w", bundleName, moduleName, err)
		}
	}

	return nil
}
