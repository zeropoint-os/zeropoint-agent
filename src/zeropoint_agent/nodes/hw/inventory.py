"""Generic live hardware roots for non-storage device classes."""

from __future__ import annotations

import os
import platform
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict

from zeropoint_agent.hw_probe import HWProbe
from zeropoint_agent.inode import INode, NodeResult, ResolveMode
from zeropoint_agent.nodes.hw.disks import _node_segment


@dataclass
class HardwareDeviceResult:
    kind: str
    stable_id: str
    attributes: Dict[str, Any] = field(default_factory=dict)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _network_devices() -> Dict[str, Dict[str, Any]]:
    devices: Dict[str, Dict[str, Any]] = {}
    for path in sorted(Path("/sys/class/net").glob("*")):
        name = path.name
        address = _read(path / "address")
        stable_id = address if address and address != "00:00:00:00:00:00" else name
        devices[stable_id] = {
            "name": name,
            "address": address,
            "operstate": _read(path / "operstate"),
            "mtu": _read(path / "mtu"),
        }
    return devices


def _usb_devices() -> Dict[str, Dict[str, Any]]:
    devices: Dict[str, Dict[str, Any]] = {}
    for path in sorted(Path("/sys/bus/usb/devices").glob("*")):
        vendor = _read(path / "idVendor")
        product = _read(path / "idProduct")
        if not vendor or not product:
            continue
        serial = _read(path / "serial")
        stable_id = f"{vendor}:{product}:{serial or path.name}"
        devices[stable_id] = {
            "vendor_id": vendor,
            "product_id": product,
            "serial": serial,
            "manufacturer": _read(path / "manufacturer"),
            "product": _read(path / "product"),
            "sysfs": str(path),
        }
    return devices


def _pci_devices() -> Dict[str, Dict[str, Any]]:
    devices: Dict[str, Dict[str, Any]] = {}
    for path in sorted(Path("/sys/bus/pci/devices").glob("*")):
        driver = ""
        try:
            driver = (path / "driver").resolve().name
        except OSError:
            pass
        devices[path.name] = {
            "vendor": _read(path / "vendor"),
            "device": _read(path / "device"),
            "class": _read(path / "class"),
            "subsystem_vendor": _read(path / "subsystem_vendor"),
            "subsystem_device": _read(path / "subsystem_device"),
            "driver": driver,
            "sysfs": str(path),
        }
    return devices


def _gpu_devices() -> Dict[str, Dict[str, Any]]:
    return {
        gpu.id: {
            "device": gpu.device,
            "name": gpu.name,
            "memory_total": gpu.memory_total,
            "memory_free": gpu.memory_free,
            "driver_version": gpu.driver_version,
            "compute_capability": gpu.compute_capability,
        }
        for gpu in HWProbe.get_gpus()
    }


def _system_devices() -> Dict[str, Dict[str, Any]]:
    memory = 0
    try:
        memory = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError):
        pass
    return {
        "machine": {
            "architecture": platform.machine(),
            "processor": platform.processor(),
            "cpu_count": os.cpu_count() or 0,
            "memory_total": memory,
            "kernel": platform.release(),
        }
    }


_PROBES = {
    "gpu": _gpu_devices,
    "network": _network_devices,
    "usb": _usb_devices,
    "pci": _pci_devices,
    "system": _system_devices,
}


@dataclass
class HardwareDevice(INode[None, HardwareDeviceResult]):
    kind: str
    stable_id: str
    attributes: Dict[str, Any] = field(default_factory=dict)

    def resolve(self, input, mode: ResolveMode) -> NodeResult[HardwareDeviceResult]:
        if mode == ResolveMode.MOCK:
            return NodeResult.success(HardwareDeviceResult(
                self.kind,
                self.stable_id,
                dict(self.attributes),
            ))
        current = _PROBES[self.kind]().get(self.stable_id)
        if current is None:
            return NodeResult.failed(
                f"{self.kind} hardware not found: {self.stable_id}"
            )
        return NodeResult.success(HardwareDeviceResult(
            self.kind,
            self.stable_id,
            current,
        ))

    def verify(self, mode: ResolveMode) -> NodeResult[HardwareDeviceResult]:
        if mode == ResolveMode.MOCK:
            return self.resolve(None, mode)
        current = _PROBES[self.kind]().get(self.stable_id)
        if current is None:
            return NodeResult()
        return NodeResult.success(HardwareDeviceResult(
            self.kind,
            self.stable_id,
            current,
        ))

    def remove(self, mode: ResolveMode) -> NodeResult[HardwareDeviceResult]:
        return NodeResult.success()


@dataclass
class HardwareInventoryResult:
    kind: str
    devices: Dict[str, str] = field(default_factory=dict)


@dataclass
class HardwareInventory(INode[None, HardwareInventoryResult]):
    kind: str

    def resolve(
        self,
        input,
        mode: ResolveMode,
    ) -> NodeResult[HardwareInventoryResult]:
        if self.kind not in _PROBES:
            return NodeResult.failed(f"unknown hardware inventory kind: {self.kind}")
        devices = {} if mode == ResolveMode.MOCK else _PROBES[self.kind]()
        result = NodeResult.success(HardwareInventoryResult(kind=self.kind))
        for stable_id, attributes in devices.items():
            node_id = f"{self.id}/{_node_segment(stable_id)}"
            result.output.devices[stable_id] = node_id
            result.produce(
                node_id,
                HardwareDevice(
                    kind=self.kind,
                    stable_id=stable_id,
                    attributes=attributes,
                ),
                parents=[self.id],
                perms="r--",
                tags=["hardware", self.kind],
            )
        return result

    def verify(self, mode: ResolveMode) -> NodeResult[HardwareInventoryResult]:
        return NodeResult()

    def remove(self, mode: ResolveMode) -> NodeResult[HardwareInventoryResult]:
        return NodeResult.success()
