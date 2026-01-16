# Boot Monitor Testing Guide

This guide shows how to test the boot monitoring API and WebSocket streaming.

## Prerequisites

```bash
# Install wscat for WebSocket testing
npm install -g wscat

# Or use curl for REST testing
curl --version  # should already be available
```

## Testing the Boot Monitor

### Terminal 1: Start the Agent

```bash
./bin/zeropoint-agent
```

You should see output like:
```
{"time":"2026-01-16T...","level":"INFO","msg":"zeropoint-agent starting"}
{"time":"2026-01-16T...","level":"INFO","msg":"starting server","addr":":2370"}
{"time":"2026-01-16T...","level":"INFO","msg":"boot process monitoring started"}
```

### Terminal 2: Watch Boot Status (HTTP Polling)

```bash
# Get a single snapshot of boot status
curl http://localhost:2370/api/boot/status | jq

# Watch with live updates (polls every 2 seconds)
watch -n 2 'curl -s http://localhost:2370/api/boot/status | jq .is_complete,.current_phase,.services[].name'

# Get logs for a specific service
curl 'http://localhost:2370/api/boot/logs?service=setup-storage' | jq

# Get only error logs
curl 'http://localhost:2370/api/boot/logs?level=error' | jq
```

### Terminal 2 (Alternative): Watch Boot Status (WebSocket)

```bash
# Stream real-time updates via WebSocket
wscat -c ws://localhost:2370/api/boot/stream

# You'll see updates like:
# Connected (press ENTER to send a message)
# > 
# < {"is_complete":false,"is_boot_failed":false,"current_phase":"base",...}
# < {"type":"status_update","data":{...}}
# < {"type":"log_entry","data":{"timestamp":"2026-01-16T...","service":"set-memorable-hostname",...}}
```

### Terminal 3: Run Boot Simulation

```bash
./test-boot-sequence.sh
```

This script simulates the complete boot sequence with:
- All 4 phases (base → storage → utilities → drivers)
- Realistic messages for each service
- Progress markers (✓ step-name)
- Proper timing between events
- Simulated reboot delay between driver install and verification

## What to Observe

### In Terminal 1 (Agent Output)
You'll see debug logs as the journal is parsed:
```
{"level":"INFO","msg":"boot process monitoring started"}
{"level":"INFO","msg":"..."}
```

### In Terminal 2 (HTTP/WebSocket)
Initial status (before simulation):
```json
{
  "is_complete": false,
  "is_boot_failed": false,
  "current_phase": "base",
  "phases": [...],
  "services": [...],
  "recent_logs": [],
  "needs_reboot": false
}
```

As logs arrive:
- `recent_logs` populates with entries
- `current_phase` updates as phases progress
- Service `status` changes from "pending" → "running" → "completed"
- `is_complete` becomes true when boot-complete service finishes

### In Terminal 3 (Simulation)
Color-coded output showing:
```
[set-memorable-hostname] Generated hostname: zeropoint-dakara-peron
[set-memorable-hostname] ✓ hostname-generated
[setup-storage] Selected largest disk: /dev/sdb (500GB)
```

## Debugging

If messages aren't appearing:

1. **Check journal is readable:**
   ```bash
   journalctl -t zeropoint-set-memorable-hostname -n 10
   ```

2. **Verify logger command works:**
   ```bash
   logger -t test-service "test message"
   journalctl -t test-service -n 1
   ```

3. **Check agent is running:**
   ```bash
   ps aux | grep zeropoint-agent
   ```

4. **Check for journal permission issues:**
   ```bash
   stat /run/log/journal
   groups $USER  # should include 'systemd-journal' or 'adm'
   ```

## Testing Error Scenarios

To test error handling, modify `test-boot-sequence.sh` to include:

```bash
# Add after a service starts
log_error "setup-storage" "Failed to format disk /dev/sdb1"

# This will populate failed_services in boot status
```

Run the modified script and check:
```bash
curl http://localhost:2370/api/boot/status | jq '.is_boot_failed, .failed_services'
```

Should show:
```json
true
{
  "setup-storage": "Failed to format disk /dev/sdb1"
}
```

## Next: UI Testing

Once the REST/WebSocket APIs are confirmed working, the React overlay component will:
1. Connect to `ws://localhost:2370/api/boot/stream`
2. Block all UI interactions until `is_complete` is true
3. Display phase timeline and live logs
4. Show error details if `is_boot_failed` is true
