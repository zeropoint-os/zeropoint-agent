#!/bin/bash
# Test script to simulate the zeropoint boot sequence
# Run this in parallel with the agent to test boot monitoring

set -e

DELAY="${1:-1}"  # Default 1 second between log entries

log_service() {
    local service=$1
    local message=$2
    logger -t "zeropoint-$service" "$message"
    sleep "$DELAY"
}

mark_step() {
    local service=$1
    local step=$2
    logger -t "zeropoint-$service" "✓ $step"
    sleep "$DELAY"
}

echo "🚀 Starting boot sequence simulation..."
echo "   Service tags will be logged to syslog"
echo "   Agent should be running: ./bin/zeropoint-agent"
echo ""

# ============================================================================
# PHASE 1: Base System Services
# ============================================================================
echo "📋 PHASE 1: Base System Services"

# Service: set-memorable-hostname
log_service "hostname" "=== Generating Memorable Hostname ==="
log_service "hostname" "Querying metadata service..."
log_service "hostname" "Generated hostname: zeropoint-dakara-peron"
mark_step "hostname" "hostname-generated"
log_service "hostname" "Updated system hostname"
mark_step "hostname" "hostname-updated"
log_service "hostname" "=== Hostname Generation Complete ==="

# Service: resize-rootfs
log_service "rootfs" "=== Starting Root Filesystem Expansion ==="
log_service "rootfs" "Detected root device: /dev/sda1"
mark_step "rootfs" "device-detected"
log_service "rootfs" "Expanding partition..."
mark_step "rootfs" "partition-expanded"
log_service "rootfs" "Expanding filesystem..."
mark_step "rootfs" "filesystem-expanded"
log_service "rootfs" "=== Root Filesystem Expansion Complete ==="

# ============================================================================
# PHASE 2: Storage Setup Services
# ============================================================================
echo "📋 PHASE 2: Storage Setup Services"

# Service: setup-storage
log_service "storage" "=== Starting Storage Setup ==="
log_service "storage" "Scanning for additional block devices..."
log_service "storage" "Found NVMe: /dev/nvme0n1 (1TB)"
mark_step "storage" "nvme-detected"
log_service "storage" "Initializing storage mount points..."
mark_step "storage" "mounts-created"
log_service "storage" "=== Storage Setup Complete ==="

# Service: configure-apt-storage
log_service "apt-storage" "=== Configuring Apt for NVMe Storage ==="
log_service "apt-storage" "Updating apt sources.list..."
log_service "apt-storage" "Configuring apt cache directory: /nvme/apt-cache"
mark_step "apt-storage" "apt-cache-configured"
log_service "apt-storage" "Running apt update..."
log_service "apt-storage" "Successfully downloaded package lists"
mark_step "apt-storage" "package-lists-updated"
log_service "apt-storage" "=== Apt Configuration Complete ==="

# ============================================================================
# PHASE 3: Utility Services
# ============================================================================
echo "📋 PHASE 3: Utility Services"

# Service: update-agent
log_service "update-agent" "=== Checking for Agent Updates ==="
log_service "update-agent" "Current version: 0.1.0"
log_service "update-agent" "Checking upstream repository..."
log_service "update-agent" "Latest version available: 0.1.0 (already current)"
mark_step "update-agent" "version-check-complete"
log_service "update-agent" "=== Agent Update Check Complete ==="

# ============================================================================
# PHASE 4: Hardware Driver Services
# ============================================================================
echo "📋 PHASE 4: Hardware Driver Services"

# Service: setup-nvidia-drivers
log_service "nvidia-drivers" "=== Starting NVIDIA GPU Driver Installation ==="
log_service "nvidia-drivers" "Detecting NVIDIA GPU..."
log_service "nvidia-drivers" "Found: NVIDIA A100 (CUDA Compute 8.0)"
mark_step "nvidia-drivers" "gpu-detected"
log_service "nvidia-drivers" "Installing NVIDIA driver dependencies..."
log_service "nvidia-drivers" "Installing CUDA toolkit 12.1..."
mark_step "nvidia-drivers" "cuda-installed"
log_service "nvidia-drivers" "Installing cuDNN..."
mark_step "nvidia-drivers" "cudnn-installed"
log_service "nvidia-drivers" "Kernel headers required - system will reboot"
mark_step "nvidia-drivers" "reboot-required"
log_service "nvidia-drivers" "=== NVIDIA Driver Installation Complete (Reboot Pending) ==="

# Simulate reboot delay
echo "🔄 System rebooting in 5 seconds... (waiting for reconnection)"
sleep 5

# Service: setup-nvidia-post-reboot
log_service "nvidia-post" "=== Starting NVIDIA Post-Reboot Verification ==="
log_service "nvidia-post" "Verifying NVIDIA driver kernel module..."
mark_step "nvidia-post" "driver-loaded"
log_service "nvidia-post" "Running nvidia-smi..."
log_service "nvidia-post" "GPU 0: NVIDIA A100 (Driver Version: 550.54)"
mark_step "nvidia-post" "gpu-verified"
log_service "nvidia-post" "Testing CUDA functionality..."
mark_step "nvidia-post" "cuda-functional"
log_service "nvidia-post" "=== NVIDIA Post-Reboot Verification Complete ==="

# Final completion
log_service "boot-complete" "=== All Boot Services Complete ==="
mark_step "boot-complete" "boot-complete"
log_service "boot-complete" "System initialization successful"

echo ""
echo "✅ Boot sequence simulation complete!"
echo ""
echo "Check the agent status:"
echo "  curl http://localhost:2370/api/boot/status | jq"
echo ""
echo "Stream logs from WebSocket:"
echo "  wscat -c ws://localhost:2370/api/boot/stream"
