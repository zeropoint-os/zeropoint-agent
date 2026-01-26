package queue

// EnqueueCreateLinkRequest is the request for enqueueing a create link job
type EnqueueCreateLinkRequest struct {
	LinkID    string                            `json:"link_id"`
	Modules   map[string]map[string]interface{} `json:"modules,omitempty"`
	Tags      []string                          `json:"tags,omitempty"`
	DependsOn []string                          `json:"depends_on,omitempty"`
}

// EnqueueDeleteLinkRequest is the request for enqueueing a delete link job
type EnqueueDeleteLinkRequest struct {
	LinkID    string   `json:"link_id"`
	Tags      []string `json:"tags,omitempty" example:"local-ai-chat"`
	DependsOn []string `json:"depends_on,omitempty" example:"job-1,job-2"`
}
