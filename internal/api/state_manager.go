package api

import (
	"fmt"
	"io"
	"os"
	"path/filepath"
	"time"
)

// StateManager handles Terraform state backup and restoration
type StateManager struct {
	appsDir string
}

// NewStateManager creates a new state manager
func NewStateManager(appsDir string) *StateManager {
	return &StateManager{
		appsDir: appsDir,
	}
}

// StateBackup represents a backup of Terraform states
type StateBackup struct {
	timestamp string
	backups   map[string]string // app -> backup file path
}

// BackupStates creates backups of Terraform state files for the given apps
func (sm *StateManager) BackupStates(apps []string) (*StateBackup, error) {
	timestamp := time.Now().Format("20060102-150405")
	backup := &StateBackup{
		timestamp: timestamp,
		backups:   make(map[string]string),
	}

	for _, appName := range apps {
		appDir := filepath.Join(sm.appsDir, appName)
		stateFile := filepath.Join(appDir, "terraform.tfstate")

		// Check if state file exists
		if _, err := os.Stat(stateFile); os.IsNotExist(err) {
			// No state file exists, nothing to backup for this app
			continue
		}

		// Create backup file path
		backupFile := filepath.Join(appDir, fmt.Sprintf("terraform.tfstate.backup-%s", timestamp))

		// Copy state file to backup
		if err := copyFile(stateFile, backupFile); err != nil {
			// Clean up any backups we've already created
			sm.cleanupBackup(backup)
			return nil, fmt.Errorf("failed to backup state for app %s: %w", appName, err)
		}

		backup.backups[appName] = backupFile
	}

	return backup, nil
}

// RestoreStates restores Terraform state files from backup
func (sm *StateManager) RestoreStates(backup *StateBackup) error {
	var errors []string

	for appName, backupFile := range backup.backups {
		appDir := filepath.Join(sm.appsDir, appName)
		stateFile := filepath.Join(appDir, "terraform.tfstate")

		// Restore the backup
		if err := copyFile(backupFile, stateFile); err != nil {
			errors = append(errors, fmt.Sprintf("app %s: %v", appName, err))
		}
	}

	if len(errors) > 0 {
		return fmt.Errorf("failed to restore states: %v", errors)
	}

	return nil
}

// CleanupBackup removes backup files
func (sm *StateManager) CleanupBackup(backup *StateBackup) error {
	return sm.cleanupBackup(backup)
}

// cleanupBackup removes backup files (internal helper)
func (sm *StateManager) cleanupBackup(backup *StateBackup) error {
	var errors []string

	for appName, backupFile := range backup.backups {
		if err := os.Remove(backupFile); err != nil && !os.IsNotExist(err) {
			errors = append(errors, fmt.Sprintf("app %s: %v", appName, err))
		}
	}

	if len(errors) > 0 {
		return fmt.Errorf("failed to cleanup backups: %v", errors)
	}

	return nil
}

// copyFile copies a file from src to dst
func copyFile(src, dst string) error {
	sourceFile, err := os.Open(src)
	if err != nil {
		return err
	}
	defer sourceFile.Close()

	destFile, err := os.Create(dst)
	if err != nil {
		return err
	}
	defer destFile.Close()

	_, err = io.Copy(destFile, sourceFile)
	if err != nil {
		return err
	}

	// Sync to ensure data is written
	return destFile.Sync()
}
