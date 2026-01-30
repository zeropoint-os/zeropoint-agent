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
    """
    try:
        logger.debug("Fetching all disks")
        disks = HWProbe.get_disks()
        logger.info(f"Found {len(disks)} disks")
        return {
            "ok": True,
            "disks": [
                {
                    "id": disk.id,
                    "device": disk.device,
                    "size": disk.size,
                    "free": disk.free,
                    "sector_size": disk.sector_size,
                    "partitions": [
                        {
                            "id": p.id,
                            "device": p.device,
                            "size": p.size,
                            "free": p.free,
                            "filesystem": p.filesystem,
                            "flags": p.flags,
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
        return {
            "ok": True,
            "disk": {
                "id": disk.id,
                "device": disk.device,
                "size": disk.size,
                "free": disk.free,
                "sector_size": disk.sector_size,
                "partitions": [
                    {
                        "id": p.id,
                        "device": p.device,
                        "size": p.size,
                        "free": p.free,
                        "filesystem": p.filesystem,
                        "flags": p.flags,
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


# Serve the built web UI from `webui/dist` at the application root.
# If a file isn't found, StaticFiles will fall back to `index.html` when
# `html=True`, enabling SPA client-side routing.
app.mount("/", StaticFiles(directory="webui/dist", html=True), name="webui")
logger.info("Web UI mounted at /")


if __name__ == "__main__":
    import uvicorn

    logger.info("Starting Zeropoint Agent server on 0.0.0.0:2370")
    uvicorn.run(app, host="0.0.0.0", port=2370, log_config=None)
