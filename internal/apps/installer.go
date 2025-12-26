package apps

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"time"

	"zeropoint-agent/internal/system"
	"zeropoint-agent/internal/terraform"
	"zeropoint-agent/internal/validator"

	"github.com/google/uuid"
	"github.com/moby/moby/client"
)

// ProgressUpdate represents an installation progress update
type ProgressUpdate struct {
	Status  string `json:"status"`
	Message string `json:"message"`
	Error   string `json:"error,omitempty"`
}

// ProgressCallback is called with progress updates during installation
type ProgressCallback func(ProgressUpdate)

// Installer handles app installation from git or local sources
type Installer struct {
	docker     *client.Client
	appsDir    string
	workingDir string
	logger     *slog.Logger
}

// NewInstaller creates a new app installer
func NewInstaller(docker *client.Client, appsDir string, logger *slog.Logger) *Installer {
	return &Installer{
		docker:     docker,
		appsDir:    appsDir,
		workingDir: os.TempDir(),
		logger:     logger,
	}
}

// InstallRequest represents an app installation request
type InstallRequest struct {
	Source    string `json:"source,omitempty"`     // Git URL (e.g., https://user:pat@github.com/org/repo.git@v1.0)
	LocalPath string `json:"local_path,omitempty"` // Local module path (alternative to Source)
	AppID     string `json:"app_id"`               // Unique app identifier
	Arch      string `json:"arch,omitempty"`       // Optional architecture override
	GPUVendor string `json:"gpu_vendor,omitempty"` // Optional GPU vendor override
}

// Install installs an app from git or local source
func (i *Installer) Install(req InstallRequest, progress ProgressCallback) error {
	logger := i.logger.With("app_id", req.AppID)
	logger.Info("starting installation")

	if progress == nil {
		progress = func(ProgressUpdate) {} // No-op if not provided
	}

	var modulePath string
	var metadata *Metadata

	if req.Source != "" {
		// Install from git
		gitURL, ref := parseGitURL(req.Source)
		logger.Info("cloning from git", "url", gitURL, "ref", ref)
		progress(ProgressUpdate{Status: "cloning", Message: "Cloning repository"})

		// Clone to temporary location
		tmpDir, err := i.cloneFromGit(gitURL, ref)
		if err != nil {
			logger.Error("git clone failed", "error", err)
			return fmt.Errorf("git clone failed: %w", err)
		}
		defer os.RemoveAll(tmpDir)

		// Copy to apps directory (without .git)
		targetPath := filepath.Join(i.appsDir, req.AppID)
		logger.Info("copying module to apps directory", "target", targetPath)
		if err := copyDirWithoutGit(tmpDir, targetPath); err != nil {
			logger.Error("failed to copy module", "error", err)
			return fmt.Errorf("failed to copy module: %w", err)
		}

		// Save metadata
		metadata = &Metadata{
			Source:   gitURL,
			Ref:      ref,
			ClonedAt: time.Now(),
			AppID:    req.AppID,
		}
		if err := SaveMetadata(targetPath, metadata); err != nil {
			logger.Error("failed to save metadata", "error", err)
			return fmt.Errorf("failed to save metadata: %w", err)
		}

		modulePath = targetPath
	} else if req.LocalPath != "" {
		// Use local path directly (no copy)
		logger.Info("using local module", "path", req.LocalPath)
		modulePath = req.LocalPath
	} else {
		return fmt.Errorf("either source or local_path must be provided")
	}

	// Validate module conforms to contract
	logger.Info("validating module")
	progress(ProgressUpdate{Status: "validating", Message: "Validating module"})
	if err := validator.ValidateAppModule(modulePath, req.AppID); err != nil {
		logger.Error("module validation failed", "error", err)
		return fmt.Errorf("module validation failed: %w", err)
	}

	// Create network
	networkName := fmt.Sprintf("zeropoint-app-%s", req.AppID)
	logger.Info("creating docker network", "network", networkName)
	progress(ProgressUpdate{Status: "network", Message: "Creating Docker network"})
	if err := i.createNetwork(networkName); err != nil {
		logger.Error("failed to create network", "error", err)
		return fmt.Errorf("failed to create network: %w", err)
	}

	// Prepare variables
	arch := req.Arch
	if arch == "" {
		arch = runtime.GOARCH
	}

	gpuVendor := req.GPUVendor
	if gpuVendor == "" {
		gpuVendor = system.DetectGPU()
	}
	logger.Info("detected system", "arch", arch, "gpu_vendor", gpuVendor)

	variables := map[string]string{
		"app_id":       req.AppID,
		"network_name": networkName,
		"arch":         arch,
		"gpu_vendor":   gpuVendor,
	}

	// Apply terraform
	logger.Info("applying terraform")
	progress(ProgressUpdate{Status: "applying", Message: "Running terraform apply"})
	executor, err := terraform.NewExecutor(modulePath)
	if err != nil {
		logger.Error("failed to create terraform executor", "error", err)
		return fmt.Errorf("failed to create terraform executor: %w", err)
	}

	if err := executor.Init(); err != nil {
		logger.Error("terraform init failed", "error", err)
		return fmt.Errorf("terraform init failed: %w", err)
	}

	if err := executor.Apply(variables); err != nil {
		logger.Error("terraform apply failed", "error", err)
		return fmt.Errorf("terraform apply failed: %w", err)
	}

	// Validate required outputs exist after apply
	logger.Info("validating outputs")
	outputs, err := executor.Output()
	if err != nil {
		logger.Error("failed to read outputs", "error", err)
		return fmt.Errorf("failed to read outputs: %w", err)
	}

	if _, exists := outputs["main"]; !exists {
		logger.Error("missing required output 'main'")
		return fmt.Errorf("missing required output 'main' - app must expose main container")
	}

	// Validate main_ports output
	// Validate all {container}_ports outputs
	containerCount := 0
	for outputName, outputValue := range outputs {
		if !strings.HasSuffix(outputName, "_ports") {
			continue
		}

		containerName := strings.TrimSuffix(outputName, "_ports")
		containerCount++

		// The Value field may be json.RawMessage from terraform-exec
		var portsValue map[string]interface{}

		// Try to unmarshal if it's JSON
		if jsonData, ok := outputValue.Value.(json.RawMessage); ok {
			if err := json.Unmarshal(jsonData, &portsValue); err != nil {
				logger.Error("failed to unmarshal container ports", "container", containerName, "error", err)
				return fmt.Errorf("failed to parse %s output: %w", outputName, err)
			}
		} else if m, ok := outputValue.Value.(map[string]interface{}); ok {
			// Already a map
			portsValue = m
		} else {
			logger.Error("container ports output has unexpected type", "container", containerName, "type", fmt.Sprintf("%T", outputValue.Value))
			return fmt.Errorf("%s output must be a map of port configurations (got %T)", outputName, outputValue.Value)
		}

		// Validate ports structure
		if portErrors := validator.ValidateContainerPorts(portsValue); len(portErrors) > 0 {
			logger.Error("container ports validation failed", "container", containerName, "errors", portErrors)
			return fmt.Errorf("%s validation failed: %v", outputName, portErrors)
		}

		logger.Info("validated container ports", "container", containerName, "ports", len(portsValue))
	}

	if containerCount == 0 {
		logger.Error("no container port outputs found")
		return fmt.Errorf("app must declare at least one {container}_ports output")
	}

	logger.Info("installation complete", "containers", containerCount)
	progress(ProgressUpdate{Status: "complete", Message: "Installation complete"})
	return nil
}

// parseGitURL splits a git URL like "https://github.com/org/repo.git@v1.0" into URL and ref
func parseGitURL(source string) (gitURL, ref string) {
	parts := strings.Split(source, "@")
	gitURL = parts[0]

	if len(parts) > 1 {
		ref = parts[1]
	} else {
		ref = "HEAD"
	}

	return gitURL, ref
}

// cloneFromGit clones a git repository to a temporary directory
func (i *Installer) cloneFromGit(gitURL, ref string) (string, error) {
	tmpDir := filepath.Join(i.workingDir, "zeropoint-clone-"+uuid.New().String())

	args := []string{"clone", "--depth", "1"}
	if ref != "HEAD" {
		args = append(args, "--branch", ref)
	}
	args = append(args, gitURL, tmpDir)

	cmd := exec.Command("git", args...)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr

	if err := cmd.Run(); err != nil {
		return "", err
	}

	return tmpDir, nil
}

// copyDirWithoutGit copies a directory tree excluding .git folders
func copyDirWithoutGit(src, dst string) error {
	return filepath.Walk(src, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}

		// Skip .git directory
		if info.IsDir() && info.Name() == ".git" {
			return filepath.SkipDir
		}

		relPath, _ := filepath.Rel(src, path)
		dstPath := filepath.Join(dst, relPath)

		if info.IsDir() {
			return os.MkdirAll(dstPath, info.Mode())
		}

		return copyFile(path, dstPath)
	})
}

// copyFile copies a single file
func copyFile(src, dst string) error {
	srcFile, err := os.Open(src)
	if err != nil {
		return err
	}
	defer srcFile.Close()

	dstFile, err := os.Create(dst)
	if err != nil {
		return err
	}
	defer dstFile.Close()

	if _, err := io.Copy(dstFile, srcFile); err != nil {
		return err
	}

	// Preserve permissions
	srcInfo, err := os.Stat(src)
	if err != nil {
		return err
	}
	return os.Chmod(dst, srcInfo.Mode())
}

// createNetwork creates a Docker bridge network
func (i *Installer) createNetwork(name string) error {
	ctx := context.Background()

	// Check if network already exists
	networks, err := i.docker.NetworkList(ctx, client.NetworkListOptions{})
	if err != nil {
		return err
	}

	for _, net := range networks.Items {
		if net.Name == name {
			return nil // Already exists
		}
	}

	// Create network
	_, err = i.docker.NetworkCreate(ctx, name, client.NetworkCreateOptions{})
	return err
}
