package queue

// EnqueueBundleInstallRequest is the request for creating a bundle installation meta-job.
// The frontend sends only the bundle name; the backend will be extended to automatically
// fetch the bundle definition and enqueue all component jobs. The DependsOn field allows
// chaining multiple bundle installations (e.g., for specialized sequential installs).
type EnqueueBundleInstallRequest struct {
	BundleName string   `json:"bundle_name"`
	DependsOn  []string `json:"depends_on,omitempty"` // For chaining multiple bundle installations
}

// EnqueueBundleUninstallRequest is the request for creating a bundle uninstallation meta-job.
type EnqueueBundleUninstallRequest struct {
	BundleID string `json:"bundle_id"`
}
