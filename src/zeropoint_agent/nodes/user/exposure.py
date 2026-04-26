"""ExposureNode — exposes a module's port via Envoy reverse proxy."""

import logging
from dataclasses import dataclass
from typing import Optional

from zeropoint_agent.inode import INode
from zeropoint_agent.nodes.user.module import ModuleResult

logger = logging.getLogger(__name__)


@dataclass
class ExposureResult:
    """Contract for an exposure node (Envoy xDS route)."""
    module_id: str
    port: int
    protocol: str = "http"
    path_prefix: str = "/"
    route_name: Optional[str] = None
    external_url: Optional[str] = None
    description: Optional[str] = None


class ExposureNode(INode[ModuleResult, ExposureResult]):
    """Exposes a module's port via Envoy reverse proxy. In-process."""

    def __init__(self, module_id: str, port: int,
                 protocol: str = "http", path_prefix: str = "/",
                 description: Optional[str] = None):
        self.module_id = module_id
        self.port = port
        self.protocol = protocol
        self.path_prefix = path_prefix
        self.description = description

    def resolve(self, input: ModuleResult) -> ExposureResult:
        logger.info(f"ExposureNode.resolve() — exposing {self.module_id}:{self.port}")
        return ExposureResult(module_id=self.module_id, port=self.port,
                              protocol=self.protocol, path_prefix=self.path_prefix,
                              description=self.description)

    def mock_resolve(self, input: ModuleResult) -> ExposureResult:
        return ExposureResult(
            module_id=self.module_id, port=self.port,
            protocol=self.protocol, path_prefix=self.path_prefix,
            description=self.description,
            route_name=f"mock-route-{self.module_id}",
            external_url=f"http://localhost/{self.module_id}",
        )

    def verify(self) -> bool:
        return False

    def remove(self) -> bool:
        return True
