"""Persistent agent identity and fleet-facing metadata."""

from __future__ import annotations

import os
import platform
import shutil
import socket
import uuid
from dataclasses import asdict, dataclass
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Dict

from zeropoint_agent.sqlite_store import SQLiteGraphStore

API_VERSION = "1"


@dataclass(frozen=True)
class AgentIdentity:
    agent_id: str
    name: str


def load_or_create_identity(store: SQLiteGraphStore) -> AgentIdentity:
    agent_id = store.get_agent_metadata("agent_id")
    if not agent_id:
        agent_id = f"zp_{uuid.uuid4().hex}"
        store.set_agent_metadata("agent_id", agent_id)
    configured_name = os.environ.get("ZEROPOINT_AGENT_NAME", "").strip()
    name = configured_name or store.get_agent_metadata("agent_name")
    if not name:
        name = socket.gethostname()
    if store.get_agent_metadata("agent_name") != name:
        store.set_agent_metadata("agent_name", name)
    return AgentIdentity(agent_id=str(agent_id), name=str(name))


def agent_version() -> str:
    try:
        return version("zeropoint-agent")
    except PackageNotFoundError:
        return "0.0.0+local"


def build_meta(
    identity: AgentIdentity,
    store: SQLiteGraphStore,
    *,
    port: int,
) -> Dict[str, Any]:
    return {
        **asdict(identity),
        "agent_version": agent_version(),
        "api_version": API_VERSION,
        "schema_version": store.schema_version,
        "security_mode": "trusted_lan",
        "endpoint": {"scheme": "http", "port": port},
        "platform": {
            "hostname": socket.gethostname(),
            "os": platform.system().lower(),
            "architecture": platform.machine(),
            "kernel": platform.release(),
        },
        "capabilities": {
            "graph": True,
            "graph_runs": True,
            "promises": True,
            "jobs": True,
            "pause_resume": True,
            "remove_runs": True,
            "hardware_inventory": True,
            "docker": shutil.which("docker") is not None,
            "terraform": shutil.which("terraform") is not None,
        },
    }
