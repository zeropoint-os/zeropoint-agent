package queue

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
)

// JobExecutor executes queued commands using the existing API handlers
type JobExecutor struct {
	router http.Handler
	logger *slog.Logger
}

// NewJobExecutor creates a new job executor
// It wraps the API router to execute commands from the job queue
func NewJobExecutor(router http.Handler, logger *slog.Logger) *JobExecutor {
	return &JobExecutor{
		router: router,
		logger: logger,
	}
}

// Execute runs a command and returns the result
func (e *JobExecutor) Execute(ctx context.Context, cmd Command) (interface{}, error) {
	switch cmd.Type {
	case CmdInstallModule:
		return e.executeInstallModule(ctx, cmd)
	case CmdUninstallModule:
		return e.executeUninstallModule(ctx, cmd)
	case CmdCreateExposure:
		return e.executeCreateExposure(ctx, cmd)
	case CmdDeleteExposure:
		return e.executeDeleteExposure(ctx, cmd)
	case CmdCreateLink:
		return e.executeCreateLink(ctx, cmd)
	case CmdDeleteLink:
		return e.executeDeleteLink(ctx, cmd)
	default:
		return nil, fmt.Errorf("unknown command type: %s", cmd.Type)
	}
}

// executeInstallModule runs an install_module command
func (e *JobExecutor) executeInstallModule(ctx context.Context, cmd Command) (interface{}, error) {
	moduleID, ok := cmd.Args["module_id"].(string)
	if !ok || moduleID == "" {
		return nil, fmt.Errorf("module_id is required")
	}

	source, _ := cmd.Args["source"].(string)
	localPath, _ := cmd.Args["local_path"].(string)

	if source == "" && localPath == "" {
		return nil, fmt.Errorf("either source or local_path is required")
	}

	// Create a request body that matches the existing handler expectations
	reqBody := fmt.Sprintf(`{"source":"%s","local_path":"%s"}`, escapeJSON(source), escapeJSON(localPath))

	// Create an HTTP request to the existing endpoint
	req := httptest.NewRequest(http.MethodPost, fmt.Sprintf("/api/modules/%s", moduleID), strings.NewReader(reqBody))
	req.Header.Set("Content-Type", "application/json")
	req = req.WithContext(ctx)

	// Record the response
	w := httptest.NewRecorder()
	e.router.ServeHTTP(w, req)

	if w.Code >= 400 {
		return nil, fmt.Errorf("install failed: status %d: %s", w.Code, w.Body.String())
	}

	// Parse response and extract result
	result := map[string]interface{}{
		"module_id": moduleID,
		"status":    "installed",
	}

	return result, nil
}

// executeUninstallModule runs an uninstall_module command
func (e *JobExecutor) executeUninstallModule(ctx context.Context, cmd Command) (interface{}, error) {
	moduleID, ok := cmd.Args["module_id"].(string)
	if !ok || moduleID == "" {
		return nil, fmt.Errorf("module_id is required")
	}

	// Create an HTTP request to the existing endpoint
	req := httptest.NewRequest(http.MethodDelete, fmt.Sprintf("/api/modules/%s", moduleID), nil)
	req = req.WithContext(ctx)

	// Record the response
	w := httptest.NewRecorder()
	e.router.ServeHTTP(w, req)

	if w.Code >= 400 {
		return nil, fmt.Errorf("uninstall failed: status %d: %s", w.Code, w.Body.String())
	}

	result := map[string]interface{}{
		"module_id": moduleID,
		"status":    "uninstalled",
	}

	return result, nil
}

// executeCreateExposure runs a create_exposure command
func (e *JobExecutor) executeCreateExposure(ctx context.Context, cmd Command) (interface{}, error) {
	exposureID, ok := cmd.Args["exposure_id"].(string)
	if !ok || exposureID == "" {
		return nil, fmt.Errorf("exposure_id is required")
	}

	moduleID, _ := cmd.Args["module_id"].(string)
	protocol, _ := cmd.Args["protocol"].(string)
	hostname, _ := cmd.Args["hostname"].(string)
	containerPortVal, _ := cmd.Args["container_port"]
	tagsVal, _ := cmd.Args["tags"]

	var containerPort uint32
	switch v := containerPortVal.(type) {
	case float64:
		containerPort = uint32(v)
	case uint32:
		containerPort = v
	}

	// Build tags array
	tagsJSON := "[]"
	if tags, ok := tagsVal.([]string); ok && len(tags) > 0 {
		tagsJSON = fmt.Sprintf("[%s]", strings.Join(wrapStrings(tags), ","))
	}

	// Create request body
	reqBody := fmt.Sprintf(`{"module_id":"%s","protocol":"%s","hostname":"%s","container_port":%d,"tags":%s}`,
		escapeJSON(moduleID), escapeJSON(protocol), escapeJSON(hostname), containerPort, tagsJSON)

	req := httptest.NewRequest(http.MethodPost, fmt.Sprintf("/api/exposures/%s", exposureID), strings.NewReader(reqBody))
	req.Header.Set("Content-Type", "application/json")
	req = req.WithContext(ctx)

	w := httptest.NewRecorder()
	e.router.ServeHTTP(w, req)

	if w.Code >= 400 {
		return nil, fmt.Errorf("create exposure failed: status %d: %s", w.Code, w.Body.String())
	}

	result := map[string]interface{}{
		"exposure_id": exposureID,
		"status":      "created",
	}

	return result, nil
}

// executeDeleteExposure runs a delete_exposure command
func (e *JobExecutor) executeDeleteExposure(ctx context.Context, cmd Command) (interface{}, error) {
	exposureID, ok := cmd.Args["exposure_id"].(string)
	if !ok || exposureID == "" {
		return nil, fmt.Errorf("exposure_id is required")
	}

	req := httptest.NewRequest(http.MethodDelete, fmt.Sprintf("/api/exposures/%s", exposureID), nil)
	req = req.WithContext(ctx)

	w := httptest.NewRecorder()
	e.router.ServeHTTP(w, req)

	if w.Code >= 400 {
		return nil, fmt.Errorf("delete exposure failed: status %d: %s", w.Code, w.Body.String())
	}

	result := map[string]interface{}{
		"exposure_id": exposureID,
		"status":      "deleted",
	}

	return result, nil
}

// executeCreateLink runs a create_link command
func (e *JobExecutor) executeCreateLink(ctx context.Context, cmd Command) (interface{}, error) {
	linkID, ok := cmd.Args["link_id"].(string)
	if !ok || linkID == "" {
		return nil, fmt.Errorf("link_id is required")
	}

	modules, ok := cmd.Args["modules"].(map[string]interface{})
	if !ok || len(modules) == 0 {
		return nil, fmt.Errorf("modules is required")
	}

	// Build the request body with the modules structure
	modulesJSON, err := json.Marshal(modules)
	if err != nil {
		return nil, fmt.Errorf("failed to marshal modules: %w", err)
	}

	reqBody := fmt.Sprintf(`{"modules":%s}`, modulesJSON)

	// Add tags if provided
	if tags, ok := cmd.Args["tags"].([]interface{}); ok && len(tags) > 0 {
		tagsJSON, _ := json.Marshal(tags)
		reqBody = fmt.Sprintf(`{"modules":%s,"tags":%s}`, modulesJSON, tagsJSON)
	}

	req := httptest.NewRequest(http.MethodPost, fmt.Sprintf("/api/links/%s", linkID), strings.NewReader(reqBody))
	req.Header.Set("Content-Type", "application/json")
	req = req.WithContext(ctx)

	w := httptest.NewRecorder()
	e.router.ServeHTTP(w, req)

	if w.Code >= 400 {
		return nil, fmt.Errorf("create link failed: status %d: %s", w.Code, w.Body.String())
	}

	result := map[string]interface{}{
		"link_id": linkID,
		"status":  "created",
	}

	return result, nil
}

// executeDeleteLink runs a delete_link command
func (e *JobExecutor) executeDeleteLink(ctx context.Context, cmd Command) (interface{}, error) {
	linkID, ok := cmd.Args["link_id"].(string)
	if !ok || linkID == "" {
		return nil, fmt.Errorf("link_id is required")
	}

	req := httptest.NewRequest(http.MethodDelete, fmt.Sprintf("/api/links/%s", linkID), nil)
	req = req.WithContext(ctx)

	w := httptest.NewRecorder()
	e.router.ServeHTTP(w, req)

	if w.Code >= 400 {
		return nil, fmt.Errorf("delete link failed: status %d: %s", w.Code, w.Body.String())
	}

	result := map[string]interface{}{
		"link_id": linkID,
		"status":  "deleted",
	}

	return result, nil
}

// Helper functions for JSON escaping
func escapeJSON(s string) string {
	s = strings.ReplaceAll(s, "\\", "\\\\")
	s = strings.ReplaceAll(s, "\"", "\\\"")
	s = strings.ReplaceAll(s, "\n", "\\n")
	s = strings.ReplaceAll(s, "\r", "\\r")
	s = strings.ReplaceAll(s, "\t", "\\t")
	return s
}

func wrapStrings(strs []string) []string {
	wrapped := make([]string, len(strs))
	for i, s := range strs {
		wrapped[i] = fmt.Sprintf("\"%s\"", escapeJSON(s))
	}
	return wrapped
}

// Ensure JobExecutor implements Executor interface
var _ Executor = (*JobExecutor)(nil)
