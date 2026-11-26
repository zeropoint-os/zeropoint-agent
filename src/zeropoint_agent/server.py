"""Zeropoint Agent — app factory and startup."""

import asyncio
import os
import platform
import sys
import logging
import logging.handlers
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from zeropoint_agent.agent_discovery import AgentAdvertiser
from zeropoint_agent.dag import DAG
from zeropoint_agent.graph_transaction import graph_transaction
from zeropoint_agent.graph_runner import GraphRunner
from zeropoint_agent.hardware_monitor import HardwareMonitor
from zeropoint_agent.jobs import JobManager
from zeropoint_agent.nodes.core.shell_script import (
    execute_shell_script_job,
    execute_shell_script_verify_job,
)
from zeropoint_agent.nodes.system.docker import SystemDocker
from zeropoint_agent.nodes.system.dockernetwork import DockerNetwork
from zeropoint_agent.nodes.system.envoy import SystemEnvoy
from zeropoint_agent.nodes.system.reboot import Reboot, execute_reboot_job
from zeropoint_agent.nodes.config.namespace import Namespace
from zeropoint_agent.nodes.config.directory import execute_directory_move_job
from zeropoint_agent.nodes.config.var import Var
from zeropoint_agent.nodes.hw.disks import Disks
from zeropoint_agent.nodes.hw.inventory import HardwareInventory
from zeropoint_agent.nodes.hw.format import (
    execute_format_job,
    execute_format_remove_job,
)
from zeropoint_agent.nodes.hw.mount import (
    execute_mount_job,
    execute_mount_remove_job,
)
from zeropoint_agent.nodes.hw.mountdir import execute_mountdir_job
from zeropoint_agent.nodes.hw.partition import (
    execute_partition_job,
    execute_partition_remove_job,
)
from zeropoint_agent.nodes.user.service import Service
from zeropoint_agent.integrations.tailscale.nodes import (
    SystemTailscale,
    TailnetExposure,
)
from zeropoint_agent.integrations.tailscale.jobs import execute_tailscale_enroll_job
from zeropoint_agent.query import _get_all_descendants
from zeropoint_agent.nodes.user.module import (
    Terraform,
    execute_terraform_destroy_job,
    execute_terraform_job,
)
from zeropoint_agent.nodes.user.module_definition import (
    MODULE_JOB_CONTROLS,
    execute_module_prepare_job,
)
from zeropoint_agent.promise_controls import (
    JobCancellation,
    JobCheckpoint,
    JobProgress,
    ShellScriptCancellation,
    ShellScriptProgress,
    HardwareCancellation,
    HardwareProgress,
    TerraformCancellation,
    TerraformProgress,
)
from zeropoint_agent.sqlite_store import SQLiteGraphStore
from zeropoint_agent.identity import load_or_create_identity
from zeropoint_agent.inode import ResolveMode
from zeropoint_agent.handlers import health, dag, query, resolve, mutations, hw, modules, detect, node_types, links, expose, tailscale, jobs, runs, meta, events
from zeropoint_agent.xds.runner import XdsRunner


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

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await _init()
        try:
            yield
        finally:
            await _shutdown()

    app = FastAPI(
        title="Zeropoint Agent API",
        version="2.0.0",
        lifespan=lifespan,
    )
    app.state.api_port = int(os.environ.get("ZEROPOINT_AGENT_PORT", "2370"))

    # Register route handlers
    app.include_router(health.router)
    graph_routers = (dag.router, resolve.router, query.router, mutations.router)
    for graph_router in graph_routers:
        app.include_router(graph_router, prefix="/api/graph")
    app.include_router(hw.router)
    app.include_router(modules.router)
    app.include_router(detect.router)
    app.include_router(node_types.router)
    app.include_router(links.router)
    app.include_router(expose.router)
    app.include_router(tailscale.router)
    app.include_router(jobs.router)
    app.include_router(runs.router)
    app.include_router(meta.router)
    app.include_router(events.router)

    async def _init():
        store_path = os.environ.get("ZEROPOINT_ROOT_PATH", ".")
        data_dir = Path(store_path) / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        db_path = str(data_dir / "state.db")
        logger.info("Initializing SQLite graph store at %s", db_path)
        app.state.store = SQLiteGraphStore(db_path)
        app.state.identity = load_or_create_identity(app.state.store)
        # DAG.__init__ rehydrates persisted nodes from the store.
        app.state.dag = DAG(store=app.state.store)
        app.state.dag.machine_name = app.state.identity.name
        if os.environ.get("ZEROPOINT_SEED_ROOTS") == "1":
            with graph_transaction(app.state.dag):
                root_specs = (
                    ("settings", Namespace(name="settings"), [], "rw*"),
                    ("modules", Namespace(name="modules"), [], "rw*"),
                    ("system", Namespace(name="system"), [], "rw-"),
                    (
                        "system/reboot",
                        Reboot(),
                        ["system"],
                        "r--",
                    ),
                    (
                        "system/docker",
                        SystemDocker(),
                        ["system"],
                        "r--",
                    ),
                    (
                        "system/zeropoint_network",
                        DockerNetwork(
                            name="zeropoint-network",
                            driver="bridge",
                        ),
                        ["system", "system/docker"],
                        "r--",
                    ),
                    (
                        "system/envoy",
                        SystemEnvoy(
                            xds_port=int(
                                os.environ.get(
                                    "ZEROPOINT_XDS_PORT",
                                    "18000",
                                )
                            ),
                        ),
                        [
                            "system",
                            "system/docker",
                            "system/zeropoint_network",
                        ],
                        "r--",
                    ),
                    ("hardware", Namespace(name="hardware"), [], "rwd"),
                    (
                        "hardware/disks",
                        Disks(),
                        ["hardware"],
                        "r--",
                    ),
                    *(
                        (
                            f"hardware/{kind}s"
                            if kind != "system"
                            else "hardware/system",
                            HardwareInventory(kind=kind),
                            ["hardware"],
                            "r--",
                        )
                        for kind in ("gpu", "network", "usb", "pci", "system")
                    ),
                    (
                        "settings/zp_arch",
                        Var(
                            name="zp_arch",
                            value={
                                "x86_64": "amd64",
                                "aarch64": "arm64",
                            }.get(platform.machine(), platform.machine()),
                        ),
                        ["settings"],
                        "r--",
                    ),
                    (
                        "settings/zp_gpu_vendor",
                        Var(name="zp_gpu_vendor", value=""),
                        ["settings"],
                        "r--",
                    ),
                )
                for node_id, node, parents, perms in root_specs:
                    if node_id not in app.state.dag.nodes:
                        app.state.dag.add(
                            node_id,
                            node,
                            parents=parents,
                            perms=perms,
                        )
        app.state.jobs = JobManager(app.state.store)
        app.state.jobs.register_executor(
            "node.shell_script",
            execute_shell_script_job,
            cancellation=ShellScriptCancellation,
            progress=ShellScriptProgress,
        )
        app.state.jobs.register_executor(
            "node.shell_script.verify",
            execute_shell_script_verify_job,
            cancellation=ShellScriptCancellation,
            progress=ShellScriptProgress,
        )
        app.state.jobs.register_executor(
            "node.terraform",
            execute_terraform_job,
            cancellation=TerraformCancellation,
            progress=TerraformProgress,
        )
        app.state.jobs.register_executor(
            "node.terraform.destroy",
            execute_terraform_destroy_job,
            cancellation=TerraformCancellation,
            progress=TerraformProgress,
        )
        app.state.jobs.register_executor(
            "node.module.prepare",
            execute_module_prepare_job,
            cancellation=MODULE_JOB_CONTROLS["cancellation"],
            pausing=MODULE_JOB_CONTROLS["pausing"],
            progress=MODULE_JOB_CONTROLS["progress"],
            checkpoint=MODULE_JOB_CONTROLS["checkpoint"],
        )
        for kind, executor in (
            ("tailscale.enroll", execute_tailscale_enroll_job),
        ):
            app.state.jobs.register_executor(
                kind,
                executor,
                cancellation=JobCancellation,
                progress=JobProgress,
                checkpoint=JobCheckpoint,
            )
        for kind, executor in (
            ("node.partition", execute_partition_job),
            ("node.partition.remove", execute_partition_remove_job),
            ("node.format", execute_format_job),
            ("node.format.remove", execute_format_remove_job),
            ("node.mount", execute_mount_job),
            ("node.mount.remove", execute_mount_remove_job),
            ("node.mountdir", execute_mountdir_job),
            ("node.directory.move", execute_directory_move_job),
            ("system.reboot", execute_reboot_job),
        ):
            app.state.jobs.register_executor(
                kind,
                executor,
                cancellation=HardwareCancellation,
                progress=HardwareProgress,
            )
        app.state.graph_runner = GraphRunner(
            app.state.dag, app.state.store, app.state.jobs
        )
        event_loop = asyncio.get_running_loop()
        app.state.jobs.set_promise_callback(
            lambda run_id, promise_id: event_loop.call_soon_threadsafe(
                app.state.graph_runner.on_promise_terminal,
                run_id,
                promise_id,
            )
        )
        logger.info("Graph store initialized (%d nodes loaded)",
                    len(app.state.dag.nodes))

        # Default resolve mode used by endpoints that don't override.
        mode_str = os.environ.get("ZEROPOINT_MODE", "mock")
        app.state.default_mode = mode_str
        logger.info("Default resolve mode: %s", mode_str)

        # xDS server: runs in every mode. In mock mode SystemEnvoy.resolve
        # returns mock state without touching docker; in live mode it
        # ensures the actual container is up.
        app.state.xds = None
        xds_port = int(os.environ.get("ZEROPOINT_XDS_PORT", "18000"))
        try:
            runner = XdsRunner(port=xds_port)
            await runner.start()
            runner.attach_to_dag(app.state.dag)
            app.state.xds = runner
        except Exception as e:
            logger.warning("xDS server start failed (continuing without): %s", e)

        app.state.jobs.start()
        app.state.graph_runner.resume_incomplete()

        # Runtime sinks, live hardware roots and local Terraform modules need
        # process-local reconciliation after every start. Docker restart
        # policies are not a sufficient source of truth: containers can remain
        # stopped after host shutdown or storage-mount ordering changes.
        runtime_types = (Disks, HardwareInventory, Reboot)
        if app.state.xds is not None:
            runtime_types += (
                SystemEnvoy,
                Service,
                SystemTailscale,
                TailnetExposure,
            )
        runtime_node_set = {
            node_id
            for node_id, entry in app.state.dag.nodes.items()
            if isinstance(entry.node, runtime_types)
            and not (
                isinstance(entry.node, Reboot)
                and not entry.node.request_id
            )
        }
        for node_id, entry in app.state.dag.nodes.items():
            if not isinstance(entry.node, Terraform):
                continue
            runtime_node_set.add(node_id)
            runtime_node_set.update(
                _get_all_descendants(app.state.dag, node_id)
            )
        pending = list(runtime_node_set)
        while pending:
            node_id = pending.pop()
            for parent_id in app.state.dag.get(node_id).parents:
                if parent_id in runtime_node_set:
                    continue
                parent = app.state.dag.get(parent_id)
                if parent.status.value in {"success", "success_skip"}:
                    continue
                runtime_node_set.add(parent_id)
                pending.append(parent_id)
        runtime_nodes = [
            node_id for node_id in app.state.dag._order
            if node_id in runtime_node_set
        ]
        if runtime_nodes:
            try:
                app.state.startup_run = app.state.graph_runner.start(
                    ResolveMode(mode_str),
                    node_ids=runtime_nodes,
                    pattern="startup-runtime",
                )
            except Exception as e:
                logger.warning(
                    "startup runtime reconciliation failed: %s", e
                )

        def _refresh_hardware() -> None:
            hardware_nodes = [
                node_id
                for node_id, entry in app.state.dag.nodes.items()
                if isinstance(entry.node, (Disks, HardwareInventory))
            ]
            if not hardware_nodes:
                return
            for run_id in app.state.store.list_incomplete_graph_runs():
                running_nodes = {
                    str(row["node_id"])
                    for row in app.state.store.get_graph_run_nodes(run_id)
                }
                if any(
                    node_id == "hardware"
                    or node_id.startswith("hardware/")
                    for node_id in running_nodes
                ):
                    return
            app.state.graph_runner.start(
                ResolveMode(app.state.default_mode),
                node_ids=hardware_nodes,
                pattern="hardware-refresh",
            )

        app.state.hardware_monitor = HardwareMonitor(
            lambda: event_loop.call_soon_threadsafe(_refresh_hardware),
            interval_seconds=float(
                os.environ.get(
                    "ZEROPOINT_HARDWARE_REFRESH_SECONDS",
                    "300",
                )
            ),
        )
        app.state.hardware_monitor.start()

        app.state.advertiser = None
        if os.environ.get("ZEROPOINT_MDNS_ADVERTISE", "1") != "0":
            advertiser = AgentAdvertiser(
                app.state.identity,
                app.state.api_port,
            )
            if await asyncio.to_thread(advertiser.start):
                app.state.advertiser = advertiser

    async def _shutdown():
        monitor = getattr(app.state, "hardware_monitor", None)
        if monitor is not None:
            monitor.stop()
        advertiser = getattr(app.state, "advertiser", None)
        if advertiser is not None:
            await asyncio.to_thread(advertiser.stop)
        jobs = getattr(app.state, "jobs", None)
        if jobs is not None:
            jobs.stop()
        runner = getattr(app.state, "xds", None)
        if runner is not None:
            await runner.stop()
        try:
            from zeropoint_agent.mdns import close_registry
            await asyncio.to_thread(close_registry)
        except Exception:
            pass
        mode_str = getattr(app.state, "default_mode", "mock")
        if mode_str == "live":
            try:
                from zeropoint_agent.envoy_manager import stop_envoy
                stop_envoy()
            except Exception as e:
                logger.warning("envoy stop failed: %s", e)
        store = getattr(app.state, "store", None)
        if store is not None:
            store.close()

    return app


# For uvicorn: `uvicorn zeropoint_agent.server:app`
app = create_app()
