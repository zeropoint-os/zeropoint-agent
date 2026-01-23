package boot

import "time"

// BootPhase represents a phase in the boot process
type BootPhase string

const (
	PhaseBase      BootPhase = "base"
	PhaseStorage   BootPhase = "storage"
	PhaseUtilities BootPhase = "utilities"
	PhaseDrivers   BootPhase = "drivers"
)

// ServiceState represents the state of a single service
type ServiceState string

const (
	StatePending   ServiceState = "pending"
	StateRunning   ServiceState = "running"
	StateCompleted ServiceState = "completed"
	StateFailed    ServiceState = "failed"
	StateRebooting ServiceState = "rebooting"
)

// LogEntry represents a single log line from a boot service
type LogEntry struct {
	Timestamp time.Time `json:"timestamp"`
	Service   string    `json:"service"`
	Message   string    `json:"message"`
	Level     string    `json:"level"` // info, warn, error
	IsMarker  bool      `json:"is_marker"`
	Step      string    `json:"step,omitempty"` // For marker messages
}

// ServiceStatus tracks the state of a single boot service
type ServiceStatus struct {
	Name        string       `json:"name"`
	Phase       string       `json:"phase"`
	State       ServiceState `json:"status"` // pending, running, completed, failed, rebooting
	Description string       `json:"description"`
	StartedAt   *time.Time   `json:"started_at,omitempty"`
	CompletedAt *time.Time   `json:"completed_at,omitempty"`
	Error       string       `json:"error,omitempty"`
	Warning     string       `json:"warning,omitempty"`
	Steps       []string     `json:"steps"` // Completed milestones
	CurrentStep string       `json:"current_step,omitempty"`
	NeedsReboot bool         `json:"needs_reboot"`
}

// PhaseStatus tracks the state of a phase
type PhaseStatus struct {
	Name        string          `json:"name"`
	Description string          `json:"description"`
	State       ServiceState    `json:"status"` // pending, running, completed, failed
	Services    []ServiceStatus `json:"services"`
	StartedAt   *time.Time      `json:"started_at,omitempty"`
	CompletedAt *time.Time      `json:"completed_at,omitempty"`
}

// BootStatus is the current state of the boot process
type BootStatus struct {
	IsComplete     bool              `json:"is_complete"`
	IsBootFailed   bool              `json:"is_boot_failed"`
	CurrentPhase   string            `json:"current_phase"`
	Phases         []PhaseStatus     `json:"phases"`
	Services       []ServiceStatus   `json:"services"`
	CompletedAt    *time.Time        `json:"completed_at,omitempty"`
	FailedServices map[string]string `json:"failed_services"` // service → error
	RecentLogs     []LogEntry        `json:"recent_logs"`     // Last 50
	NeedsReboot    bool              `json:"needs_reboot"`
}

// MarkerEntry represents a single marker in a service's progress
type MarkerEntry struct {
	Step      string    `json:"step"`
	Message   string    `json:"message"`
	Timestamp time.Time `json:"timestamp"`
	Status    string    `json:"status"` // notice, warn, error
}

// ServiceMarkers represents a service and its ordered list of markers
type ServiceMarkers struct {
	Service string        `json:"service"`
	Markers []MarkerEntry `json:"markers"`
}

// StatusUpdate is sent to subscribers when status changes
type StatusUpdate struct {
	Type string      `json:"type"` // "status_update", "log_entry", "service_failed", "phase_complete"
	Data interface{} `json:"data"`
}
