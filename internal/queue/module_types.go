package queue

// EnqueueInstallRequest is the request for enqueueing an install job
type EnqueueInstallRequest struct {
	ModuleID  string   `json:"module_id"`
	Source    string   `json:"source,omitempty"`
	LocalPath string   `json:"local_path,omitempty"`
	Tags      []string `json:"tags,omitempty"`
	DependsOn []string `json:"depends_on,omitempty"`
}

// EnqueueUninstallRequest is the request for enqueueing an uninstall job
type EnqueueUninstallRequest struct {
	ModuleID  string   `json:"module_id"`
	Tags      []string `json:"tags,omitempty" example:"local-ai-chat"`
	DependsOn []string `json:"depends_on,omitempty" example:"job-1,job-2"`
}
