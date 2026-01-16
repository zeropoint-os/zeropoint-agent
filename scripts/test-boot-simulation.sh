#!/bin/bash
# Test script: simulates boot services logging to syslog in realistic patterns
# Run this in a terminal while the agent is running in another
# The agent will parse these messages and stream them to WebSocket clients

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_service() {
    local service=$1
    local message=$2
    logger -t "$service" "$message"
    echo -e "${BLUE}[$(date '+%H:%M:%S')]${NC} $service: $message"
}

mark_service() {
    local service=$1
    local step=$2
    logger -t "$service" "✓ $step"
    echo -e "${GREEN}[$(date '+%H:%M:%S')]${NC} $service: ✓ $step"
}

error_service() {
    local service=$1
    local message=$2
    logger -t "$service" -p err "✗ $message"
    echo -e "${RED}[$(date '+%H:%M:%S')]${NC} $service: ✗ $message"
}

pause() {
    local secs=${1:-2}
    sleep "$secs"
}

echo -e "${YELLOW}=== Boot Services Test Simulator ===${NC}"
echo "This script simulates real boot service logging"
echo "The agent should be running: ./bin/zeropoint-agent"
echo ""
echo "Open another terminal to monitor:"
echo "  curl http://localhost:2370/api/boot/status | jq"
echo "  wscat -c ws://localhost:2370/api/boot/stream"
echo ""
echo -e "${YELLOW}Starting boot sequence simulation...${NC}"
echo ""

# ============================================================================
# PHASE 1: Base System Services
# ============================================================================
echo -e "${YELLOW}[Phase 1] Base System Services${NC}"

# Service: set-memorable-hostname
echo ""
log_service "zeropoint-set-memorable-hostname" "=== Generating Memorable Hostname ==="
pause 1
log_service "zeropoint-set-memorable-hostname" "Fetching random hostname from API..."
pause 1
mark_service "zeropoint-set-memorable-hostname" "hostname-fetched"
pause 0.5
log_service "zeropoint-set-memorable-hostname" "Setting hostname to: zeropoint-dakara-peron"
pause 1
mark_service "zeropoint-set-memorable-hostname" "hostname-applied"
pause 0.5
mark_service "zeropoint-set-memorable-hostname" "service-complete"

# Service: resize-rootfs
echo ""
log_service "zeropoint-resize-rootfs" "=== First-Boot Root Filesystem Expansion ==="
pause 1
log_service "zeropoint-resize-rootfs" "Detecting root partition: /dev/sda1"
pause 0.5
mark_service "zeropoint-resize-rootfs" "root-partition-detected"
pause 0.5
log_service "zeropoint-resize-rootfs" "Current size: 20GB, expanding to full disk..."
pause 2
log_service "zeropoint-resize-rootfs" "Filesystem extended: 20GB → 500GB"
pause 0.5
mark_service "zeropoint-resize-rootfs" "filesystem-expanded"
pause 0.5
mark_service "zeropoint-resize-rootfs" "service-complete"

# ============================================================================
# PHASE 2: Storage Setup Services
# ============================================================================
echo ""
echo -e "${YELLOW}[Phase 2] Storage Setup Services${NC}"

# Service: setup-storage
echo ""
log_service "zeropoint-setup-storage" "=== First-Boot Storage Setup ==="
pause 1
log_service "zeropoint-setup-storage" "Scanning for additional disks..."
pause 1
log_service "zeropoint-setup-storage" "Found 3 disks: /dev/sdb (1TB), /dev/sdc (2TB), /dev/nvme0n1 (500GB)"
pause 0.5
mark_service "zeropoint-setup-storage" "disks-scanned"
pause 0.5
log_service "zeropoint-setup-storage" "Selected largest disk: /dev/sdc (2TB)"
pause 0.5
mark_service "zeropoint-setup-storage" "storage-disk-selected"
pause 1
log_service "zeropoint-setup-storage" "Formatting /dev/sdc as ext4..."
pause 2
mark_service "zeropoint-setup-storage" "storage-formatted"
pause 0.5
log_service "zeropoint-setup-storage" "Mounting at /var/lib/zeropoint"
pause 1
mark_service "zeropoint-setup-storage" "storage-mounted"
pause 0.5
mark_service "zeropoint-setup-storage" "service-complete"

# Service: configure-apt-storage
echo ""
log_service "zeropoint-configure-apt-storage" "=== Configure Apt to use NVMe Storage ==="
pause 1
log_service "zeropoint-configure-apt-storage" "Checking NVMe availability..."
pause 0.5
log_service "zeropoint-configure-apt-storage" "Found: /dev/nvme0n1"
pause 0.5
mark_service "zeropoint-configure-apt-storage" "nvme-detected"
pause 1
log_service "zeropoint-configure-apt-storage" "Redirecting /var/cache/apt to NVMe"
pause 0.5
mark_service "zeropoint-configure-apt-storage" "apt-cache-configured"
pause 0.5
log_service "zeropoint-configure-apt-storage" "Cache directory: /dev/nvme0n1/apt-cache"
pause 0.5
mark_service "zeropoint-configure-apt-storage" "service-complete"

# ============================================================================
# PHASE 3: Utility Services
# ============================================================================
echo ""
echo -e "${YELLOW}[Phase 3] Utility Services${NC}"

# Service: update-agent
echo ""
log_service "zeropoint-update-agent" "=== Zeropoint Agent Update Check ==="
pause 1
log_service "zeropoint-update-agent" "Checking for agent updates..."
pause 2
log_service "zeropoint-update-agent" "Current version: 0.2.1"
pause 0.5
log_service "zeropoint-update-agent" "Latest version: 0.2.3"
pause 0.5
mark_service "zeropoint-update-agent" "update-available"
pause 1
log_service "zeropoint-update-agent" "Downloading agent v0.2.3..."
pause 2
log_service "zeropoint-update-agent" "Verifying checksum..."
pause 1
mark_service "zeropoint-update-agent" "agent-downloaded"
pause 0.5
log_service "zeropoint-update-agent" "Installing update..."
pause 1
mark_service "zeropoint-update-agent" "agent-updated"
pause 0.5
mark_service "zeropoint-update-agent" "service-complete"

# ============================================================================
# PHASE 4: Driver Services
# ============================================================================
echo ""
echo -e "${YELLOW}[Phase 4] Hardware Driver Services${NC}"

# Service: setup-nvidia-drivers
echo ""
log_service "zeropoint-setup-nvidia-drivers" "=== NVIDIA GPU Driver Installation ==="
pause 1
log_service "zeropoint-setup-nvidia-drivers" "Detecting NVIDIA GPU..."
pause 1
log_service "zeropoint-setup-nvidia-drivers" "Found: NVIDIA A100 GPU"
pause 0.5
mark_service "zeropoint-setup-nvidia-drivers" "gpu-detected"
pause 1
log_service "zeropoint-setup-nvidia-drivers" "Installing kernel headers..."
pause 2
mark_service "zeropoint-setup-nvidia-drivers" "kernel-headers-installed"
pause 1
log_service "zeropoint-setup-nvidia-drivers" "Installing NVIDIA driver (nvidia-driver-535)..."
pause 3
log_service "zeropoint-setup-nvidia-drivers" "Compiling kernel module..."
pause 2
mark_service "zeropoint-setup-nvidia-drivers" "driver-compiled"
pause 1
log_service "zeropoint-setup-nvidia-drivers" "Installation complete. Reboot required to activate."
pause 0.5
mark_service "zeropoint-setup-nvidia-drivers" "reboot-required"
pause 0.5
mark_service "zeropoint-setup-nvidia-drivers" "service-complete"

# Service: setup-nvidia-post-reboot
echo ""
log_service "zeropoint-setup-nvidia-post-reboot" "=== NVIDIA GPU Post-Reboot Verification ==="
pause 1
log_service "zeropoint-setup-nvidia-post-reboot" "Waiting for system to stabilize..."
pause 2
log_service "zeropoint-setup-nvidia-post-reboot" "Verifying NVIDIA driver loaded..."
pause 1
mark_service "zeropoint-setup-nvidia-post-reboot" "driver-verified"
pause 0.5
log_service "zeropoint-setup-nvidia-post-reboot" "Running nvidia-smi..."
pause 1
log_service "zeropoint-setup-nvidia-post-reboot" "NVIDIA A100 | 40GB | Active"
pause 0.5
mark_service "zeropoint-setup-nvidia-post-reboot" "gpu-operational"
pause 0.5
mark_service "zeropoint-setup-nvidia-post-reboot" "service-complete"

# Service: boot-complete
echo ""
log_service "zeropoint-boot-complete" "=== Boot Process Complete ==="
pause 1
log_service "zeropoint-boot-complete" "All services initialized successfully"
pause 0.5
mark_service "zeropoint-boot-complete" "all-services-ready"
pause 0.5
log_service "zeropoint-boot-complete" "System is ready for user access"
pause 0.5
mark_service "zeropoint-boot-complete" "boot-complete"

echo ""
echo -e "${GREEN}=== Boot sequence complete! ===${NC}"
echo ""
echo "Check the status endpoints:"
echo "  curl http://localhost:2370/api/boot/status | jq"
echo "  curl http://localhost:2370/api/boot/logs?level=error | jq"
echo "  curl http://localhost:2370/api/boot/logs?service=zeropoint-setup-storage | jq"
