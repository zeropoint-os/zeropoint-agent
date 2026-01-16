package boot

import (
	"log/slog"
	"os"
	"strings"
	"sync"
	"time"
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
	}

	// Load persistent markers from disk
	m.loadPersistentMarkers()

	return m
}

// loadPersistentMarkers checks for marker files to determine already-completed services
func (m *BootMonitor) loadPersistentMarkers() {
	// Check if boot already completed
	if _, err := os.Stat(m.markerDir + "/.boot-complete"); err == nil {
		m.isComplete = true
		m.logger.Info("boot already completed (marker file found)")
		return
	}

	m.logger.Info("boot process monitoring started")
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

// broadcast sends an update to all subscribers
func (m *BootMonitor) broadcast(update StatusUpdate) {
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
			// Drop if buffer full (subscriber not reading fast enough)
		}
	}
}

// updateServiceStatus updates or creates a service status based on a log entry
func (m *BootMonitor) updateServiceStatus(entry LogEntry) {
	m.mu.Lock()
	defer m.mu.Unlock()

	// Add to log history
	m.allLogs = append(m.allLogs, entry)

	var updates []StatusUpdate

	// Initialize service if not exists
	if _, ok := m.services[entry.Service]; !ok {
		m.services[entry.Service] = &ServiceStatus{
			Name:  entry.Service,
			State: StatePending,
			Steps: []string{},
		}

		// Add service name as a phase (if not already present)
		found := false
		for _, phase := range m.phaseOrder {
			if phase == entry.Service {
				found = true
				break
			}
		}
		if !found {
			m.phaseOrder = append(m.phaseOrder, entry.Service)
			m.phases[entry.Service] = &PhaseStatus{
				Name:     entry.Service,
				State:    StatePending,
				Services: []ServiceStatus{},
			}
		}
	}

	svc := m.services[entry.Service]

	// If marker, add to steps
	if entry.IsMarker {
		svc.Steps = append(svc.Steps, entry.Step)
		svc.CurrentStep = entry.Step

		// Check for boot completion marker
		if entry.Step == "boot-complete" {
			m.isComplete = true
			now := time.Now()
			m.completedAt = &now
			m.logger.Info("boot process completed")
		}

		// Queue service update broadcast
		updates = append(updates, StatusUpdate{
			Type: "service_update",
			Data: svc,
		})
	}

	// Detect error messages
	if strings.Contains(entry.Message, "error") || strings.Contains(entry.Message, "failed") || entry.Level == "error" {
		if svc.State != StateCompleted {
			svc.State = StateFailed
			svc.Error = entry.Message
			m.failedServices[entry.Service] = entry.Message
			m.isBootFailed = true

			m.logger.Error("boot service failed", "service", entry.Service, "error", entry.Message)
			updates = append(updates, StatusUpdate{
				Type: "service_failed",
				Data: map[string]interface{}{
					"service": entry.Service,
					"error":   entry.Message,
				},
			})
		}
	}

	// Detect state transitions from messages
	if entry.IsMarker && entry.Step == "started" {
		if svc.State == StatePending {
			svc.State = StateRunning
			now := time.Now()
			svc.StartedAt = &now
		}
	}

	// Queue current status broadcast after any change
	updates = append(updates, StatusUpdate{
		Type: "status_update",
		Data: m.getStatusSnapshot(),
	})

	// Unlock before broadcasting
	m.mu.Unlock()

	// Now broadcast all updates after unlocking
	for _, update := range updates {
		m.broadcast(update)
	}

	// Re-lock for defer unlock
	m.mu.Lock()
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
		m.broadcast(StatusUpdate{
			Type: "service_update",
			Data: svc,
		})
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
	if err := os.WriteFile(m.markerDir+"/.boot-complete", []byte(now.Format(time.RFC3339)), 0644); err != nil {
		m.logger.Warn("failed to write boot-complete marker", "error", err)
	}

	m.broadcast(StatusUpdate{
		Type: "boot_complete",
		Data: m.getStatusSnapshot(),
	})
}

// SetNeedsReboot marks that a reboot is needed
func (m *BootMonitor) SetNeedsReboot(needsReboot bool) {
	m.mu.Lock()
	defer m.mu.Unlock()

	m.needsReboot = needsReboot
	m.broadcast(StatusUpdate{
		Type: "needs_reboot",
		Data: needsReboot,
	})
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
