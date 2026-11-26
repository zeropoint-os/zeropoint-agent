"""Graph nodes for one machine Tailscale runtime and per-app Serve ports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar, Optional

from zeropoint_agent.inode import INode, NodeResult, ResolveMode, readonly
from zeropoint_agent.jobs import JobSpec
from zeropoint_agent.promise import Promise
from zeropoint_agent.nodes.system.envoy import SystemEnvoyResult
from zeropoint_agent.nodes.user.exposure import Exposure, canonical_local_hostname
from zeropoint_agent.nodes.user.service import Service
from zeropoint_agent.xds.snapshot import TailnetRoute

from . import manager

SYSTEM_TAILSCALE_NODE_ID = "system/tailscale"
ENROLLMENT_MAX_ATTEMPTS = 32
TAILNET_HTTPS_PORT_MIN = 10000
TAILNET_HTTPS_PORT_MAX = 10999


def normalize_user_logins(values: list[str]) -> list[str]:
    """Normalize configured Tailscale login names without accepting empties."""
    normalized: set[str] = set()
    for value in values:
        login = str(value).strip().lower()
        if not login or any(ord(char) < 32 for char in login):
            raise ValueError(f"invalid Tailscale user login: {value!r}")
        normalized.add(login)
    return sorted(normalized)


def effective_allowed_users(
    primary_user_login: str,
    global_allowed_users: list[str],
    exposure_allowed_users: list[str],
    exposure_denied_users: list[str],
) -> list[str]:
    """Owner is non-deniable; deny wins for every other identity."""
    owner = primary_user_login.strip().lower()
    inherited = set(normalize_user_logins(global_allowed_users))
    inherited.update(normalize_user_logins(exposure_allowed_users))
    denied = set(normalize_user_logins(exposure_denied_users))
    effective = inherited - denied
    if owner:
        effective.add(owner)
    return sorted(effective)


@dataclass
class SystemTailscaleResult:
    state: str = "unknown"
    image: str = ""
    image_version: str = manager.IMAGE_VERSION
    container_id: str = ""
    envoy_generation: str = ""
    authenticated: bool = False
    backend_state: str = ""
    tailnet: str = ""
    magic_dns_suffix: str = ""
    node_dns_name: str = ""
    primary_user_login: str = ""
    allowed_users: list[str] | None = None
    health: str = "unknown"
    error: str = ""


def system_tailscale_result(
    status: dict[str, Any],
    image: str = manager.IMAGE,
) -> SystemTailscaleResult:
    authenticated = bool(status.get("authenticated"))
    return SystemTailscaleResult(
        state=str(status.get("state") or "unknown"),
        image=str(status.get("image") or image),
        image_version=str(status.get("image_version") or manager.IMAGE_VERSION),
        container_id=str(status.get("container_id") or ""),
        envoy_generation=str(status.get("envoy_generation") or ""),
        authenticated=authenticated,
        backend_state=str(status.get("backend_state") or ""),
        tailnet=str(status.get("tailnet") or ""),
        magic_dns_suffix=str(status.get("magic_dns_suffix") or ""),
        node_dns_name=str(status.get("node_dns_name") or ""),
        primary_user_login=str(status.get("primary_user_login") or ""),
        health=str(status.get("health") or "unknown"),
        error="" if authenticated else str(status.get("error") or ""),
    )


@dataclass
class SystemTailscale(INode[Any, SystemTailscaleResult]):
    """The optional machine-wide Tailscale device and persistent runtime."""

    default_perms: ClassVar[str] = "rw-"
    image: str = readonly(default=manager.IMAGE)
    allowed_users: list[str] | None = None

    def _result(self, status: dict[str, Any]) -> SystemTailscaleResult:
        result = system_tailscale_result(status, self.image)
        result.allowed_users = normalize_user_logins(self.allowed_users or [])
        return result

    @staticmethod
    def _envoy_output(input: Any) -> Optional[SystemEnvoyResult]:
        if isinstance(input, SystemEnvoyResult):
            return input
        if isinstance(input, dict):
            for value in input.values():
                if isinstance(value, SystemEnvoyResult):
                    return value
        return None

    @staticmethod
    def _envoy_contract(result: SystemEnvoyResult) -> dict[str, Any]:
        return {
            "state": result.state,
            "container_id": result.container_id,
            "runtime_generation": result.runtime_generation,
        }

    def resolve(self, input: Any, mode: ResolveMode) -> NodeResult[SystemTailscaleResult]:
        machine_name = str(getattr(self.dag, "machine_name", ""))
        if mode == ResolveMode.MOCK:
            return NodeResult.success(SystemTailscaleResult(
                state="mock",
                image=self.image,
                image_version=manager.IMAGE_VERSION,
                container_id="mock-tailscale",
                envoy_generation="mock-generation",
                authenticated=True,
                backend_state="Running",
                tailnet="example",
                magic_dns_suffix="example.ts.net",
                node_dns_name=f"{machine_name}.example.ts.net",
                primary_user_login="owner@example.com",
                allowed_users=normalize_user_logins(self.allowed_users or []),
                health="healthy",
            ))
        if mode == ResolveMode.DRY_RUN:
            return NodeResult.success(self._result(manager.runtime_status()))
        envoy = self._envoy_output(input)
        if envoy is None:
            return NodeResult.failed("system/tailscale requires resolved system/envoy")
        result = self._result(manager.ensure_runtime(
            machine_name,
            self._envoy_contract(envoy),
            self.image,
        ))
        if result.state in {"error", "blocked", "unavailable"}:
            return NodeResult.failed(result.error or "Tailscale runtime unavailable", result)
        return NodeResult.success(result)

    def resolve_promise(self, input: Any, mode: ResolveMode, context):
        if mode != ResolveMode.LIVE:
            return super().resolve_promise(input, mode, context)
        machine_name = str(getattr(self.dag, "machine_name", ""))
        envoy = self._envoy_output(input)
        if envoy is None:
            return Promise.resolved(NodeResult.failed(
                "system/tailscale requires resolved system/envoy"
            ))
        envoy_contract = self._envoy_contract(envoy)
        result = self._result(manager.ensure_runtime(
            machine_name,
            envoy_contract,
            self.image,
        ))
        if result.state in {"error", "blocked", "unavailable"}:
            return Promise.resolved(NodeResult.failed(
                result.error or "Tailscale runtime unavailable",
                result,
            ))
        if result.authenticated:
            return Promise.resolved(NodeResult.success(result))
        return context.jobs.submit(
            context,
            JobSpec(
                kind="tailscale.enroll",
                arguments={
                    "machine_name": machine_name,
                    "image": self.image,
                    "envoy": envoy_contract,
                    "allowed_users": normalize_user_logins(self.allowed_users or []),
                },
                resources=["tailscale:enrollment"],
                max_attempts=ENROLLMENT_MAX_ATTEMPTS,
            ),
        )

    def verify(self, mode: ResolveMode) -> NodeResult[SystemTailscaleResult]:
        if mode == ResolveMode.MOCK:
            return self.resolve(None, mode)
        return NodeResult.pending_reboot(self._result(manager.runtime_status()))

    def remove(self, mode: ResolveMode) -> NodeResult[SystemTailscaleResult]:
        if mode != ResolveMode.LIVE:
            return NodeResult.success()
        outcome = manager.remove_runtime()
        if outcome.get("ok"):
            return NodeResult.success()
        return NodeResult.failed(str(outcome.get("error") or "failed to remove Tailscale runtime"))


@dataclass
class TailnetExposureResult:
    slug: str
    node_hostname: str
    https_port: int
    local_url: str = ""
    url: str = ""
    private_target: str = ""
    publication_status: str = "pending"
    effective_allowed_users: list[str] | None = None
    healthy: bool = False
    error: str = ""


@dataclass
class TailnetExposure(INode[Any, TailnetExposureResult]):
    """One ordinary Tailscale Serve port layered over a local Exposure."""

    default_perms: ClassVar[str] = "rw-"
    slug: str
    https_port: int = readonly()
    allowed_users: list[str] | None = None
    denied_users: list[str] | None = None

    def _parents(self) -> tuple[Optional[Exposure], Optional[SystemTailscale], str]:
        if self.dag is None or not self.id:
            return None, None, ""
        exposure = None
        root = None
        service_id = ""
        for parent_id in self.dag.get(self.id).parents:
            entry = self.dag.nodes.get(parent_id)
            if entry is None:
                continue
            if isinstance(entry.node, Exposure):
                exposure = entry.node
                for candidate in entry.parents:
                    candidate_entry = self.dag.nodes.get(candidate)
                    if candidate_entry is not None and isinstance(candidate_entry.node, Service):
                        service_id = candidate
                        break
            elif isinstance(entry.node, SystemTailscale):
                root = entry.node
        return exposure, root, service_id

    @staticmethod
    def _root_output(input: Any) -> Optional[SystemTailscaleResult]:
        if isinstance(input, SystemTailscaleResult):
            return input
        if isinstance(input, dict):
            for value in input.values():
                if isinstance(value, SystemTailscaleResult):
                    return value
        return None

    def _output(
        self,
        root: SystemTailscaleResult,
        *,
        status: str,
        healthy: bool,
        error: str = "",
    ) -> TailnetExposureResult:
        machine_name = str(getattr(self.dag, "machine_name", ""))
        allowed_users = effective_allowed_users(
            root.primary_user_login,
            root.allowed_users or [],
            self.allowed_users or [],
            self.denied_users or [],
        )
        return TailnetExposureResult(
            slug=self.slug,
            node_hostname=root.node_dns_name,
            https_port=self.https_port,
            local_url=f"http://{canonical_local_hostname(self.slug, machine_name)}/",
            url=manager.node_serve_url(root.node_dns_name, self.https_port),
            private_target=manager.private_target_url(self.https_port),
            publication_status=status,
            effective_allowed_users=allowed_users,
            healthy=healthy,
            error=error,
        )

    def _sync_private_route(
        self,
        service_id: str,
        root: SystemTailscaleResult,
    ) -> None:
        cache = getattr(self.dag, "xds_cache", None)
        if cache is not None:
            cache.set_tailnet_route(
                self.id,
                TailnetRoute(
                    id=self.id,
                    endpoint_id=service_id,
                    path_prefix=manager.private_route_prefix(self.https_port),
                    allowed_users=tuple(effective_allowed_users(
                        root.primary_user_login,
                        root.allowed_users or [],
                        self.allowed_users or [],
                        self.denied_users or [],
                    )),
                ),
            )

    def resolve(self, input: Any, mode: ResolveMode) -> NodeResult[TailnetExposureResult]:
        exposure, root_node, service_id = self._parents()
        root = self._root_output(input)
        if exposure is None or root_node is None or not service_id or root is None:
            return NodeResult.failed(
                "Tailnet Exposure requires a resolved local Exposure and system/tailscale"
            )
        self._sync_private_route(service_id, root)
        if mode == ResolveMode.MOCK:
            return NodeResult.success(self._output(root, status="published", healthy=True))
        if mode == ResolveMode.DRY_RUN:
            return NodeResult.success(self._output(root, status="dry_run", healthy=False))
        if not root.authenticated:
            return NodeResult.failed("Tailscale device authentication is required")
        published = manager.publish_port(self.https_port)
        output = self._output(
            root,
            status="published" if published.get("ok") else "error",
            healthy=bool(published.get("ok")),
            error=str(published.get("error") or ""),
        )
        return NodeResult.success(output) if published.get("ok") else NodeResult.failed(output.error, output)

    def verify(self, mode: ResolveMode) -> NodeResult[TailnetExposureResult]:
        return NodeResult.pending_reboot(TailnetExposureResult(
            slug=self.slug,
            node_hostname="",
            https_port=self.https_port,
        ))

    def remove(self, mode: ResolveMode) -> NodeResult[TailnetExposureResult]:
        cache = getattr(self.dag, "xds_cache", None) if self.dag is not None else None
        if cache is not None and self.id:
            cache.clear_tailnet_route(self.id)
        if mode != ResolveMode.LIVE:
            return NodeResult.success()
        outcome = manager.remove_port(self.https_port)
        if outcome.get("ok"):
            return NodeResult.success()
        return NodeResult.failed(str(outcome.get("error") or "failed to remove Tailscale Serve port"))
