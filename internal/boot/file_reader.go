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
		// Open FIFO in blocking mode (without O_NONBLOCK).
		// This will block if no writers are connected yet.
		file, err := os.OpenFile(logFile, os.O_RDONLY, 0)
		if err != nil {
			// If the file doesn't exist, this can indicate the system has
			// rebooted and the FIFO/marker files were removed. Clear in-
			// memory state so we don't keep stale markers.
			if os.IsNotExist(err) {
				m.logger.Info("log file missing; clearing in-memory state")
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

// parseBootLogLine parses a line from boot log in format: "[timestamp] SERVICE: message"
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

	// Check if this is a marker message (✓ ... or ✗ ...)
	isMarker := strings.HasPrefix(message, "✓ ") || strings.HasPrefix(message, "✗ ")
	step := ""
	if isMarker {
		if strings.HasPrefix(message, "✓ ") {
			step = strings.TrimPrefix(message, "✓ ")
		} else {
			step = strings.TrimPrefix(message, "✗ ")
		}
	}

	return &LogEntry{
		Timestamp: time.Now(),
		Service:   service,
		Message:   message,
		Level:     "info",
		IsMarker:  isMarker,
		Step:      step,
	}
}
