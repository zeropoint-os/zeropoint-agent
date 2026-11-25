"""Hardware discovery endpoints."""

import logging

from fastapi import APIRouter, HTTPException

from zeropoint_agent.hw_probe import HWProbe

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/hw", tags=["hardware"])


@router.get("/disks")
async def get_disks():
    """Get all available disks with partition information."""
    try:
        disks = HWProbe.get_disks()
        return {
            "ok": True,
            "disks": [
                {
                    "id": d.id, "device": d.device, "size": d.size,
                    "free": d.free, "sector_size": d.sector_size,
                    "removable": d.removable,
                    "read_only": d.read_only,
                    "is_root": d.is_root,
                    "is_boot": d.is_boot,
                    "holders": d.holders,
                    "partitions": [
                        {"id": p.id, "device": p.device, "size": p.size,
                         "free": p.free, "filesystem": p.filesystem,
                         "flags": p.flags, "number": p.number,
                         "uuid": p.uuid, "label": p.label,
                         "partuuid": p.partuuid, "parttype": p.parttype,
                         "mountpoints": p.mountpoints,
                         "mount_options": p.mount_options,
                         "is_root": p.is_root, "is_boot": p.is_boot,
                         "holders": p.holders}
                        for p in d.partitions
                    ],
                }
                for d in disks
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/gpus")
async def get_gpus():
    """Get all available GPUs."""
    try:
        gpus = HWProbe.get_gpus()
        return {
            "ok": True,
            "gpus": [
                {
                    "id": g.id, "device": g.device, "name": g.name,
                    "memory_total": g.memory_total, "memory_free": g.memory_free,
                    "driver_version": g.driver_version,
                    "compute_capability": g.compute_capability,
                }
                for g in gpus
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
