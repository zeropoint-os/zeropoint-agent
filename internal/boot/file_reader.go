package boot

import (
	"bufio"
	"os"
	"strings"
	"syscall"
	"time"
)

// StreamBootLog reads boot logs from the FIFO file in a loop,
// handling multiple writers opening and closing the FIFO
func (m *BootMonitor) StreamBootLog(logFile string) error {
	for {
		// Open FIFO with O_NONBLOCK - won't block if no writer yet
		file, err := os.OpenFile(logFile, os.O_RDONLY|syscall.O_NONBLOCK, 0)
		if err != nil {
			m.logger.Warn("error opening FIFO, retrying", "error", err)
			time.Sleep(100 * time.Millisecond)
			continue
		}

		// Read lines from FIFO
		scanner := bufio.NewScanner(file)
		for scanner.Scan() {
			line := scanner.Text()

			if entry := m.parseBootLogLine(line); entry != nil {
				// Only log markers (important events)
				if entry.IsMarker {
					m.logger.Info("boot step completed", "service", entry.Service, "step", entry.Step)
				}
				m.updateServiceStatus(*entry)
				m.broadcast(StatusUpdate{
					Type: "log_entry",
					Data: entry,
				})
			}
		}

		if err := scanner.Err(); err != nil {
			m.logger.Warn("scanner error", "error", err)
		}

		file.Close()

		// Small delay before trying to reopen
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
