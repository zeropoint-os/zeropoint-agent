package boot

import (
	"log/slog"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	orderedmap "github.com/wk8/go-ordered-map/v2"
)

// BootMonitor tracks the boot process via FIFO-based log monitoring
type BootMonitor struct {
	mu               sync.RWMutex
	logger           *slog.Logger
	phases           map[string]*PhaseStatus   // keyed by phase name
	services         map[string]*ServiceStatus // keyed by service name
	phaseOrder       []string                  // order of phases discovered from logs
	allLogs          []LogEntry                // all captured logs
	isComplete       bool
	isBootFailed     bool
	completedAt      *time.Time
	failedServices   map[string]string // service → error message
	subscribers      map[int]chan StatusUpdate
	nextSubscriberID int
	startTime        time.Time
	needsReboot      bool
	markerDir        string
	markers          *orderedmap.OrderedMap[string, []MarkerEntry] // service name → ordered list of markers
}

// NewBootMonitor creates a new boot monitor
func NewBootMonitor(logger *slog.Logger) *BootMonitor {
	m := &BootMonitor{
		logger:         logger,
		phases:         make(map[string]*PhaseStatus),
		services:       make(map[string]*ServiceStatus),
		phaseOrder:     []string{}, // Will be built dynamically from journal
		allLogs:        make([]LogEntry, 0, 1000),
		failedServices: make(map[string]string),
		subscribers:    make(map[int]chan StatusUpdate),
		startTime:      time.Now(),
		markerDir:      "/etc/zeropoint",
		markers:        orderedmap.New[string, []MarkerEntry](),
	}

	// Load persistent markers from disk
	m.loadPersistentMarkers()

	return m
}

// ResetState clears in-memory boot state for a fresh boot (e.g., when the
// boot log FIFO or marker files are gone because the system rebooted).
// However, if persistent markers exist on disk, we reload them instead of
// fully clearing state.
func (m *BootMonitor) ResetState() {
	m.mu.Lock()

	// Check if persistent markers exist on disk
	hasMarkers := m.checkPersistentMarkersExist()

	if hasMarkers {
		// Markers exist on disk - reload them instead of clearing state
		m.logger.Info("persistent markers found on disk; reloading state instead of resetting")
		m.mu.Unlock()
		m.loadPersistentMarkers()
		return
	}

	// No markers on disk - full reset for new boot
	m.logger.Info("resetting in-memory boot state due to missing markers/log file")

	m.phases = make(map[string]*PhaseStatus)
	m.services = make(map[string]*ServiceStatus)
	m.phaseOrder = []string{}
	m.allLogs = make([]LogEntry, 0, 1000)
	m.isComplete = false
	m.isBootFailed = false
	m.completedAt = nil
	m.failedServices = make(map[string]string)
	m.needsReboot = false
	m.markerDir = m.markerDir // keep same markerDir
	m.markers = orderedmap.New[string, []MarkerEntry]()

	// Build a snapshot while still holding the lock (getStatusSnapshot assumes lock held)
	snapshot := m.getStatusSnapshot()
	m.mu.Unlock()

	// Broadcast cleared status to subscribers so UI updates immediately
	m.broadcast(snapshot)
}

// checkPersistentMarkersExist checks if any marker files exist in the marker directory
func (m *BootMonitor) checkPersistentMarkersExist() bool {
	entries, err := os.ReadDir(m.markerDir)
	if err != nil {
		return false // Directory doesn't exist or unreadable = no markers
	}

	for _, entry := range entries {
		if !entry.IsDir() && strings.HasPrefix(entry.Name(), ".zeropoint-") {
			return true // Found at least one marker file
		}
	}
	return false
}

// loadPersistentMarkers scans /etc/zeropoint for marker files and loads completed/failed services
func (m *BootMonitor) loadPersistentMarkers() {
	entries, err := os.ReadDir(m.markerDir)
	if err != nil {
		m.logger.Warn("failed to read marker directory", "error", err)
		return
	}

	now := time.Now()

	m.mu.Lock()
	defer m.mu.Unlock()

	for _, entry := range entries {
		if entry.IsDir() {
			continue
		}

		filename := entry.Name()
		if !strings.HasPrefix(filename, ".zeropoint-") {
			continue
		}

		// Parse marker type and service name
		// .zeropoint-setup-storage → service: zeropoint-setup-storage
		// .zeropoint-setup-storage.error → service: zeropoint-setup-storage (error)
		// .zeropoint-setup-storage.warning → service: zeropoint-setup-storage (warning)

		serviceName := strings.TrimPrefix(filename, ".")

		if strings.HasSuffix(serviceName, ".error") {
			// Error marker
			svcName := strings.TrimSuffix(serviceName, ".error")
			markerPath := filepath.Join(m.markerDir, filename)
			errorDetails := m.readMarkerFile(markerPath)

			m.services[svcName] = &ServiceStatus{
				Name:        svcName,
				Phase:       "boot",
				State:       "failed",
				StartedAt:   &now,
				CompletedAt: &now,
				Steps:       []string{"error"},
				Error:       errorDetails,
			}
			m.isBootFailed = true
			m.failedServices[svcName] = errorDetails

			m.logger.Info("loaded error marker", "service", svcName)

		} else if strings.HasSuffix(serviceName, ".warning") {
			// Warning marker
			svcName := strings.TrimSuffix(serviceName, ".warning")
			markerPath := filepath.Join(m.markerDir, filename)
			warningDetails := m.readMarkerFile(markerPath)

			// If service not yet loaded, create it
			if _, exists := m.services[svcName]; !exists {
				m.services[svcName] = &ServiceStatus{
					Name:        svcName,
					Phase:       "boot",
					State:       "completed",
					StartedAt:   &now,
					CompletedAt: &now,
					Steps:       []string{"warning"},
				}
			}

			// Add warning to existing service
			if svc, exists := m.services[svcName]; exists {
				svc.Warning = warningDetails
			}

			m.logger.Info("loaded warning marker", "service", svcName)

		} else {
			// Completion marker
			markerPath := filepath.Join(m.markerDir, filename)
			fileInfo, err := os.Stat(markerPath)
			if err != nil {
				continue
			}

			modTime := fileInfo.ModTime()

			m.services[serviceName] = &ServiceStatus{
				Name:        serviceName,
				Phase:       "boot",
				State:       "completed",
				StartedAt:   &modTime,
				CompletedAt: &modTime,
				Steps:       []string{serviceName},
			}

			// If this is boot-complete, mark boot as done
			if serviceName == "zeropoint-boot-complete" {
				m.isComplete = true
				m.completedAt = &modTime

				markerEntry := MarkerEntry{
					Step:      "boot-complete",
					Timestamp: modTime,
					Status:    "notice",
				}
				m.markers.Set("zeropoint-boot-complete", []MarkerEntry{markerEntry})

				m.logger.Info("boot completed (marker file found)")
			} else {
				m.logger.Info("loaded completion marker", "service", serviceName)
			}
		}
	}

	if m.isBootFailed {
		m.logger.Info("boot failed - errors detected in marker files", "failed_services", m.failedServices)
	}
}

// readMarkerFile reads the contents of a marker file
func (m *BootMonitor) readMarkerFile(path string) string {
	data, err := os.ReadFile(path)
	if err != nil {
		m.logger.Warn("failed to read marker file", "path", path, "error", err)
		return ""
	}
	return strings.TrimSpace(string(data))
}

// IsComplete returns whether boot has completed
func (m *BootMonitor) IsComplete() bool {
	m.mu.RLock()
	defer m.mu.RUnlock()
	return m.isComplete
}

// GetStatus returns the current boot status snapshot
func (m *BootMonitor) GetStatus() BootStatus {
	m.mu.RLock()
	defer m.mu.RUnlock()

	// Convert services map to sorted list
	services := make([]ServiceStatus, 0, len(m.services))
	for _, svc := range m.services {
		services = append(services, *svc)
	}

	// Convert phases to list in order
	phases := make([]PhaseStatus, 0, len(m.phaseOrder))
	for _, phaseName := range m.phaseOrder {
		if ps, ok := m.phases[string(phaseName)]; ok {
			phases = append(phases, *ps)
		}
	}

	// Last 50 logs
	recentLogs := m.allLogs
	if len(recentLogs) > 50 {
		recentLogs = recentLogs[len(recentLogs)-50:]
	}

	currentPhase := ""
	for _, phase := range m.phaseOrder {
		phaseStr := string(phase)
		if ps, ok := m.phases[phaseStr]; ok && ps.State != StateCompleted {
			currentPhase = phaseStr
			break
		}
	}

	return BootStatus{
		IsComplete:     m.isComplete,
		IsBootFailed:   m.isBootFailed,
		CurrentPhase:   currentPhase,
		Phases:         phases,
		Services:       services,
		CompletedAt:    m.completedAt,
		FailedServices: m.failedServices,
		RecentLogs:     recentLogs,
		NeedsReboot:    m.needsReboot,
	}
}

// Subscribe returns a channel that receives status updates
func (m *BootMonitor) Subscribe() <-chan StatusUpdate {
	m.mu.Lock()
	defer m.mu.Unlock()

	ch := make(chan StatusUpdate, 10)
	m.nextSubscriberID++
	m.subscribers[m.nextSubscriberID] = ch
	return ch
}

// broadcastUpdate sends a StatusUpdate to all subscribers
func (m *BootMonitor) broadcastUpdate(update StatusUpdate) {
	m.mu.Lock()
	subs := make(map[int]chan StatusUpdate)
	for k, v := range m.subscribers {
		subs[k] = v
	}
	m.mu.Unlock()

	for _, ch := range subs {
		select {
		case ch <- update:
		default:
			// Don't block if subscriber is slow
		}
	}
}

// broadcast sends the current full status to all subscribers
func (m *BootMonitor) broadcast(status BootStatus) {
	update := StatusUpdate{
		Type: "status_update",
		Data: status,
	}
	m.broadcastUpdate(update)
}

// updateServiceStatus broadcasts a log entry as a streaming update
func (m *BootMonitor) updateServiceStatus(entry LogEntry) {
	m.mu.Lock()
	m.allLogs = append(m.allLogs, entry)
	m.mu.Unlock()

	// Update marker tracker (only affects marker entries)
	m.updateMarkerTracker(entry)

	// Broadcast the log entry as a streaming update
	update := StatusUpdate{
		Type: "log_entry",
		Data: entry,
	}
	m.broadcastUpdate(update)
}

// getStatusSnapshot creates a snapshot without locking (assumes mu is held)
func (m *BootMonitor) getStatusSnapshot() BootStatus {
	// Convert services map to list
	services := make([]ServiceStatus, 0, len(m.services))
	for _, svc := range m.services {
		services = append(services, *svc)
	}

	// Convert phases to list
	phases := make([]PhaseStatus, 0, len(m.phaseOrder))
	for _, phaseName := range m.phaseOrder {
		if ps, ok := m.phases[string(phaseName)]; ok {
			phases = append(phases, *ps)
		}
	}

	// Last 50 logs
	recentLogs := m.allLogs
	if len(recentLogs) > 50 {
		recentLogs = recentLogs[len(recentLogs)-50:]
	}

	currentPhase := ""
	for _, phase := range m.phaseOrder {
		phaseStr := string(phase)
		if ps, ok := m.phases[phaseStr]; ok && ps.State != StateCompleted {
			currentPhase = phaseStr
			break
		}
	}

	return BootStatus{
		IsComplete:     m.isComplete,
		IsBootFailed:   m.isBootFailed,
		CurrentPhase:   currentPhase,
		Phases:         phases,
		Services:       services,
		CompletedAt:    m.completedAt,
		FailedServices: m.failedServices,
		RecentLogs:     recentLogs,
		NeedsReboot:    m.needsReboot,
	}
}

// RegisterService registers a service that the boot monitor should track
func (m *BootMonitor) RegisterService(name, phase, description string) {
	m.mu.Lock()
	defer m.mu.Unlock()

	m.services[name] = &ServiceStatus{
		Name:        name,
		Phase:       phase,
		State:       StatePending,
		Description: description,
		Steps:       []string{},
	}
}

// SetServiceState updates the state of a service
func (m *BootMonitor) SetServiceState(name string, state ServiceState) {
	m.mu.Lock()
	defer m.mu.Unlock()

	if svc, ok := m.services[name]; ok {
		svc.State = state
		if state == StateCompleted {
			now := time.Now()
			svc.CompletedAt = &now
		}
		m.mu.Unlock()
		m.broadcast(m.getStatusSnapshot())
		m.mu.Lock()
	}
}

// MarkBootComplete marks the boot process as complete
func (m *BootMonitor) MarkBootComplete() {
	m.mu.Lock()
	defer m.mu.Unlock()

	m.isComplete = true
	now := time.Now()
	m.completedAt = &now

	m.logger.Info("boot process completed")

	// Write marker file
	if err := os.WriteFile(m.markerDir+"/.zeropoint-boot-complete", []byte(now.Format(time.RFC3339)), 0644); err != nil {
		m.logger.Warn("failed to write boot-complete marker", "error", err)
	}

	m.mu.Unlock()
	m.broadcast(m.getStatusSnapshot())
	m.mu.Lock()
}

// SetNeedsReboot marks that a reboot is needed
func (m *BootMonitor) SetNeedsReboot(needsReboot bool) {
	m.mu.Lock()
	defer m.mu.Unlock()

	m.needsReboot = needsReboot
	m.mu.Unlock()
	m.broadcast(m.getStatusSnapshot())
	m.mu.Lock()
}

// GetLogsByService returns logs for a specific service
func (m *BootMonitor) GetLogsByService(service string) []LogEntry {
	m.mu.RLock()
	defer m.mu.RUnlock()

	var result []LogEntry
	for _, log := range m.allLogs {
		if service == "" || log.Service == service {
			result = append(result, log)
		}
	}
	return result
}

// GetLogsByLevel returns logs of a specific level
func (m *BootMonitor) GetLogsByLevel(level string) []LogEntry {
	m.mu.RLock()
	defer m.mu.RUnlock()

	var result []LogEntry
	for _, log := range m.allLogs {
		if log.Level == level {
			result = append(result, log)
		}
	}
	return result
}

// GetServiceStatuses returns an ordered slice of services and their marker
// histories in the order they were first observed.
func (m *BootMonitor) GetServiceStatuses() []ServiceMarkers {
	m.mu.RLock()
	defer m.mu.RUnlock()

	result := make([]ServiceMarkers, 0)
	for el := m.markers.Oldest(); el != nil; el = el.Next() {
		// Make a fresh copy of the markers slice so callers get a snapshot
		// that won't be aliased to the internal storage.
		src := el.Value
		copied := make([]MarkerEntry, len(src))
		copy(copied, src)
		result = append(result, ServiceMarkers{
			Service: el.Key,
			Markers: copied,
		})
	}
	return result
}

// GetServiceStatus returns markers for a specific service
func (m *BootMonitor) GetServiceStatus(serviceName string) ([]MarkerEntry, bool) {
	m.mu.RLock()
	defer m.mu.RUnlock()

	markers, ok := m.markers.Get(serviceName)
	return markers, ok
}

// GetMarker returns a specific marker for a service
func (m *BootMonitor) GetMarker(serviceName, step string) (MarkerEntry, bool) {
	m.mu.RLock()
	defer m.mu.RUnlock()

	markers, ok := m.markers.Get(serviceName)
	if !ok {
		return MarkerEntry{}, false
	}

	for _, marker := range markers {
		if marker.Step == step {
			return marker, true
		}
	}
	return MarkerEntry{}, false
}

// updateMarkerTracker updates the ordered marker map for a log entry
func (m *BootMonitor) updateMarkerTracker(entry LogEntry) {
	if !entry.IsMarker {
		return
	}

	m.mu.Lock()
	defer m.mu.Unlock()

	marker := MarkerEntry{
		Step:      entry.Step,
		Message:   entry.Message,
		Timestamp: entry.Timestamp,
		Status:    entry.Level, // notice, warn, error
	}

	// Get existing markers for this service or create new entry
	if markers, ok := m.markers.Get(entry.Service); ok {
		// Append to existing markers
		markers = append(markers, marker)
		m.markers.Set(entry.Service, markers)
	} else {
		// New service: add to ordered map
		m.markers.Set(entry.Service, []MarkerEntry{marker})
	}

	// If this is the boot-complete marker, mark boot as complete
	if entry.Step == "boot-complete" && entry.Service == "zeropoint-boot-complete" {
		m.isComplete = true
		now := time.Now()
		m.completedAt = &now
		m.logger.Info("boot process completed (boot-complete marker detected)")

		// Write marker file
		if err := os.WriteFile(m.markerDir+"/.zeropoint-boot-complete", []byte(now.Format(time.RFC3339)), 0644); err != nil {
			m.logger.Warn("failed to write boot-complete marker", "error", err)
		}
	}
}
