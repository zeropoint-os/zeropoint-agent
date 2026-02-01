import os
import sys
import logging
import logging.handlers
from pathlib import Path

# Ensure `src/` is on sys.path so we can import our package during development
sys.path.insert(0, str(Path(__file__).resolve().parent.joinpath("src")))

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from datetime import datetime, timezone

from zeropoint_agent.state_store import StateStore
from zeropoint_agent.hw_probe import HWProbe
from zeropoint_agent.commands.disk import (
    AddManagedDisk, RemoveManagedDisk, PartitionManagedDisk,
    UpdatePartitionsManagedDisk, AutoPartitionManagedDisk,
    ResetPartitionsManagedDisk, FormatManagedDisk
)


# Configure logging
def setup_logging():
    """Configure colored console and file logging."""
    
    # Create logs directory if it doesn't exist
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    
    # Get root logger
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    
    # Remove any existing handlers
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    
    # Console handler with colors
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.DEBUG)
    console_formatter = _ColoredFormatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)
    
    # File handler (rotating to prevent huge logs)
    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "zeropoint-agent.log",
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5
    )
    file_handler.setLevel(logging.DEBUG)
    file_formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)
    
    return logger


class _ColoredFormatter(logging.Formatter):
    """Custom formatter with ANSI color codes."""
    
    COLORS = {
        "DEBUG": "\033[36m",      # Cyan
        "INFO": "\033[32m",       # Green
        "WARNING": "\033[33m",    # Yellow
        "ERROR": "\033[31m",      # Red
        "CRITICAL": "\033[35m",   # Magenta
    }
    RESET = "\033[0m"
    
    def format(self, record):
        log_color = self.COLORS.get(record.levelname, self.RESET)
        record.levelname = f"{log_color}{record.levelname}{self.RESET}"
        return super().format(record)


# Setup logging before creating the app
logger = setup_logging()
logger.info("Zeropoint Agent server starting...")

app = FastAPI(title="Zeropoint Agent API", version="1.0.0")


@app.on_event("startup")
def _init_state_store():
    """Initialize the StateStore at ./data/store and attach to app.state."""
    try:
        store_path = os.environ.get("ZEROPOINT_ROOT_PATH", ".")
        store_dir = Path(store_path) / "data" / "store"
        logger.info(f"Initializing StateStore at {store_dir}")
        # create state store (will initialize git repo if missing)
        state_store = StateStore(path=str(store_dir))
        # Set as singleton for app-wide access
        StateStore.set_instance(state_store)
        # attach for handlers and executors to use
        app.state.state_store = state_store
        logger.info("StateStore initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize StateStore: {e}", exc_info=True)
        raise

@app.get("/health")
async def health():
    logger.debug("Health check requested")
    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


# Hardware Discovery Endpoints

@app.get("/api/hw/disks")
@app.get("/api/hw/disks/")
async def get_disks():
    """Get all available disks with partition information.
    
    Returns list of disks with stable ID basenames (from /dev/disk/by-id/).
    Marks the boot disk and boot partition with "boot": true.
    """
    try:
        logger.debug("Fetching all disks")
        disks = HWProbe.get_disks()
        logger.info(f"Found {len(disks)} disks")
        
        # Get boot drive config
        store = StateStore.get_instance()
        boot_config = store.get_boot_drive_config()
        logger.debug(f"Boot config: {boot_config}")
        
        return {
            "ok": True,
            "boot_config": boot_config,
            "disks": [
                {
                    "id": disk.id,
                    "device": disk.device,
                    "size": disk.size,
                    "free": disk.free,
                    "sector_size": disk.sector_size,
                    "boot": disk.device == boot_config.get("disk_device") if boot_config else False,
                    "partitions": [
                        {
                            "device": p.device,
                            "size": p.size,
                            "free": p.free,
                            "filesystem": p.filesystem,
                            "flags": p.flags,
                            "boot": p.device == boot_config.get("partition_device") if boot_config else False,
                        }
                        for p in disk.partitions
                    ],
                }
                for disk in disks
            ]
        }
    except Exception as e:
        logger.error(f"Failed to fetch disks: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/hw/disks/{disk_id}")
async def get_disk(disk_id: str):
    """Get a specific disk by ID.
    
    disk_id can be:
    - ata-QEMU_HARDDISK_QM00001 (stable ID basename)
    - /dev/sda (device path)
    """
    try:
        logger.debug(f"Fetching disk: {disk_id}")
        disk = HWProbe.get_disk(disk_id)
        
        if not disk:
            logger.warning(f"Disk not found: {disk_id}")
            raise HTTPException(status_code=404, detail=f"Disk not found: {disk_id}")
        
        logger.info(f"Found disk {disk_id}: {disk.device} ({disk.size} bytes)")
        
        # Get boot drive config
        store = StateStore.get_instance()
        boot_config = store.get_boot_drive_config()
        
        return {
            "ok": True,
            "boot_config": boot_config,
            "disk": {
                "id": disk.id,
                "device": disk.device,
                "size": disk.size,
                "free": disk.free,
                "sector_size": disk.sector_size,
                "boot": disk.device == boot_config.get("disk_device") if boot_config else False,
                "partitions": [
                    {
                        "id": p.id,
                        "device": p.device,
                        "size": p.size,
                        "free": p.free,
                        "filesystem": p.filesystem,
                        "flags": p.flags,
                        "boot": p.device == boot_config.get("partition_device") if boot_config else False,
                    }
                    for p in disk.partitions
                ],
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch disk {disk_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/hw/gpus")
@app.get("/api/hw/gpus/")
async def get_gpus():
    """Get all available GPUs.
    
    Returns list of GPUs with memory and driver information.
    """
    try:
        logger.debug("Fetching all GPUs")
        gpus = HWProbe.get_gpus()
        logger.info(f"Found {len(gpus)} GPUs")
        return {
            "ok": True,
            "gpus": [
                {
                    "id": gpu.id,
                    "device": gpu.device,
                    "name": gpu.name,
                    "memory_total": gpu.memory_total,
                    "memory_free": gpu.memory_free,
                    "driver_version": gpu.driver_version,
                    "compute_capability": gpu.compute_capability,
                }
                for gpu in gpus
            ]
        }
    except Exception as e:
        logger.error(f"Failed to fetch GPUs: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# Managed Disk Management Endpoints

@app.post("/api/managed-disks")
async def add_managed_disk(body: dict):
    """Register a disk for management.
    
    Body: {disk_id: "nvme-Samsung..."}
    """
    try:
        disk_id = body.get("disk_id")
        if not disk_id:
            raise HTTPException(status_code=400, detail="disk_id is required")
        
        logger.info(f"Adding managed disk: {disk_id}")
        cmd = AddManagedDisk()
        result = cmd.execute({"disk_id": disk_id})
        
        return {
            "ok": result.status.value == "applied",
            "status": result.status.value,
            "output": result.output,
            "error": result.error,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to add managed disk: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/managed-disks/{disk_id}")
async def remove_managed_disk(disk_id: str):
    """Unregister a disk from management."""
    try:
        logger.info(f"Removing managed disk: {disk_id}")
        cmd = RemoveManagedDisk()
        result = cmd.execute({"disk_id": disk_id})
        
        return {
            "ok": result.status.value == "applied",
            "status": result.status.value,
            "output": result.output,
            "error": result.error,
        }
    except Exception as e:
        logger.error(f"Failed to remove managed disk: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# Partition Management Endpoints

@app.post("/api/managed-disks/{disk_id}/partitions")
async def set_partitions(disk_id: str, body: dict):
    """Set partition layout for a disk.
    
    Body: {partitions: [{index: 0, size_mb: 512, type: "primary", label: "boot"}, ...]}
    """
    try:
        partitions = body.get("partitions", [])
        if not partitions:
            raise HTTPException(status_code=400, detail="partitions list is required")
        
        logger.info(f"Setting {len(partitions)} partition(s) for {disk_id}")
        cmd = PartitionManagedDisk()
        result = cmd.execute({"disk_id": disk_id, "partitions": partitions})
        
        return {
            "ok": result.status.value in ["applied", "blocked"],
            "status": result.status.value,
            "reason": result.reason,
            "output": result.output,
            "error": result.error,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to set partitions for {disk_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/api/managed-disks/{disk_id}/partitions")
async def update_partitions(disk_id: str, body: dict):
    """Update specific partitions.
    
    Body: {updates: {0: {size_mb: 1024}, 1: {label: "root"}}}
    """
    try:
        updates = body.get("updates", {})
        if not updates:
            raise HTTPException(status_code=400, detail="updates dict is required")
        
        logger.info(f"Updating partitions for {disk_id}")
        cmd = UpdatePartitionsManagedDisk()
        result = cmd.execute({"disk_id": disk_id, "updates": updates})
        
        return {
            "ok": result.status.value in ["applied", "blocked"],
            "status": result.status.value,
            "reason": result.reason,
            "output": result.output,
            "error": result.error,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update partitions for {disk_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/managed-disks/{disk_id}/partitions/auto")
async def auto_partition(disk_id: str, body: dict = None):
    """Auto-partition a disk with standard layout.
    
    Body: {boot_size_mb: 512} (optional)
    """
    try:
        body = body or {}
        boot_size = body.get("boot_size_mb", 512)
        
        logger.info(f"Auto-partitioning {disk_id}")
        cmd = AutoPartitionManagedDisk()
        result = cmd.execute({"disk_id": disk_id, "boot_size_mb": boot_size})
        
        return {
            "ok": result.status.value in ["applied", "blocked"],
            "status": result.status.value,
            "reason": result.reason,
            "output": result.output,
            "error": result.error,
        }
    except Exception as e:
        logger.error(f"Failed to auto-partition {disk_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/managed-disks/{disk_id}/partitions/reset")
async def reset_partitions(disk_id: str):
    """Reset to current physical partition layout."""
    try:
        logger.info(f"Resetting partitions for {disk_id} to current layout")
        cmd = ResetPartitionsManagedDisk()
        result = cmd.execute({"disk_id": disk_id})
        
        return {
            "ok": result.status.value in ["applied", "blocked"],
            "status": result.status.value,
            "reason": result.reason,
            "output": result.output,
            "error": result.error,
        }
    except Exception as e:
        logger.error(f"Failed to reset partitions for {disk_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# Format Management Endpoints

@app.post("/api/managed-disks/{disk_id}/partitions/{partition_index}/format")
async def format_partition(disk_id: str, partition_index: int, body: dict):
    """Set filesystem for a partition.
    
    Body: {filesystem: "ext4", confirm_wipe: false}
    """
    try:
        filesystem = body.get("filesystem", "ext4")
        confirm_wipe = body.get("confirm_wipe", False)
        
        logger.info(f"Setting format for {disk_id}:{partition_index} to {filesystem}")
        cmd = FormatManagedDisk()
        result = cmd.execute({
            "disk_id": disk_id,
            "partition_index": partition_index,
            "filesystem": filesystem,
            "confirm_wipe": confirm_wipe
        })
        
        return {
            "ok": result.status.value in ["applied", "blocked"],
            "status": result.status.value,
            "reason": result.reason,
            "output": result.output,
            "error": result.error,
        }
    except Exception as e:
        logger.error(f"Failed to format {disk_id}:{partition_index}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# State Endpoints

@app.get("/api/state")
async def get_state():
    """Get overall state comparing desired (edit) vs applied (main).
    
    Returns resources grouped by table with action (added/removed/edited/unchanged)
    and state (pending/current).
    """
    try:
        store = StateStore.get_instance()
        state = store.get_state()
        return {
            "ok": True,
            "data": state
        }
    except Exception as e:
        logger.error(f"Failed to get state: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/state/{resource_type}/{resource_id}")
async def get_resource_status(resource_type: str, resource_id: str):
    """Get status of a specific resource.
    
    Returns: {
        "action": "added|removed|edited|unchanged",
        "state": "pending|current",
        "desired": {...} or null,
        "current": {...} or null
    }
    """
    try:
        store = StateStore.get_instance()
        state = store.get_state()
        
        if resource_type not in state:
            raise HTTPException(status_code=404, detail=f"Unknown resource type: {resource_type}")
        
        if resource_id not in state[resource_type]:
            raise HTTPException(status_code=404, detail=f"Resource not found: {resource_type}/{resource_id}")
        
        return state[resource_type][resource_id]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get status for {resource_type}/{resource_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# If a file isn't found, StaticFiles will fall back to `index.html` when
# `html=True`, enabling SPA client-side routing.
app.mount("/", StaticFiles(directory="webui/dist", html=True), name="webui")
logger.info("Web UI mounted at /")


if __name__ == "__main__":
    import uvicorn

    logger.info("Starting Zeropoint Agent server on 0.0.0.0:2370")
    uvicorn.run(app, host="0.0.0.0", port=2370, log_config=None)
