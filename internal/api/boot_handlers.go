package api

import (
	"encoding/json"
	"net/http"
	"strconv"

	"zeropoint-agent/internal/boot"

	"github.com/gorilla/websocket"
)

// BootHandlers handles boot monitoring API endpoints
type BootHandlers struct {
	monitor *boot.BootMonitor
}

// NewBootHandlers creates a new boot handlers instance
func NewBootHandlers(monitor *boot.BootMonitor) *BootHandlers {
	return &BootHandlers{
		monitor: monitor,
	}
}

// HandleBootStatus serves GET /api/boot/status
// Returns current boot status as JSON
func (h *BootHandlers) HandleBootStatus(w http.ResponseWriter, r *http.Request) {
	status := h.monitor.GetStatus()
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(status)
}

// HandleBootLogs serves GET /api/boot/logs
// Query params:
//
//	service=<name>  - filter by service name (optional)
//	level=<level>   - filter by level: info, warn, error (optional)
//	limit=<n>       - max entries to return (default 100)
//	offset=<n>      - offset into log list (default 0)
func (h *BootHandlers) HandleBootLogs(w http.ResponseWriter, r *http.Request) {
	service := r.URL.Query().Get("service")
	level := r.URL.Query().Get("level")
	limit := 100
	offset := 0

	if l := r.URL.Query().Get("limit"); l != "" {
		if parsed, err := strconv.Atoi(l); err == nil && parsed > 0 && parsed <= 1000 {
			limit = parsed
		}
	}

	if o := r.URL.Query().Get("offset"); o != "" {
		if parsed, err := strconv.Atoi(o); err == nil && parsed >= 0 {
			offset = parsed
		}
	}

	// Get logs by service
	var logs []boot.LogEntry
	if service != "" {
		logs = h.monitor.GetLogsByService(service)
	} else if level != "" {
		logs = h.monitor.GetLogsByLevel(level)
	} else {
		logs = h.monitor.GetLogsByService("")
	}

	// Apply offset and limit
	if offset > len(logs) {
		offset = len(logs)
	}
	end := offset + limit
	if end > len(logs) {
		end = len(logs)
	}

	if offset < len(logs) {
		logs = logs[offset:end]
	} else {
		logs = []boot.LogEntry{}
	}

	response := map[string]interface{}{
		"service": service,
		"level":   level,
		"offset":  offset,
		"limit":   limit,
		"logs":    logs,
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(response)
}

// HandleBootStream serves WS /api/boot/stream
// Streams boot status updates in real-time
var upgrader = websocket.Upgrader{
	ReadBufferSize:  1024,
	WriteBufferSize: 1024,
	CheckOrigin: func(r *http.Request) bool {
		// Allow local connections only for now
		return true
	},
}

func (h *BootHandlers) HandleBootStream(w http.ResponseWriter, r *http.Request) {
	conn, err := upgrader.Upgrade(w, r, nil)
	if err != nil {
		http.Error(w, "failed to upgrade connection", http.StatusBadRequest)
		return
	}
	defer conn.Close()

	// Send current status immediately
	status := h.monitor.GetStatus()
	if err := conn.WriteJSON(status); err != nil {
		return
	}

	// Subscribe to updates
	updates := h.monitor.Subscribe()
	defer func() {
		// TODO: unsubscribe from monitor
	}()

	// Stream updates to client
	for update := range updates {
		if err := conn.WriteJSON(update); err != nil {
			return
		}
	}
}
