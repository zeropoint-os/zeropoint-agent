"""Lifecycle and CLI adapter for the single ZeroPoint Tailscale runtime."""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from zeropoint_agent.interaction import checkpoint, url_qr

logger = logging.getLogger(__name__)

IMAGE_VERSION = "v1.104.7"
IMAGE_DIGEST = "sha256:72c1e19e0aedca96b8533bbb0b4368564410ff20b17e53b804eb6c1f04a31be4"
IMAGE = f"tailscale/tailscale@{IMAGE_DIGEST}"
CONTAINER_NAME = "zeropoint-tailscale"
PRIVATE_INGRESS_PORT = 18080
ENVOY_GENERATION_LABEL = "zeropoint.envoy-netns-generation"
_LOGIN_URL_RE = re.compile(r"https://login\.tailscale\.com/[A-Za-z0-9/_?=&.-]+")


def _docker_client():
    try:
        import docker  # type: ignore
        return docker.from_env(timeout=10)
    except Exception as error:
        logger.warning("docker client unavailable for Tailscale: %s", error)
        return None


def _state_dir() -> Path:
    root = Path(os.environ.get("ZEROPOINT_ROOT_PATH", ".")).expanduser().resolve()
    path = root / "data" / "tailscale"
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass
    return path


def _ensure_image(client, image: str) -> None:
    try:
        client.images.get(image)
    except Exception:
        logger.info("pulling Tailscale image %s", image)
        client.images.pull(image)


def _decode_output(output: Any) -> str:
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace")
    return str(output or "")


def _exec(container, command: list[str]) -> tuple[int, str]:
    result = container.exec_run(command, stdout=True, stderr=True)
    return int(result.exit_code), _decode_output(result.output).strip()


def _container_status(container) -> dict[str, Any]:
    try:
        code, output = _exec(container, ["tailscale", "status", "--json"])
    except Exception as error:
        code, output = 1, str(error)
    payload: dict[str, Any] = {}
    if code == 0:
        try:
            payload = json.loads(output)
        except (TypeError, ValueError):
            payload = {}

    self_status = payload.get("Self") or {}
    current_tailnet = payload.get("CurrentTailnet") or {}
    backend_state = str(payload.get("BackendState") or "")
    user_id = self_status.get("UserID")
    users = payload.get("User") or {}
    owner = users.get(str(user_id)) or users.get(user_id) or {}
    active = backend_state == "Running"
    authenticated = active and bool(self_status.get("TailscaleIPs"))
    dns_name = (
        str(self_status.get("DNSName") or "").rstrip(".")
        if authenticated
        else ""
    )
    magic_dns_suffix = (
        str(
            current_tailnet.get("MagicDNSSuffix")
            or payload.get("MagicDNSSuffix")
            or ""
        ).rstrip(".")
        if authenticated
        else ""
    )

    # Enrollment URLs are Job interactions, never durable node identity.
    login_url = ""
    error = "" if code == 0 else output
    if authenticated:
        error = ""
        health = "healthy"
    elif backend_state in {"Stopped", "NeedsLogin", "NoState"}:
        health = "enrollment_required"
    elif container.status != "running":
        health = "stopped"
    else:
        health = "starting"

    return {
        "state": container.status,
        "image": container.image.tags[0] if container.image.tags else IMAGE,
        "image_version": IMAGE_VERSION,
        "container_id": str(
            getattr(container, "id", None)
            or getattr(container, "short_id", "")
        ),
        "envoy_generation": str(
            (((getattr(container, "attrs", {}) or {}).get("Config") or {}).get(
                "Labels"
            ) or {}).get(ENVOY_GENERATION_LABEL)
            or ""
        ),
        "authenticated": authenticated,
        "backend_state": backend_state,
        "login_url": login_url,
        "tailnet": (
            str(current_tailnet.get("Name") or "")
            if authenticated
            else ""
        ),
        "magic_dns_suffix": magic_dns_suffix,
        "node_dns_name": dns_name,
        "primary_user_login": (
            str(owner.get("LoginName") or "").strip().lower()
            if authenticated and user_id
            else ""
        ),
        "health": health,
        "error": error,
    }


def runtime_status() -> dict[str, Any]:
    client = _docker_client()
    if client is None:
        return {"state": "unavailable", "health": "error", "error": "docker unavailable"}
    try:
        container = client.containers.get(CONTAINER_NAME)
    except Exception:
        return {"state": "not_running", "health": "stopped"}
    return _container_status(container)


def _envoy_generation(envoy) -> str:
    state = envoy.attrs.get("State") or {}
    pid = int(state.get("Pid") or 0)
    started_at = str(state.get("StartedAt") or "")
    if pid <= 0 or not started_at:
        raise RuntimeError("Envoy runtime generation is unavailable")
    return f"{pid}@{started_at}"


def ensure_runtime(
    machine_name: str,
    envoy: dict[str, Any],
    image: str = IMAGE,
) -> dict[str, Any]:
    """Ensure one persistent tailscaled runtime sharing Envoy loopback."""
    client = _docker_client()
    if client is None:
        return {"state": "unavailable", "health": "error", "error": "docker unavailable"}

    envoy_state = str(envoy.get("state") or "")
    envoy_container_id = str(envoy.get("container_id") or "")
    envoy_generation = str(envoy.get("runtime_generation") or "")
    if envoy_state != "running" or not envoy_container_id or not envoy_generation:
        return {
            "state": "blocked",
            "health": "error",
            "error": "resolved system/envoy runtime contract is incomplete",
        }
    try:
        envoy_container = client.containers.get(envoy_container_id)
        envoy_container.reload()
        actual_generation = _envoy_generation(envoy_container)
    except Exception as error:
        return {
            "state": "blocked",
            "health": "error",
            "error": f"linked Envoy runtime is unavailable: {error}",
        }
    if actual_generation != envoy_generation:
        return {
            "state": "blocked",
            "health": "error",
            "error": "linked Envoy runtime generation changed; resolve system/envoy again",
        }

    try:
        _ensure_image(client, image)
    except Exception as error:
        return {
            "state": "error",
            "health": "error",
            "error": f"failed to pull Tailscale image {image}: {error}",
        }
    desired_network_mode = f"container:{envoy_container_id}"
    try:
        container = client.containers.get(CONTAINER_NAME)
    except Exception:
        container = None

    if container is not None:
        container.reload()
        config = container.attrs.get("Config") or {}
        current_mode = str((container.attrs.get("HostConfig") or {}).get("NetworkMode") or "")
        current_image = str(config.get("Image") or "")
        current_generation = str(
            (config.get("Labels") or {}).get(ENVOY_GENERATION_LABEL) or ""
        )
        if (
            current_mode != desired_network_mode
            or current_image != image
            or current_generation != envoy_generation
        ):
            container.remove(force=True)
            container = None

    if container is None:
        try:
            container = client.containers.run(
                image=image,
                name=CONTAINER_NAME,
                detach=True,
                network_mode=desired_network_mode,
                entrypoint=["/usr/local/bin/tailscaled"],
                command=[
                    "--state=/var/lib/tailscale/tailscaled.state",
                    "--tun=userspace-networking",
                ],
                volumes={
                    str(_state_dir()): {
                        "bind": "/var/lib/tailscale",
                        "mode": "rw",
                    },
                },
                labels={ENVOY_GENERATION_LABEL: envoy_generation},
                restart_policy={"Name": "unless-stopped"},
            )
        except Exception as error:
            logger.exception("failed to create Tailscale runtime")
            return {"state": "error", "health": "error", "error": str(error)}
    elif container.status != "running":
        try:
            container.start()
        except Exception as error:
            return {"state": "error", "health": "error", "error": str(error)}

    container.reload()
    try:
        envoy_container.reload()
        if _envoy_generation(envoy_container) != envoy_generation:
            container.remove(force=True)
            return {
                "state": "blocked",
                "health": "error",
                "error": "Envoy runtime changed during Tailscale reconciliation",
            }
    except Exception as error:
        container.remove(force=True)
        return {
            "state": "blocked",
            "health": "error",
            "error": f"failed to verify linked Envoy runtime: {error}",
        }
    return _container_status(container)


def enrollment_command(machine_name: str) -> list[str]:
    return [
        "tailscale", "up",
        f"--hostname={machine_name}",
        "--accept-dns=false",
        "--qr",
        "--qr-format=small",
    ]


def _enrollment_running(container) -> bool:
    try:
        process_table = container.top()
    except Exception:
        return False
    rows = process_table.get("Processes") or []
    return any("tailscale up" in " ".join(map(str, row)) for row in rows)


def _stop_existing_enrollment(container) -> None:
    if not _enrollment_running(container):
        return
    _exec(container, ["tailscale", "down"])
    deadline = time.monotonic() + 2
    while _enrollment_running(container) and time.monotonic() < deadline:
        time.sleep(0.05)
    if _enrollment_running(container):
        raise RuntimeError("previous Tailscale enrollment command did not stop")


def enroll_interactively(
    context,
    machine_name: str,
    envoy: dict[str, Any],
    image: str = IMAGE,
) -> dict[str, Any]:
    """Stream native QR enrollment into durable Job checkpoints."""
    # Every executor invocation is a fresh attempt. Clear any preserved
    # interaction before inspecting/stopping a stale enrollment process.
    context.progress(0.0, {"phase": "starting"})
    status = ensure_runtime(machine_name, envoy, image)
    if status.get("authenticated"):
        context.progress(1.0, {"phase": "authenticated"})
        return status
    if status.get("state") in {"error", "blocked", "unavailable"}:
        raise RuntimeError(str(status.get("error") or "Tailscale runtime unavailable"))

    client = _docker_client()
    if client is None:
        raise RuntimeError("docker unavailable")
    container = client.containers.get(CONTAINER_NAME)
    _stop_existing_enrollment(container)

    created = client.api.exec_create(
        container.id,
        enrollment_command(machine_name),
        stdout=True,
        stderr=True,
    )
    exec_id = str(created["Id"])
    stream = client.api.exec_start(exec_id, stream=True)
    chunks: queue.Queue[bytes | None] = queue.Queue()

    def read_stream() -> None:
        try:
            for chunk in stream:
                chunks.put(bytes(chunk))
        finally:
            chunks.put(None)

    reader = threading.Thread(target=read_stream, daemon=True)
    reader.start()
    output = ""
    checkpoint_value = ""
    interaction_id = str(uuid.uuid4())
    try:
        while True:
            context.cancellation.raise_if_requested()
            try:
                chunk = chunks.get(timeout=0.2)
            except queue.Empty:
                continue
            if chunk is None:
                break
            output += _decode_output(chunk)
            match = _LOGIN_URL_RE.search(output)
            if match:
                interaction_checkpoint = checkpoint(url_qr(
                    interaction_id=interaction_id,
                    title="Authorize this ZeroPoint machine in Tailscale",
                    message="Open the authorization link or scan the QR code.",
                    url=match.group(0),
                    qr=output[-12000:],
                ))
                encoded = json.dumps(interaction_checkpoint, sort_keys=True)
                if encoded != checkpoint_value:
                    context.progress(0.1, interaction_checkpoint)
                    checkpoint_value = encoded
    except BaseException:
        if context.cancellation.requested:
            try:
                _exec(container, ["tailscale", "down"])
            except Exception:
                logger.warning("failed to stop cancelled Tailscale enrollment")
        raise

    status = _container_status(container)
    if not status.get("authenticated"):
        raise RuntimeError(output.strip() or "Tailscale enrollment exited before authentication")
    context.progress(1.0, {"phase": "authenticated"})
    return status


def remove_runtime() -> dict[str, Any]:
    """Remove the runtime container but intentionally retain its state directory."""
    client = _docker_client()
    if client is None:
        return {"ok": False, "error": "docker unavailable"}
    try:
        container = client.containers.get(CONTAINER_NAME)
    except Exception:
        return {"ok": True}
    try:
        container.remove(force=True)
        return {"ok": True}
    except Exception as error:
        return {"ok": False, "error": str(error)}


def private_route_prefix(https_port: int) -> str:
    return f"/__zeropoint/tailnet/{https_port}/"


def private_target_url(https_port: int) -> str:
    return f"http://127.0.0.1:{PRIVATE_INGRESS_PORT}{private_route_prefix(https_port)}"


def node_serve_url(node_dns_name: str, https_port: int) -> str:
    hostname = node_dns_name.strip().rstrip(".")
    return f"https://{hostname}:{https_port}/" if hostname else ""


def publish_port(https_port: int) -> dict[str, Any]:
    client = _docker_client()
    if client is None:
        return {"ok": False, "error": "docker unavailable"}
    try:
        container = client.containers.get(CONTAINER_NAME)
    except Exception as error:
        return {"ok": False, "error": str(error)}
    try:
        code, output = _exec(container, [
            "tailscale", "serve", "--yes", "--bg",
            f"--https={https_port}",
            private_target_url(https_port),
        ])
    except Exception as error:
        return {"ok": False, "error": str(error)}
    return {"ok": code == 0, "error": "" if code == 0 else output}


def remove_port(https_port: int) -> dict[str, Any]:
    client = _docker_client()
    if client is None:
        return {"ok": False, "error": "docker unavailable"}
    try:
        container = client.containers.get(CONTAINER_NAME)
    except Exception:
        return {"ok": True}
    try:
        code, output = _exec(container, [
            "tailscale", "serve", "--yes", "--bg",
            f"--https={https_port}",
            "off",
        ])
    except Exception as error:
        return {"ok": False, "error": str(error)}
    return {"ok": code == 0, "error": "" if code == 0 else output}
