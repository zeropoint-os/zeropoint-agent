package queue

import (
	"bufio"
	"context"
	"fmt"
	"io"
	"log/slog"
	"os/exec"
	"strings"
	"time"

	"zeropoint-agent/internal/catalog"
	"zeropoint-agent/internal/modules"
)

// ExposureHandler interface for creating/deleting exposures
type ExposureHandler interface {
	CreateExposure(ctx context.Context, exposureID, moduleID, protocol, hostname string, containerPort uint32, tags []string) error
	DeleteExposure(ctx context.Context, exposureID string) error
}

// LinkHandler interface for creating/deleting links
type LinkHandler interface {
	CreateLink(ctx context.Context, linkID string, modules map[string]map[string]interface{}, tags []string) error
	DeleteLink(ctx context.Context, id string) error
}

// BundleStoreHandler interface for persisting bundle installations
type BundleStoreHandler interface {
	CreateBundle(bundleID, bundleName, jobID string) interface{}
	AddModuleComponent(bundleID, moduleID string, status, errMsg string) error
	AddLinkComponent(bundleID, linkID string, status, errMsg string) error
	AddExposureComponent(bundleID, exposureID string, status, errMsg string) error
	UpdateModuleComponentStatus(bundleID, moduleID, status, errMsg string) error
	UpdateLinkComponentStatus(bundleID, linkID, status, errMsg string) error
	UpdateExposureComponentStatus(bundleID, exposureID, status, errMsg string) error
	GetBundle(bundleID string) (interface{}, error)
	CompleteBundleInstallation(bundleID string, success bool) error
	DeleteBundle(bundleID string) error
}

// JobExecutor executes queued commands by calling handlers and installers directly
type JobExecutor struct {
	installer       *modules.Installer
	uninstaller     *modules.Uninstaller
	exposureHandler ExposureHandler
	linkHandler     LinkHandler
	catalogStore    *catalog.Store
	bundleStore     BundleStoreHandler
	logger          *slog.Logger
}

// NewJobExecutor creates a new job executor with direct access to handlers
func NewJobExecutor(installer *modules.Installer, uninstaller *modules.Uninstaller, exposureHandler ExposureHandler, linkHandler LinkHandler, catalogStore *catalog.Store, bundleStore BundleStoreHandler, logger *slog.Logger) *JobExecutor {
	return &JobExecutor{
		installer:       installer,
		uninstaller:     uninstaller,
		exposureHandler: exposureHandler,
		linkHandler:     linkHandler,
		catalogStore:    catalogStore,
		bundleStore:     bundleStore,
		logger:          logger,
	}
}

// ExecuteWithJob runs a command and captures progress events in the job
func (e *JobExecutor) ExecuteWithJob(ctx context.Context, jobID string, manager *Manager, cmd Command) (interface{}, error) {
	switch cmd.Type {
	case CmdInstallModule:
		return e.executeInstallModule(ctx, jobID, manager, cmd)
	case CmdUninstallModule:
		return e.executeUninstallModule(ctx, jobID, manager, cmd)
	case CmdCreateExposure:
		return e.executeCreateExposure(ctx, jobID, manager, cmd)
	case CmdDeleteExposure:
		return e.executeDeleteExposure(ctx, jobID, manager, cmd)
	case CmdCreateLink:
		return e.executeCreateLink(ctx, jobID, manager, cmd)
	case CmdDeleteLink:
		return e.executeDeleteLink(ctx, jobID, manager, cmd)
	case CmdBundleInstall:
		return e.executeBundleInstall(ctx, jobID, manager, cmd)
	case CmdBundleUninstall:
		return e.executeBundleUninstall(ctx, jobID, manager, cmd)
	case CmdFormatDisk:
		return e.executeFormatDisk(ctx, jobID, manager, cmd)
	default:
		return nil, fmt.Errorf("unknown command type: %s", cmd.Type)
	}
}

// runCmdJobStream runs a command and appends stdout/stderr lines as job events
func runCmdJobStream(ctx context.Context, manager *Manager, jobID string, name string, args ...string) error {
	cmd := exec.CommandContext(ctx, name, args...)
	stdout, _ := cmd.StdoutPipe()
	stderr, _ := cmd.StderrPipe()
	if err := cmd.Start(); err != nil {
		_ = manager.AppendEvent(jobID, Event{Timestamp: time.Now().UTC(), Type: "error", Message: "failed to start command: " + err.Error()})
		return err
	}

	stream := func(r io.Reader, streamName string) {
		scanner := bufio.NewScanner(r)
		for scanner.Scan() {
			_ = manager.AppendEvent(jobID, Event{Timestamp: time.Now().UTC(), Type: "log", Message: streamName + ": " + scanner.Text()})
		}
	}
	go stream(stdout, "stdout")
	go stream(stderr, "stderr")

	if err := cmd.Wait(); err != nil {
		_ = manager.AppendEvent(jobID, Event{Timestamp: time.Now().UTC(), Type: "error", Message: "command failed: " + err.Error()})
		return err
	}
	return nil
}

// executeFormatDisk executes a format_disk command and records progress to job events
func (e *JobExecutor) executeFormatDisk(ctx context.Context, jobID string, manager *Manager, cmd Command) (interface{}, error) {
	// Extract args
	sysPath, _ := cmd.Args["sys_path"].(string)
	if sysPath == "" {
		return nil, fmt.Errorf("sys_path is required")
	}

	filesystem, _ := cmd.Args["filesystem"].(string)
	label, _ := cmd.Args["label"].(string)

	wipefs := false
	if v, ok := cmd.Args["wipefs"].(bool); ok {
		wipefs = v
	}

	confirm := false
	if v, ok := cmd.Args["confirm"].(bool); ok {
		confirm = v
	}

	confirmFixed := false
	if v, ok := cmd.Args["confirm_fixed_disk_operation"].(bool); ok {
		confirmFixed = v
	}

	autoPartition := false
	if v, ok := cmd.Args["auto_partition"].(bool); ok {
		autoPartition = v
	}

	if !confirm {
		return nil, fmt.Errorf("confirm must be true for destructive operation")
	}

	// Check transport for fixed-disk confirmation
	transport := ""
	if sysPath != "" {
		ctxT, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		out, err := exec.CommandContext(ctxT, "lsblk", "-dn", "-o", "TRAN", sysPath).Output()
		if err == nil {
			transport = strings.TrimSpace(string(out))
		}
	}
	if transport != "usb" && transport != "" && !confirmFixed {
		return nil, fmt.Errorf("confirm_fixed_disk_operation must be true for non-USB devices")
	}

	// Determine device type
	devType := ""
	{
		ctxT, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		defer cancel()
		out, err := exec.CommandContext(ctxT, "lsblk", "-dn", "-o", "TYPE", sysPath).Output()
		if err != nil {
			return nil, fmt.Errorf("unable to determine device type or device not found")
		}
		devType = strings.TrimSpace(string(out))
		if devType == "" {
			return nil, fmt.Errorf("unable to determine device type for path: %s", sysPath)
		}
	}

	if devType == "disk" && !autoPartition {
		return nil, fmt.Errorf("refusing to format whole-disk without explicit partition intent (auto_partition)")
	}

	// Step: dry-run wipefs
	_ = manager.AppendEvent(jobID, Event{Timestamp: time.Now().UTC(), Type: "step", Message: "dry-run: wipefs -n"})
	if err := runCmdJobStream(ctx, manager, jobID, "wipefs", "-n", sysPath); err != nil {
		_ = manager.AppendEvent(jobID, Event{Timestamp: time.Now().UTC(), Type: "final", Message: "failed dry-run: " + err.Error()})
		return nil, err
	}

	if wipefs {
		_ = manager.AppendEvent(jobID, Event{Timestamp: time.Now().UTC(), Type: "step", Message: "running: wipefs -a"})
		if err := runCmdJobStream(ctx, manager, jobID, "wipefs", "-a", sysPath); err != nil {
			_ = manager.AppendEvent(jobID, Event{Timestamp: time.Now().UTC(), Type: "final", Message: "wipefs failed: " + err.Error()})
			return nil, err
		}
	}

	targetPath := sysPath
	if autoPartition && devType == "disk" {
		_ = manager.AppendEvent(jobID, Event{Timestamp: time.Now().UTC(), Type: "step", Message: "creating GPT partition table (zap)"})
		if err := runCmdJobStream(ctx, manager, jobID, "sgdisk", "--zap-all", sysPath); err != nil {
			_ = manager.AppendEvent(jobID, Event{Timestamp: time.Now().UTC(), Type: "final", Message: "sgdisk zap failed: " + err.Error()})
			return nil, err
		}

		_ = manager.AppendEvent(jobID, Event{Timestamp: time.Now().UTC(), Type: "step", Message: "creating single partition to fill disk"})
		if err := runCmdJobStream(ctx, manager, jobID, "sgdisk", "--new=1:1MiB:0", "--typecode=1:8300", sysPath); err != nil {
			_ = manager.AppendEvent(jobID, Event{Timestamp: time.Now().UTC(), Type: "final", Message: "sgdisk create failed: " + err.Error()})
			return nil, err
		}

		_ = runCmdJobStream(ctx, manager, jobID, "partprobe", sysPath)
		_ = runCmdJobStream(ctx, manager, jobID, "udevadm", "settle")

		var partPath string
		found := false
		for i := 0; i < 10; i++ {
			ctxT, cancel := context.WithTimeout(context.Background(), 2*time.Second)
			out, _ := exec.CommandContext(ctxT, "lsblk", "-ln", "-o", "KNAME,TYPE", sysPath).Output()
			cancel()
			lines := strings.Split(strings.TrimSpace(string(out)), "\n")
			for _, ln := range lines {
				f := strings.Fields(ln)
				if len(f) >= 2 && f[1] == "part" {
					partPath = "/dev/" + f[0]
					found = true
					break
				}
			}
			if found {
				break
			}
			time.Sleep(500 * time.Millisecond)
		}
		if !found {
			_ = manager.AppendEvent(jobID, Event{Timestamp: time.Now().UTC(), Type: "final", Message: "failed to detect created partition device"})
			return nil, fmt.Errorf("failed to detect created partition device")
		}
		_ = manager.AppendEvent(jobID, Event{Timestamp: time.Now().UTC(), Type: "step", Message: "detected partition: " + partPath})
		targetPath = partPath
	}

	// mkfs
	fs := filesystem
	if fs == "" {
		fs = "ext4"
	}
	_ = manager.AppendEvent(jobID, Event{Timestamp: time.Now().UTC(), Type: "step", Message: "mkfs: " + fs + " on " + targetPath})
	if fs == "ext4" {
		if err := runCmdJobStream(ctx, manager, jobID, "mkfs.ext4", "-F", "-L", label, targetPath); err != nil {
			_ = manager.AppendEvent(jobID, Event{Timestamp: time.Now().UTC(), Type: "final", Message: "mkfs failed: " + err.Error()})
			return nil, err
		}
	} else if fs == "xfs" {
		if err := runCmdJobStream(ctx, manager, jobID, "mkfs.xfs", "-f", "-L", label, targetPath); err != nil {
			_ = manager.AppendEvent(jobID, Event{Timestamp: time.Now().UTC(), Type: "final", Message: "mkfs failed: " + err.Error()})
			return nil, err
		}
	} else {
		_ = manager.AppendEvent(jobID, Event{Timestamp: time.Now().UTC(), Type: "final", Message: "unsupported filesystem: " + fs})
		return nil, fmt.Errorf("unsupported filesystem: %s", fs)
	}

	// blkid
	_ = manager.AppendEvent(jobID, Event{Timestamp: time.Now().UTC(), Type: "step", Message: "reading blkid on " + targetPath})
	blkidOut := ""
	if out, err := exec.CommandContext(ctx, "blkid", "-s", "UUID", "-o", "value", targetPath).Output(); err == nil {
		blkidOut = strings.TrimSpace(string(out))
	}

	finalMsg := fmt.Sprintf("{\"device\": \"%s\", \"uuid\": \"%s\"}", targetPath, blkidOut)
	_ = manager.AppendEvent(jobID, Event{Timestamp: time.Now().UTC(), Type: "final", Message: finalMsg})

	result := map[string]interface{}{"device": targetPath, "uuid": blkidOut}
	return result, nil
}

// executeInstallModule runs an install_module command with direct installer call
func (e *JobExecutor) executeInstallModule(ctx context.Context, jobID string, manager *Manager, cmd Command) (interface{}, error) {
	moduleID, ok := cmd.Args["module_id"].(string)
	if !ok || moduleID == "" {
		return nil, fmt.Errorf("module_id is required")
	}

	source, _ := cmd.Args["source"].(string)
	localPath, _ := cmd.Args["local_path"].(string)

	if source == "" && localPath == "" {
		return nil, fmt.Errorf("either source or local_path is required")
	}

	// Extract tags if provided
	var tags []string
	if tagsInterface, ok := cmd.Args["tags"]; ok {
		if tagsSlice, ok := tagsInterface.([]interface{}); ok {
			for _, tag := range tagsSlice {
				if tagStr, ok := tag.(string); ok {
					tags = append(tags, tagStr)
				}
			}
		} else if tagsSlice, ok := tagsInterface.([]string); ok {
			tags = tagsSlice
		}
	}

	// Create progress callback that appends events to the job
	progressCallback := func(update modules.ProgressUpdate) {
		event := Event{
			Timestamp: time.Now().UTC(),
			Type:      "progress",
			Message:   update.Message,
			Data: map[string]string{
				"status": update.Status,
			},
		}
		if update.Error != "" {
			event.Type = "error"
			event.Data.(map[string]string)["error"] = update.Error
		}

		if err := manager.AppendEvent(jobID, event); err != nil {
			e.logger.Error("failed to append progress event", "job_id", jobID, "error", err)
		}
	}

	// Build install request
	req := modules.InstallRequest{
		ModuleID:  moduleID,
		Source:    source,
		LocalPath: localPath,
		Tags:      tags,
	}

	// Call installer directly with progress callback
	if err := e.installer.Install(req, progressCallback); err != nil {
		return nil, fmt.Errorf("installation failed: %w", err)
	}

	result := map[string]interface{}{
		"module_id": moduleID,
		"status":    "installed",
	}

	return result, nil
}

// executeUninstallModule runs an uninstall_module command with direct uninstaller call
func (e *JobExecutor) executeUninstallModule(ctx context.Context, jobID string, manager *Manager, cmd Command) (interface{}, error) {
	moduleID, ok := cmd.Args["module_id"].(string)
	if !ok || moduleID == "" {
		return nil, fmt.Errorf("module_id is required")
	}

	// Create progress callback that appends events to the job
	progressCallback := func(update modules.ProgressUpdate) {
		event := Event{
			Timestamp: time.Now().UTC(),
			Type:      "progress",
			Message:   update.Message,
			Data: map[string]string{
				"status": update.Status,
			},
		}
		if update.Error != "" {
			event.Type = "error"
			event.Data.(map[string]string)["error"] = update.Error
		}

		if err := manager.AppendEvent(jobID, event); err != nil {
			e.logger.Error("failed to append progress event", "job_id", jobID, "error", err)
		}
	}

	// Build uninstall request
	req := modules.UninstallRequest{
		ModuleID: moduleID,
	}

	// Call uninstaller directly with progress callback
	if err := e.uninstaller.Uninstall(req, progressCallback); err != nil {
		return nil, fmt.Errorf("uninstallation failed: %w", err)
	}

	result := map[string]interface{}{
		"module_id": moduleID,
		"status":    "uninstalled",
	}

	return result, nil
}

// executeCreateExposure runs a create_exposure command
func (e *JobExecutor) executeCreateExposure(ctx context.Context, jobID string, manager *Manager, cmd Command) (interface{}, error) {
	exposureID, ok := cmd.Args["exposure_id"].(string)
	if !ok || exposureID == "" {
		return nil, fmt.Errorf("exposure_id is required")
	}

	moduleID, ok := cmd.Args["module_id"].(string)
	if !ok || moduleID == "" {
		return nil, fmt.Errorf("module_id is required")
	}

	protocol, ok := cmd.Args["protocol"].(string)
	if !ok || protocol == "" {
		return nil, fmt.Errorf("protocol is required")
	}

	containerPort, ok := cmd.Args["container_port"].(int)
	if !ok {
		// Try to convert from float64 (JSON numbers come as float64)
		if portFloat, ok := cmd.Args["container_port"].(float64); ok {
			containerPort = int(portFloat)
		} else {
			return nil, fmt.Errorf("container_port is required and must be an integer")
		}
	}

	hostname, _ := cmd.Args["hostname"].(string)

	var tags []string
	if tagsInterface, ok := cmd.Args["tags"]; ok {
		if tagsSlice, ok := tagsInterface.([]interface{}); ok {
			for _, tag := range tagsSlice {
				if tagStr, ok := tag.(string); ok {
					tags = append(tags, tagStr)
				}
			}
		} else if tagsSlice, ok := tagsInterface.([]string); ok {
			tags = tagsSlice
		}
	}

	e.logger.Info("creating exposure", "exposure_id", exposureID, "module_id", moduleID)

	// Call exposure handler method directly to create exposure
	if err := e.exposureHandler.CreateExposure(ctx, exposureID, moduleID, protocol, hostname, uint32(containerPort), tags); err != nil {
		e.logger.Error("failed to create exposure", "exposure_id", exposureID, "error", err)
		return nil, fmt.Errorf("failed to create exposure: %w", err)
	}

	result := map[string]interface{}{
		"exposure_id": exposureID,
		"module_id":   moduleID,
		"protocol":    protocol,
		"status":      "created",
	}

	return result, nil
}

// executeDeleteExposure runs a delete_exposure command
func (e *JobExecutor) executeDeleteExposure(ctx context.Context, jobID string, manager *Manager, cmd Command) (interface{}, error) {
	exposureID, ok := cmd.Args["exposure_id"].(string)
	if !ok || exposureID == "" {
		return nil, fmt.Errorf("exposure_id is required")
	}

	e.logger.Info("deleting exposure", "exposure_id", exposureID)

	// Call exposure handler method directly to delete exposure
	if err := e.exposureHandler.DeleteExposure(ctx, exposureID); err != nil {
		e.logger.Error("failed to delete exposure", "exposure_id", exposureID, "error", err)
		return nil, fmt.Errorf("failed to delete exposure: %w", err)
	}

	result := map[string]interface{}{
		"exposure_id": exposureID,
		"status":      "deleted",
	}

	return result, nil
}

// executeCreateLink runs a create_link command
func (e *JobExecutor) executeCreateLink(ctx context.Context, jobID string, manager *Manager, cmd Command) (interface{}, error) {
	linkID, ok := cmd.Args["link_id"].(string)
	if !ok || linkID == "" {
		return nil, fmt.Errorf("link_id is required")
	}

	modules, ok := cmd.Args["modules"].(map[string]interface{})
	if !ok {
		return nil, fmt.Errorf("modules is required")
	}

	var tags []string
	if tagsInterface, ok := cmd.Args["tags"]; ok {
		if tagsSlice, ok := tagsInterface.([]interface{}); ok {
			for _, tag := range tagsSlice {
				if tagStr, ok := tag.(string); ok {
					tags = append(tags, tagStr)
				}
			}
		} else if tagsSlice, ok := tagsInterface.([]string); ok {
			tags = tagsSlice
		}
	}

	e.logger.Info("creating link", "link_id", linkID)

	// Convert modules to the correct type for the handler
	modulesConfig := make(map[string]map[string]interface{})
	for moduleName, config := range modules {
		if moduleConfig, ok := config.(map[string]interface{}); ok {
			modulesConfig[moduleName] = moduleConfig
		} else {
			return nil, fmt.Errorf("module %s config must be a map", moduleName)
		}
	}

	// Call link handler method directly to create link
	if err := e.linkHandler.CreateLink(ctx, linkID, modulesConfig, tags); err != nil {
		e.logger.Error("failed to create link", "link_id", linkID, "error", err)
		return nil, fmt.Errorf("failed to create link: %w", err)
	}

	result := map[string]interface{}{
		"link_id": linkID,
		"modules": modulesConfig,
		"tags":    tags,
		"status":  "created",
	}

	return result, nil
}

// executeDeleteLink runs a delete_link command
func (e *JobExecutor) executeDeleteLink(ctx context.Context, jobID string, manager *Manager, cmd Command) (interface{}, error) {
	linkID, ok := cmd.Args["link_id"].(string)
	if !ok || linkID == "" {
		return nil, fmt.Errorf("link_id is required")
	}

	e.logger.Info("deleting link", "link_id", linkID)

	// Call link handler method directly to delete link
	if err := e.linkHandler.DeleteLink(ctx, linkID); err != nil {
		e.logger.Error("failed to delete link", "link_id", linkID, "error", err)
		return nil, fmt.Errorf("failed to delete link: %w", err)
	}

	result := map[string]interface{}{
		"link_id": linkID,
		"status":  "deleted",
	}

	return result, nil
}

// executeBundleInstall runs a bundle_install command
// The bundle_install is a meta-job that orchestrates installation of all bundle components.
// All component jobs (modules, links, exposures) are created by the handler (EnqueueBundleInstall)
// when the meta-job is first enqueued, and the meta-job's DependsOn field is set to all of them.
// When this executor runs, all component jobs are guaranteed to be complete, so we update their statuses.
func (e *JobExecutor) executeBundleInstall(ctx context.Context, jobID string, manager *Manager, cmd Command) (interface{}, error) {
	bundleName, ok := cmd.Args["bundle_name"].(string)
	if !ok || bundleName == "" {
		return nil, fmt.Errorf("bundle_name is required")
	}

	bundleID, ok := cmd.Args["bundle_id"].(string)
	if !ok || bundleID == "" {
		return nil, fmt.Errorf("bundle_id is required")
	}

	// Get the current job to find all dependency jobs
	job, err := manager.Get(jobID)
	if err != nil {
		e.logger.Error("failed to get job", "job_id", jobID, "error", err)
		return nil, err
	}

	// Update component statuses based on their job results
	if e.bundleStore != nil {
		// Check each dependency job to see if it succeeded or failed
		for _, depJobID := range job.DependsOn {
			depJob, err := manager.Get(depJobID)
			if err != nil {
				e.logger.Warn("failed to get dependency job", "dep_job_id", depJobID, "error", err)
				continue
			}

			if depJob.Command.Type == CmdInstallModule {
				moduleID, _ := depJob.Command.Args["module_id"].(string)
				if depJob.Status == StatusCompleted {
					_ = e.bundleStore.UpdateModuleComponentStatus(bundleID, moduleID, "completed", "")
				} else if depJob.Status == StatusFailed {
					_ = e.bundleStore.UpdateModuleComponentStatus(bundleID, moduleID, "failed", depJob.Error)
				}
			} else if depJob.Command.Type == CmdCreateLink {
				linkID, _ := depJob.Command.Args["link_id"].(string)
				if depJob.Status == StatusCompleted {
					_ = e.bundleStore.UpdateLinkComponentStatus(bundleID, linkID, "completed", "")
				} else if depJob.Status == StatusFailed {
					_ = e.bundleStore.UpdateLinkComponentStatus(bundleID, linkID, "failed", depJob.Error)
				}
			} else if depJob.Command.Type == CmdCreateExposure {
				exposureID, _ := depJob.Command.Args["exposure_id"].(string)
				if depJob.Status == StatusCompleted {
					_ = e.bundleStore.UpdateExposureComponentStatus(bundleID, exposureID, "completed", "")
				} else if depJob.Status == StatusFailed {
					_ = e.bundleStore.UpdateExposureComponentStatus(bundleID, exposureID, "failed", depJob.Error)
				}
			}
		}

		// Mark bundle installation complete
		_ = e.bundleStore.CompleteBundleInstallation(bundleID, true)
	}

	event := Event{
		Timestamp: time.Now().UTC(),
		Type:      "info",
		Message:   fmt.Sprintf("Bundle installation completed: %s", bundleName),
	}
	if err := manager.AppendEvent(jobID, event); err != nil {
		e.logger.Error("failed to append event", "job_id", jobID, "error", err)
	}

	result := map[string]interface{}{
		"bundle_name": bundleName,
		"bundle_id":   bundleID,
		"status":      "completed",
	}

	return result, nil
}

// executeBundleUninstall runs a bundle_uninstall command
// The bundle_uninstall is a meta-job that orchestrates uninstallation of all bundle components.
// All component jobs (exposures, links, modules) are created by the handler (EnqueueBundleUninstall)
// when the meta-job is first enqueued, and the meta-job's DependsOn field is set to all of them.
// When this executor runs, all component jobs are guaranteed to be complete, so we delete the bundle.
func (e *JobExecutor) executeBundleUninstall(ctx context.Context, jobID string, manager *Manager, cmd Command) (interface{}, error) {
	bundleID, ok := cmd.Args["bundle_id"].(string)
	if !ok || bundleID == "" {
		return nil, fmt.Errorf("bundle_id is required")
	}

	// Get the current job to find all dependency jobs
	job, err := manager.Get(jobID)
	if err != nil {
		e.logger.Error("failed to get job", "job_id", jobID, "error", err)
		return nil, err
	}

	// Update component statuses based on their job results
	if e.bundleStore != nil {
		// Check each dependency job to see if it succeeded or failed
		for _, depJobID := range job.DependsOn {
			depJob, err := manager.Get(depJobID)
			if err != nil {
				e.logger.Warn("failed to get dependency job", "dep_job_id", depJobID, "error", err)
				continue
			}

			if depJob.Command.Type == CmdUninstallModule {
				moduleID, _ := depJob.Command.Args["module_id"].(string)
				if depJob.Status == StatusCompleted {
					_ = e.bundleStore.UpdateModuleComponentStatus(bundleID, moduleID, "deleted", "")
				} else if depJob.Status == StatusFailed {
					_ = e.bundleStore.UpdateModuleComponentStatus(bundleID, moduleID, "failed", depJob.Error)
				}
			} else if depJob.Command.Type == CmdDeleteLink {
				linkID, _ := depJob.Command.Args["link_id"].(string)
				if depJob.Status == StatusCompleted {
					_ = e.bundleStore.UpdateLinkComponentStatus(bundleID, linkID, "deleted", "")
				} else if depJob.Status == StatusFailed {
					_ = e.bundleStore.UpdateLinkComponentStatus(bundleID, linkID, "failed", depJob.Error)
				}
			} else if depJob.Command.Type == CmdDeleteExposure {
				exposureID, _ := depJob.Command.Args["exposure_id"].(string)
				if depJob.Status == StatusCompleted {
					_ = e.bundleStore.UpdateExposureComponentStatus(bundleID, exposureID, "deleted", "")
				} else if depJob.Status == StatusFailed {
					_ = e.bundleStore.UpdateExposureComponentStatus(bundleID, exposureID, "failed", depJob.Error)
				}
			}
		}

		// Delete the bundle record from the store
		if err := e.bundleStore.DeleteBundle(bundleID); err != nil {
			e.logger.Warn("failed to delete bundle record", "bundle_id", bundleID, "error", err)
		}
	}

	event := Event{
		Timestamp: time.Now().UTC(),
		Type:      "info",
		Message:   fmt.Sprintf("Bundle uninstallation completed: %s", bundleID),
	}
	if err := manager.AppendEvent(jobID, event); err != nil {
		e.logger.Error("failed to append event", "job_id", jobID, "error", err)
	}

	result := map[string]interface{}{
		"bundle_id": bundleID,
		"status":    "completed",
	}

	return result, nil
}

// Ensure JobExecutor implements Executor interface
var _ Executor = (*JobExecutor)(nil)
