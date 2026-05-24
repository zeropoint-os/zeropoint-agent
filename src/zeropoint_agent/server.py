"""Zeropoint Agent — app factory and startup."""

import os
import sys
import logging
import logging.handlers
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from zeropoint_agent.dag import DAG
from zeropoint_agent.graph_store import GraphStore
from zeropoint_agent.inode import ResolveMode
from zeropoint_agent.handlers import health, dag, query, resolve, mutations, hw, modules, detect, node_types, links


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


def create_app() -> FastAPI:
    """Create and configure the FastAPI application.

    Does NOT configure logging — that's the caller's job (the CLI's
    `serve` does it; in-process callers leave the user's existing
    logging config in place).
    """
    logger = logging.getLogger(__name__)

    app = FastAPI(title="Zeropoint Agent API", version="2.0.0")

    # Register route handlers
    app.include_router(health.router)
    app.include_router(dag.router)
    app.include_router(resolve.router)
    app.include_router(query.router)
    app.include_router(mutations.router)
    app.include_router(hw.router)
    app.include_router(modules.router)
    app.include_router(detect.router)
    app.include_router(node_types.router)
    app.include_router(links.router)

    @app.on_event("startup")
    def _init():
        store_path = os.environ.get("ZEROPOINT_ROOT_PATH", ".")
        data_dir = Path(store_path) / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        db_path = str(data_dir / "graph.db")
        logger.info("Initializing graph store at %s", db_path)
        app.state.store = GraphStore(db_path)
        # DAG.__init__ rehydrates persisted nodes from the store.
        app.state.dag = DAG(store=app.state.store)
        logger.info("Graph store initialized (%d nodes loaded)",
                    len(app.state.dag.nodes))

        # Default resolve mode used by endpoints that don't override.
        mode_str = os.environ.get("ZEROPOINT_MODE", "mock")
        app.state.default_mode = mode_str
        logger.info("Default resolve mode: %s", mode_str)

        webui_dist = Path("webui/dist")
        if webui_dist.exists():
            app.mount("/", StaticFiles(directory=str(webui_dist), html=True), name="webui")
            logger.info("WebUI mounted at /")

    return app


# For uvicorn: `uvicorn zeropoint_agent.server:app`
app = create_app()
