package boot

import (
	"bufio"
	"os"
	"strings"
	"time"
)

// StreamBootLog reads boot logs from the FIFO file in a loop,
// handling multiple writers opening and closing the FIFO
func (m *BootMonitor) StreamBootLog(logFile string) error {
	for {
		// Before attempting to open FIFO, reload persistent markers.
		// This ensures we pick up markers from any boot services that
		// completed before we started listening (handles mid-boot connections).
		m.loadPersistentMarkers()

		// Check if boot is already complete via persistent markers.
		// If so, don't bother trying to open the FIFO (it may not exist).
		m.mu.RLock()
		isComplete := m.isComplete
		m.mu.RUnlock()

		if isComplete {
			// Boot already marked as complete via persistent markers.
			// Just listen for changes to marker files and wait for potential reboot.
			time.Sleep(5 * time.Second)
			continue
		}

		// Open FIFO in blocking mode (without O_NONBLOCK).
		// This will block if no writers are connected yet.
		file, err := os.OpenFile(logFile, os.O_RDONLY, 0)
		if err != nil {
			// If the file doesn't exist, this can indicate the system has
			// rebooted and the FIFO/marker files were removed. Check if markers
			// exist first - if they do, reload them. If not, clear state.
			if os.IsNotExist(err) {
				m.logger.Info("log file missing; checking for persistent markers")
				m.ResetState()
			} else {
				m.logger.Debug("error opening FIFO, retrying", "error", err)
			}
			time.Sleep(100 * time.Millisecond)
			continue
		}

		// Read lines from FIFO until all writers close (EOF).
		// New data will block the scanner if no writers are active.
		scanner := bufio.NewScanner(file)
		for scanner.Scan() {
			line := scanner.Text()

			if entry := m.parseBootLogLine(line); entry != nil {
				// Only log markers (important events)
				if entry.IsMarker {
					m.logger.Info("boot step completed", "service", entry.Service, "step", entry.Step)
				}
				m.updateServiceStatus(*entry)
			}
		}

		if err := scanner.Err(); err != nil {
			m.logger.Warn("scanner error", "error", err)
		}

		file.Close()

		// All writers have closed the FIFO. Wait a moment before reopening
		// to listen for new writers.
		time.Sleep(100 * time.Millisecond)
	}
}

// parseBootLogLine parses a line from boot log in format: "[timestamp] SERVICE: [priority] message"
func (m *BootMonitor) parseBootLogLine(line string) *LogEntry {
	line = strings.TrimSpace(line)
	if line == "" {
		return nil
	}

	// Find the first bracket pair for timestamp
	openBracket := strings.Index(line, "[")
	closeBracket := strings.Index(line, "]")
	if openBracket < 0 || closeBracket < 0 || closeBracket < openBracket {
		return nil
	}

	// Get the rest after timestamp
	rest := line[closeBracket+1:]
	rest = strings.TrimSpace(rest)

	// Split on colon to get service and message
	parts := strings.SplitN(rest, ":", 2)
	if len(parts) < 2 {
		return nil
	}

	service := strings.TrimSpace(parts[0])
	message := strings.TrimSpace(parts[1])

	// Extract priority level from [notice], [warn], [err] format
	// Format: "[notice] step-name" or "[warn] step-name" or "[err] step-name"
	status := "notice" // default
	step := message

	if strings.HasPrefix(message, "[") {
		closeBracketIdx := strings.Index(message, "]")
		if closeBracketIdx > 0 {
			priority := message[1:closeBracketIdx]
			// Normalize priorities
			switch priority {
			case "notice":
				status = "notice"
			case "warn":
				status = "warn"
			case "err":
				status = "error"
			default:
				status = "notice"
			}
			// Extract step name (after the priority bracket)
			step = strings.TrimSpace(message[closeBracketIdx+1:])
		}
	}

	isMarker := step != ""

	return &LogEntry{
		Timestamp: time.Now(),
		Service:   service,
		Message:   message,
		Level:     status,
		IsMarker:  isMarker,
		Step:      step,
	}
}
