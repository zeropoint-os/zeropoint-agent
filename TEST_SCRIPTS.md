# Boot Monitor Test Scripts

Two test scripts are provided to validate the boot monitoring API:

## 1. `test-boot-sequence.sh` - Boot Simulation

Simulates the complete Zeropoint boot sequence with all 4 phases and realistic logging.

**Usage:**
```bash
./test-boot-sequence.sh
```

**What it does:**
- Logs messages for all services in correct sequence
- Includes progress markers (`✓ step-name`)
- Simulates reboot delays
- Takes ~30 seconds to complete

**Expected output:**
```
[set-memorable-hostname] Generated hostname: zeropoint-dakara-peron
[set-memorable-hostname] ✓ hostname-generated
[resize-rootfs] Expanding Root Filesystem...
...
=== Boot Sequence Simulation Complete ===
```

These messages go to systemd journal and are picked up by the boot monitor.

---

## 2. `test-boot-api.sh` - HTTP API Testing

Quick HTTP testing script to inspect boot status and logs.

**Usage:**
```bash
./test-boot-api.sh                    # Uses localhost:2370
./test-boot-api.sh http://other:2370  # Custom URL
```

**What it shows:**
- Boot completion status
- Current phase
- Service statuses
- Recent logs
- Failed services

---

## Quick Testing Flow

### Terminal 1: Start Agent
```bash
./bin/zeropoint-agent
```

### Terminal 2: Monitor (choose one)

**Option A - WebSocket (real-time):**
```bash
# Install wscat if needed:
npm install -g wscat

# Then watch:
wscat -c ws://localhost:2370/api/boot/stream
```

**Option B - HTTP Polling:**
```bash
watch -n 1 'curl -s http://localhost:2370/api/boot/status | jq .is_complete, .current_phase'
```

**Option C - One-time API test:**
```bash
./test-boot-api.sh
```

### Terminal 3: Run Simulation
```bash
./test-boot-sequence.sh
```

---

## Expected Behavior

As `test-boot-sequence.sh` runs:

1. **Agent Terminal** - Debug logs appear as journal entries are read
2. **Monitor Terminal** - Status updates in real-time
   - `is_complete` stays `false` during boot
   - `current_phase` progresses: "base" → "storage" → "utilities" → "drivers"
   - Service statuses change: "pending" → "running" → "completed"
   - Logs populate in `recent_logs`
3. **Simulation Terminal** - Color-coded progress output

When boot completes:
- `is_complete` becomes `true`
- WebSocket may reconnect briefly for post-boot messages
- All logs are preserved

---

## Message Format

All boot services must use this format:

```bash
# Regular message
logger -t zeropoint-<service> "Message here"

# Progress marker
logger -t zeropoint-<service> "✓ step-completed"

# Error (optional)
logger -t zeropoint-<service> -p err "Error message"
```

The boot monitor parses:
- Service name from tag (removes `zeropoint-` prefix)
- Markers starting with `✓ ` are flagged as milestones
- Error level triggers `is_boot_failed = true`

---

## Troubleshooting

**Q: No logs appear in boot status**
- Check agent is running: `ps aux | grep zeropoint-agent`
- Verify logger command: `logger -t test-msg "hello" && journalctl -t test-msg -n 1`
- Check journal readable: `journalctl -n 1` (should work)

**Q: WebSocket won't connect**
- Verify agent is listening: `ss -tlnp | grep 2370`
- Check firewall: `sudo ufw allow 2370` (if needed)
- Verify path: should be `/api/boot/stream` (no `.ws` extension)

**Q: Messages appear in journal but not in API**
- Agent may not be reading journal yet (starts from tail on startup)
- New messages are picked up immediately after agent starts
- Check agent logs: `grep -i journal` in agent output

**Q: Test script hangs**
- May be waiting for logger to complete
- Press Ctrl+C to stop, or check disk space

---

## Next Steps

Once API is validated:
1. Create React `<BootOverlay>` component
2. Add request-blocking middleware (return 503 until boot complete)
3. Integrate into main web UI
4. Test full lifecycle with real boot services
