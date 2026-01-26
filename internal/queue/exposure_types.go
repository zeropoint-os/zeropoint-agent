package queue

// EnqueueCreateExposureRequest is the request for enqueueing a create exposure job
type EnqueueCreateExposureRequest struct {
	ExposureID    string   `json:"exposure_id"`
	ModuleID      string   `json:"module_id"`
	Protocol      string   `json:"protocol"`
	Hostname      string   `json:"hostname,omitempty"`
	ContainerPort uint32   `json:"container_port"`
	Tags          []string `json:"tags,omitempty"`
	DependsOn     []string `json:"depends_on,omitempty"`
}

// EnqueueDeleteExposureRequest is the request for enqueueing a delete exposure job
type EnqueueDeleteExposureRequest struct {
	ExposureID string   `json:"exposure_id"`
	Tags       []string `json:"tags,omitempty" example:"local-ai-chat"`
	DependsOn  []string `json:"depends_on,omitempty" example:"job-1,job-2"`
}
