package apps

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

// UninstallRequest represents an app uninstallation request
type UninstallRequest struct {
	AppID string `json:"app_id"` // App identifier to uninstall
}

// Uninstall removes an app by destroying terraform resources and deleting the module directory
func (u *Uninstaller) Uninstall(req UninstallRequest, progress ProgressCallback) error {
	logger := u.logger.With("app_id", req.AppID)
	logger.Info("starting uninstallation")

	if progress == nil {
		progress = func(ProgressUpdate) {} // No-op if not provided
	}

	modulePath := filepath.Join(u.appsDir, req.AppID)

	// Check if app exists
	if _, err := os.Stat(modulePath); os.IsNotExist(err) {
		return fmt.Errorf("app '%s' not found", req.AppID)
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
	appStoragePath := filepath.Join(GetDataDir(), req.AppID)
	absAppStoragePath, err := filepath.Abs(appStoragePath)
	if err != nil {
		// If we can't get absolute path, try with relative (destroy should still work)
		absAppStoragePath = appStoragePath
	}

	variables := map[string]string{
		"zp_app_id":       req.AppID,
		"zp_network_name": fmt.Sprintf("zeropoint-app-%s", req.AppID),
		"zp_arch":         "amd64", // These don't matter for destroy
		"zp_gpu_vendor":   "",      // These don't matter for destroy
		"zp_app_storage":  absAppStoragePath,
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
