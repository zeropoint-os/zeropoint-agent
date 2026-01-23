package api

import (
	"encoding/json"
	"net/http"
	"strconv"
	"strings"

	"zeropoint-agent/internal/boot"

	"github.com/gorilla/mux"
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
// Returns an ordered array of service marker lists in the order observed.
//
// @ID getBootStatus
// @Summary Get boot service markers
// @Description Returns an ordered array of services each with an array of MarkerEntry seen so far for that service
// @Tags boot
// @Produce json
// @Success 200 {array} boot.ServiceMarkers "Ordered list of services with markers"
// @Router /api/boot/status [get]
func (h *BootHandlers) HandleBootStatus(w http.ResponseWriter, r *http.Request) {
	// Return ordered slice: [{service, markers}, ...]
	markers := h.monitor.GetServiceStatuses()
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(markers)
}

// HandleBootService serves GET /api/boot/status/{service}
// Returns marker history for a single service as an array of MarkerEntry
// @ID getBootService
// @Summary Get service marker history
// @Description Returns markers seen so far for a specific service
// @Tags boot
// @Produce json
// @Param service path string true "Service name"
// @Success 200 {array} boot.MarkerEntry
// @Router /api/boot/status/{service} [get]
func (h *BootHandlers) HandleBootService(w http.ResponseWriter, r *http.Request) {
	// Extract service from URL path using mux vars if present
	service := ""
	if vars := mux.Vars(r); vars != nil {
		service = vars["service"]
	}
	if service == "" {
		// Fallback: parse path directly
		// path: /api/boot/status/{service}
		parts := strings.Split(strings.TrimPrefix(r.URL.Path, "/api/boot/status/"), "/")
		if len(parts) > 0 {
			service = parts[0]
		}
	}

	markers, ok := h.monitor.GetServiceStatus(service)
	if !ok {
		markers = []boot.MarkerEntry{}
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(markers)
}

// HandleBootMarker serves GET /api/boot/status/{service}/{marker}
// Returns a single MarkerEntry for the given service and marker name, or an empty object if not seen
// @ID getBootMarker
// @Summary Get single marker for a service
// @Description Returns the last-seen marker entry for a given service and marker name
// @Tags boot
// @Produce json
// @Param service path string true "Service name"
// @Param marker path string true "Marker name"
// @Success 200 {object} boot.MarkerEntry
// @Router /api/boot/status/{service}/{marker} [get]
func (h *BootHandlers) HandleBootMarker(w http.ResponseWriter, r *http.Request) {
	service := ""
	marker := ""
	if vars := mux.Vars(r); vars != nil {
		service = vars["service"]
		marker = vars["marker"]
	}
	if service == "" || marker == "" {
		// Fallback: parse path
		parts := strings.Split(strings.TrimPrefix(r.URL.Path, "/api/boot/status/"), "/")
		if len(parts) > 0 {
			service = parts[0]
		}
		if len(parts) > 1 {
			marker = parts[1]
		}
	}

	if service == "" || marker == "" {
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]string{"error": "service and marker required"})
		return
	}

	me, ok := h.monitor.GetMarker(service, marker)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	if !ok {
		json.NewEncoder(w).Encode(map[string]interface{}{})
		return
	}
	json.NewEncoder(w).Encode(me)
}

// HandleBootLogs serves GET /api/boot/logs
// Query params:
//
//	service=<name>  - filter by service name (optional)
//	level=<level>   - filter by level: info, warn, error (optional)
//	limit=<n>       - max entries to return (default 100)
//	offset=<n>      - offset into log list (default 0)
//
// @ID getBootLogs
// @Summary Get boot logs
// @Description Returns boot process logs with optional filtering by service or level
// @Tags boot
// @Produce json
// @Param service query string false "Filter by service name"
// @Param level query string false "Filter by level (info, warn, error)"
// @Param limit query int false "Maximum entries to return (default 100, max 1000)"
// @Param offset query int false "Offset into log list (default 0)"
// @Success 200 {object} map[string]interface{} "Boot logs response with service, level, offset, limit, and logs array"
// @Router /api/boot/logs [get]
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
//
// @ID streamBootUpdates
// @Summary Stream boot updates
// @Description WebSocket endpoint that streams real-time boot status updates and log entries
// @Tags boot
// @Success 101 "Switching Protocols"
// @Router /api/boot/stream [get]
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

	// Send current status immediately as a status_update
	status := h.monitor.GetStatus()
	initialUpdate := boot.StatusUpdate{
		Type: "status_update",
		Data: status,
	}
	if err := conn.WriteJSON(initialUpdate); err != nil {
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
