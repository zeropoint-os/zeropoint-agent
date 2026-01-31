#!/bin/bash
# Generate defaults.json from host system boot configuration
# Run this on the host system (not in devcontainer) to detect boot drive

set -e

OUTPUT_FILE="${1:-.}/data/defaults.json"
mkdir -p "$(dirname "$OUTPUT_FILE")"

# Parse /proc/cmdline for root= parameter
parse_root_param() {
    local root_param
    root_param=$(grep -oP 'root=\S+' /proc/cmdline | head -1)
    
    if [ -z "$root_param" ]; then
        echo "ERROR: Could not find root= parameter in /proc/cmdline" >&2
        return 1
    fi
    
    echo "$root_param"
}

# Resolve UUID/PARTUUID to device path
resolve_root_device() {
    local root_spec="$1"
    local device
    
    if [[ "$root_spec" == root=/dev/* ]]; then
        # Direct device path
        device="${root_spec#root=}"
    elif [[ "$root_spec" == root=UUID=* ]]; then
        # UUID format
        local uuid="${root_spec#root=UUID=}"
        device=$(blkid -U "$uuid" 2>/dev/null || true)
        if [ -z "$device" ]; then
            echo "ERROR: Could not resolve UUID=$uuid" >&2
            return 1
        fi
    elif [[ "$root_spec" == root=PARTUUID=* ]]; then
        # PARTUUID format
        local partuuid="${root_spec#root=PARTUUID=}"
        device=$(blkid -t PARTUUID="$partuuid" -o device 2>/dev/null || true)
        if [ -z "$device" ]; then
            echo "ERROR: Could not resolve PARTUUID=$partuuid" >&2
            return 1
        fi
    else
        echo "ERROR: Unrecognized root format: $root_spec" >&2
        return 1
    fi
    
    # Normalize to /dev path if it's a symlink
    device=$(readlink -f "$device")
    echo "$device"
}

# Get parent disk from partition device
get_disk_device() {
    local partition="$1"
    # Remove trailing digits and 'p' (for nvme): /dev/sda1 -> /dev/sda, /dev/nvme0n1p1 -> /dev/nvme0n1
    echo "$partition" | sed -E 's/p?[0-9]+$//'
}

# Get partition number from device
get_partition_number() {
    local partition="$1"
    # Extract trailing digits: /dev/sda1 -> 1, /dev/nvme0n1p1 -> 1
    echo "$partition" | grep -oE '[0-9]+$'
}

# Get filesystem type
get_filesystem() {
    local partition="$1"
    local fstype
    fstype=$(blkid -o value -s TYPE "$partition" 2>/dev/null || true)
    if [ -z "$fstype" ]; then
        # Fallback to /proc/mounts
        fstype=$(grep "^$partition " /proc/mounts 2>/dev/null | awk '{print $3}' || echo "unknown")
    fi
    echo "$fstype"
}

# Get stable /dev/disk/by-id/ ID for a device
get_stable_id() {
    local device="$1"
    local stable_id
    
    # Find the symlink in /dev/disk/by-id/ that points to this device
    for link in /dev/disk/by-id/*; do
        if [ -L "$link" ]; then
            local target
            target=$(readlink -f "$link")
            if [ "$target" = "$device" ]; then
                # Extract basename (without /dev/disk/by-id/)
                echo "${link##*/}"
                return 0
            fi
        fi
    done
    
    # Fallback: return the device path if no stable ID found
    echo "$device"
}

# Get partition device from disk and partition number
get_partition_device() {
    local disk="$1"
    local partition_num="$2"
    
    # For nvme devices: /dev/nvme0n1p1, for others: /dev/sdap1
    if [[ "$disk" == *nvme* ]]; then
        echo "${disk}p${partition_num}"
    else
        echo "${disk}${partition_num}"
    fi
}

# Main logic
echo "Detecting boot drive configuration..." >&2

root_param=$(parse_root_param)
echo "  Root parameter: $root_param" >&2

root_device=$(resolve_root_device "$root_param")
echo "  Boot device: $root_device" >&2

disk_device=$(get_disk_device "$root_device")
echo "  Parent disk: $disk_device" >&2

partition_num=$(get_partition_number "$root_device")
echo "  Partition number: $partition_num" >&2

filesystem=$(get_filesystem "$root_device")
echo "  Filesystem: $filesystem" >&2

# Get stable IDs
disk_stable_id=$(get_stable_id "$disk_device")
echo "  Disk stable ID: $disk_stable_id" >&2

partition_device=$(get_partition_device "$disk_device" "$partition_num")
partition_stable_id=$(get_stable_id "$partition_device")
echo "  Partition device: $partition_device" >&2
echo "  Partition stable ID: $partition_stable_id" >&2

# Generate JSON
cat > "$OUTPUT_FILE" << EOF
{
  "disks": [
    {
      "id": "$disk_stable_id",
      "device": "$disk_device"
    }
  ],
  "mounts": [
    {
      "id": "root",
      "partition_id": "$partition_stable_id",
      "partition_device": "$partition_device",
      "mountpoint": "/",
      "options": "defaults"
    }
  ],
  "paths": [
    {
      "id": "root",
      "mount_id": "root",
      "path": "/",
      "mode": "0755"
    }
  ],
  "vars": [],
  "modules": [],
  "links": [],
  "exposures": []
}
EOF

echo "Generated: $OUTPUT_FILE" >&2
cat "$OUTPUT_FILE" >&2
