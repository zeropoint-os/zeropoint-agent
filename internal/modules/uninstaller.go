package modules

import (
	"fmt"
	"log/slog"
	"os"
	"path/filepath"

	"zeropoint-agent/internal/terraform"
)

// Uninstaller handles app uninstallation
type Uninstaller struct {
	appsDir string
	logger  *slog.Logger
}

// NewUninstaller creates a new app uninstaller
func NewUninstaller(appsDir string, logger *slog.Logger) *Uninstaller {
	return &Uninstaller{
		appsDir: appsDir,
		logger:  logger,
	}
}

// UninstallRequest represents a module uninstallation request
type UninstallRequest struct {
	ModuleID string `json:"module_id"` // Module identifier to uninstall
}

// Uninstall removes a module by destroying terraform resources and deleting the module directory
func (u *Uninstaller) Uninstall(req UninstallRequest, progress ProgressCallback) error {
	logger := u.logger.With("module_id", req.ModuleID)
	logger.Info("starting uninstallation")

	if progress == nil {
		progress = func(ProgressUpdate) {} // No-op if not provided
	}

	modulePath := filepath.Join(u.appsDir, req.ModuleID)

	// Check if module exists
	if _, err := os.Stat(modulePath); os.IsNotExist(err) {
		return fmt.Errorf("module '%s' not found", req.ModuleID)
	}

	// Destroy terraform resources
	logger.Info("destroying terraform resources")
	progress(ProgressUpdate{Status: "destroying", Message: "Destroying infrastructure"})

	executor, err := terraform.NewExecutor(modulePath)
	if err != nil {
		logger.Error("failed to create terraform executor", "error", err)
		return fmt.Errorf("failed to create terraform executor: %w", err)
	}

	// Need to init first
	if err := executor.Init(); err != nil {
		logger.Error("terraform init failed", "error", err)
		return fmt.Errorf("terraform init failed: %w", err)
	}

	// Destroy with auto-approve
	moduleStoragePath := filepath.Join(GetDataDir(), req.ModuleID)
	absModuleStoragePath, err := filepath.Abs(moduleStoragePath)
	if err != nil {
		// If we can't get absolute path, try with relative (destroy should still work)
		absModuleStoragePath = moduleStoragePath
	}

	variables := map[string]string{
		"zp_module_id":      req.ModuleID,
		"zp_network_name":   fmt.Sprintf("zeropoint-module-%s", req.ModuleID),
		"zp_arch":           "amd64", // These don't matter for destroy
		"zp_gpu_vendor":     "",      // These don't matter for destroy
		"zp_module_storage": absModuleStoragePath,
	}

	if err := executor.Destroy(variables); err != nil {
		logger.Error("terraform destroy failed", "error", err)
		return fmt.Errorf("terraform destroy failed: %w", err)
	}

	// Remove app directory
	logger.Info("removing app directory")
	progress(ProgressUpdate{Status: "cleaning", Message: "Removing app directory"})

	if err := os.RemoveAll(modulePath); err != nil {
		logger.Error("failed to remove app directory", "error", err)
		return fmt.Errorf("failed to remove app directory: %w", err)
	}

	logger.Info("uninstallation complete")
	progress(ProgressUpdate{Status: "complete", Message: "Uninstallation complete"})

	return nil
}
