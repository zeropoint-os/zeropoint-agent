"""Zeropoint Agent — REST API for the graph-based infrastructure language.

The DAG is the source of truth. The API lets you:
- Build the graph (add nodes, type-checked edges)
- Resolve the graph (live, dry_run, mock)
- Inspect the graph (status, outputs, structure)
- Probe hardware (disks, GPUs)
"""

import os
import sys
import logging
import logging.handlers
from pathlib import Path
from dataclasses import asdict
from typing import Optional, List, Dict, Any

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from datetime import datetime, timezone

from zeropoint_agent.dag import DAG
from zeropoint_agent.inode import ResolveMode
from zeropoint_agent.entities import NodeStatus
from zeropoint_agent.graph_store import GraphStore
from zeropoint_agent.hw_probe import HWProbe
from zeropoint_agent.nodes import (
    NetworkNode, DockerNode, DriverNode,
    DiskNode, PartitionNode, FormatNode, MountNode,
    PathNode, VarNode, ModuleNode, LinkNode, ExposureNode,
)


# --- Logging ---

def setup_logging():
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)

    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    for h in logger.handlers[:]:
        logger.removeHandler(h)

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.DEBUG)
    console.setFormatter(_ColoredFormatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))
    logger.addHandler(console)

    fh = logging.handlers.RotatingFileHandler(
        log_dir / "zeropoint-agent.log", maxBytes=10*1024*1024, backupCount=5
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))
    logger.addHandler(fh)
    return logger


class _ColoredFormatter(logging.Formatter):
    COLORS = {
        "DEBUG": "\033[36m", "INFO": "\033[32m", "WARNING": "\033[33m",
        "ERROR": "\033[31m", "CRITICAL": "\033[35m",
    }
    RESET = "\033[0m"

    def format(self, record):
        color = self.COLORS.get(record.levelname, self.RESET)
        record.levelname = f"{color}{record.levelname}{self.RESET}"
        return super().format(record)


logger = setup_logging()
logger.info("Zeropoint Agent starting...")

app = FastAPI(title="Zeropoint Agent API", version="2.0.0")


# --- Node type registry ---

NODE_REGISTRY = {
    "network": NetworkNode,
    "docker": DockerNode,
    "driver": DriverNode,
    "disk": DiskNode,
    "partition": PartitionNode,
    "format": FormatNode,
    "mount": MountNode,
    "path": PathNode,
    "var": VarNode,
    "module": ModuleNode,
    "link": LinkNode,
    "exposure": ExposureNode,
}


# --- Request/Response models ---

class NodeSpec(BaseModel):
    """Specification for a node to add to the graph."""
    id: str
    type: str           # "disk", "partition", "format", etc.
    config: Dict[str, Any]
    parents: List[str] = []


class GraphBuildRequest(BaseModel):
    """Build a complete graph in one call."""
    nodes: List[NodeSpec]


class ResolveRequest(BaseModel):
    """Request to resolve the graph."""
    mode: str = "mock"   # "live", "dry_run", "mock"


# --- Startup ---

@app.on_event("startup")
def _init():
    store_path = os.environ.get("ZEROPOINT_ROOT_PATH", ".")
    db_path = str(Path(store_path) / "data" / "graph.db")
    logger.info(f"Initializing graph store at {db_path}")
    app.state.store = GraphStore(db_path)
    app.state.dag = DAG(store=app.state.store)
    logger.info("Graph store initialized")


# --- Health ---

@app.get("/api/health")
async def health():
    dag = app.state.dag
    node_count = len(dag.nodes)
    statuses = {}
    for nid, entry in dag.nodes.items():
        s = entry.status.value
        statuses[s] = statuses.get(s, 0) + 1
    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "graph": {"nodes": node_count, "statuses": statuses},
    }


# --- Graph Build ---

def _create_node(spec: NodeSpec):
    """Instantiate an INode from a NodeSpec."""
    if spec.type not in NODE_REGISTRY:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown node type: {spec.type}. "
                   f"Available: {list(NODE_REGISTRY.keys())}"
        )
    cls = NODE_REGISTRY[spec.type]
    try:
        return cls(**spec.config)
    except TypeError as e:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid config for {spec.type}: {e}"
        )


@app.post("/api/dag/build")
async def build_graph(request: GraphBuildRequest):
    """Build a complete graph from a list of node specs.

    Nodes must be in topological order (parents before children).
    Type checking happens at each edge.
    """
    try:
        store_path = os.environ.get("ZEROPOINT_ROOT_PATH", ".")
        db_path = str(Path(store_path) / "data" / "graph.db")

        # Fresh graph
        store = GraphStore(db_path)
        store.clear()
        dag = DAG(store=store)

        for spec in request.nodes:
            node = _create_node(spec)
            dag.add(spec.id, node, parents=spec.parents)

        app.state.store = store
        app.state.dag = dag

        return {
            "ok": True,
            "nodes": len(request.nodes),
            "message": f"Graph built with {len(request.nodes)} nodes"
        }
    except (TypeError, KeyError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to build graph: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/dag/nodes")
async def add_node(spec: NodeSpec):
    """Add a single node to the existing graph."""
    try:
        dag = app.state.dag
        node = _create_node(spec)
        dag.add(spec.id, node, parents=spec.parents)
        return {"ok": True, "node_id": spec.id}
    except (TypeError, KeyError) as e:
        raise HTTPException(status_code=400, detail=str(e))


# --- Resolve ---

@app.post("/api/dag/resolve")
async def resolve_graph(request: ResolveRequest):
    """Resolve the graph in the specified mode.

    Modes:
    - mock: simulated, no side effects (development/testing)
    - dry_run: real verify, no resolve (what would change?)
    - live: real execution with side effects (production)
    """
    try:
        mode_map = {
            "live": ResolveMode.LIVE,
            "dry_run": ResolveMode.DRY_RUN,
            "mock": ResolveMode.MOCK,
        }
        mode = mode_map.get(request.mode)
        if not mode:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid mode: {request.mode}. Use: live, dry_run, mock"
            )

        dag = app.state.dag
        results = dag.resolve(mode=mode)

        nodes = []
        for nid, status in results.items():
            entry = dag.get(nid)
            node_data = {
                "id": nid,
                "type": type(entry.node).__name__,
                "status": status.value,
                "error": entry.error,
            }
            if entry.output and hasattr(entry.output, "__dataclass_fields__"):
                node_data["output"] = asdict(entry.output)
            nodes.append(node_data)

        summary = {}
        for s in results.values():
            summary[s.value] = summary.get(s.value, 0) + 1

        return {
            "ok": all(s in (NodeStatus.SUCCESS, NodeStatus.PENDING_REBOOT)
                      for s in results.values()),
            "mode": request.mode,
            "summary": summary,
            "nodes": nodes,
        }
    except Exception as e:
        logger.error(f"Failed to resolve graph: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# --- Graph Inspection ---

@app.get("/api/dag")
async def get_dag():
    """Get the current graph structure with all nodes and edges."""
    dag = app.state.dag
    nodes = []
    edges = []

    for nid, entry in dag.nodes.items():
        node_data = {
            "id": nid,
            "type": type(entry.node).__name__,
            "status": entry.status.value,
            "config": {k: v for k, v in entry.node.__dict__.items()
                       if not k.startswith("_")},
            "error": entry.error,
        }
        if entry.output and hasattr(entry.output, "__dataclass_fields__"):
            node_data["output"] = asdict(entry.output)
        nodes.append(node_data)

        for parent_id in entry.parents:
            edges.append({"source": parent_id, "target": nid})

    return {"ok": True, "nodes": nodes, "edges": edges}


@app.get("/api/dag/nodes/{node_id}")
async def get_node(node_id: str):
    """Get a specific node's status and output."""
    dag = app.state.dag
    try:
        entry = dag.get(node_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Node not found: {node_id}")

    result = {
        "id": node_id,
        "type": type(entry.node).__name__,
        "status": entry.status.value,
        "config": {k: v for k, v in entry.node.__dict__.items()
                   if not k.startswith("_")},
        "parents": entry.parents,
        "error": entry.error,
    }
    if entry.output and hasattr(entry.output, "__dataclass_fields__"):
        result["output"] = asdict(entry.output)
    return {"ok": True, "node": result}


# --- Boot Status (= DAG status) ---

@app.get("/api/boot/status")
async def boot_status():
    """Boot status is the DAG status.

    Returns the current state of all nodes — which have converged,
    which are pending, which failed.
    """
    dag = app.state.dag
    nodes_by_status = {}
    all_nodes = []

    for nid, entry in dag.nodes.items():
        status = entry.status.value
        nodes_by_status.setdefault(status, []).append(nid)
        all_nodes.append({
            "id": nid,
            "type": type(entry.node).__name__,
            "status": status,
            "error": entry.error,
        })

    total = len(all_nodes)
    success = len(nodes_by_status.get("success", []))
    is_complete = total > 0 and success == total
    needs_reboot = len(nodes_by_status.get("pending_reboot", [])) > 0

    return {
        "is_complete": is_complete,
        "needs_reboot": needs_reboot,
        "total_nodes": total,
        "summary": {k: len(v) for k, v in nodes_by_status.items()},
        "nodes": all_nodes,
    }


# --- Hardware Discovery ---

@app.get("/api/hw/disks")
async def get_disks():
    try:
        disks = HWProbe.get_disks()
        return {
            "ok": True,
            "disks": [
                {
                    "id": d.id, "device": d.device, "size": d.size,
                    "free": d.free, "sector_size": d.sector_size,
                    "partitions": [
                        {"id": p.id, "device": p.device, "size": p.size,
                         "free": p.free, "filesystem": p.filesystem, "flags": p.flags}
                        for p in d.partitions
                    ],
                }
                for d in disks
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/hw/gpus")
async def get_gpus():
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


# --- Static files (WebUI) ---

webui_dist = Path("webui/dist")
if webui_dist.exists():
    app.mount("/", StaticFiles(directory=str(webui_dist), html=True), name="webui")
    logger.info("WebUI mounted at /")
else:
    logger.warning(f"WebUI not found at {webui_dist}, skipping mount")


if __name__ == "__main__":
    import uvicorn
    logger.info("Starting Zeropoint Agent on 0.0.0.0:2370")
    uvicorn.run(app, host="0.0.0.0", port=2370, log_config=None)
