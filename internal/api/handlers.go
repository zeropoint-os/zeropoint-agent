package api

import (
	"zeropoint-agent/internal/modules"
)

// Type aliases for cleaner code
type (
	Module           = modules.Module
	Installer        = modules.Installer
	Uninstaller      = modules.Uninstaller
	InstallRequest   = modules.InstallRequest
	UninstallRequest = modules.UninstallRequest
	ProgressUpdate   = modules.ProgressUpdate
)
