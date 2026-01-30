import os
import sys
from pathlib import Path

# Ensure `src/` is on sys.path so we can import our package during development
sys.path.insert(0, str(Path(__file__).resolve().parent.joinpath("src")))

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from datetime import datetime, timezone

from zeropoint_agent.state_store import StateStore
from zeropoint_agent.hw_probe import HWProbe

app = FastAPI(title="Zeropoint Agent API", version="1.0.0")


@app.on_event("startup")
def _init_state_store():
    """Initialize the StateStore at ./data/store and attach to app.state."""
    store_path = os.environ.get("ZEROPOINT_ROOT_PATH", ".")
    store_dir = Path(store_path) / "data" / "store"
    # create state store (will initialize git repo if missing)
    state_store = StateStore(path=str(store_dir))
    # attach for handlers and executors to use
    app.state.state_store = state_store

@app.get("/health")
async def health():
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
        disks = HWProbe.get_disks()
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
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/hw/disks/{disk_id}")
async def get_disk(disk_id: str):
    """Get a specific disk by ID.
    
    disk_id can be:
    - ata-QEMU_HARDDISK_QM00001 (stable ID basename)
    - /dev/sda (device path)
    """
    try:
        disk = HWProbe.get_disk(disk_id)
        
        if not disk:
            raise HTTPException(status_code=404, detail=f"Disk not found: {disk_id}")
        
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
        raise HTTPException(status_code=500, detail=str(e))


# Serve the built web UI from `webui/dist` at the application root.
# If a file isn't found, StaticFiles will fall back to `index.html` when
# `html=True`, enabling SPA client-side routing.
app.mount("/", StaticFiles(directory="webui/dist", html=True), name="webui")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=2370)
