"""Module — a Promise-backed producer of a Terraform module subgraph."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict

from zeropoint_agent.inode import NodeResult, NodeStatus, ResolveMode
from zeropoint_agent.jobs import JobContext, JobSpec
from zeropoint_agent.module_installer import build_module_child_plan
from zeropoint_agent.nodes.config.namespace import (
    Namespace,
    NamespaceResult,
    _inherited_path,
)
from zeropoint_agent.nodes.config.var import Var
from zeropoint_agent.promise_controls import (
    TerraformCancellation,
    TerraformProgress,
    UnsupportedCheckpoint,
    UnsupportedPause,
)

logger = logging.getLogger(__name__)


@dataclass
class Module(Namespace):
    """A pinned module source whose resolve produces its complete child graph."""

    source: str = ""
    overrides: Dict[str, str] = field(default_factory=dict)

    def _namespace_result(self, input_value: Any) -> NamespaceResult:
        inherited = _inherited_path(input_value)
        path = f"{inherited}/{self.name}" if inherited else self.name
        return NamespaceResult(name=self.name, path=path)

    def _global_vars(self) -> Dict[str, str]:
        if self.dag is None:
            return {}
        result: Dict[str, str] = {}
        for node_id, entry in self.dag.nodes.items():
            if node_id.startswith(f"{self.id}/"):
                continue
            if isinstance(entry.node, Var):
                result[entry.node.name] = node_id
        return result

    def resolve(self, input: Any, mode: ResolveMode) -> NodeResult[NamespaceResult]:
        parent_namespace = self.id.rpartition("/")[0] or "modules"
        plan = build_module_child_plan(
            self.name,
            self.source,
            overrides=self.overrides,
            parent_namespace=parent_namespace,
            global_by_name=self._global_vars(),
        )
        result = NodeResult.success(self._namespace_result(input))
        result.produced_nodes.extend(plan.specs)
        return result

    def resolve_promise(self, input: Any, mode: ResolveMode, context):
        if mode != ResolveMode.LIVE:
            return super().resolve_promise(input, mode, context)
        namespace = self._namespace_result(input)
        parent_namespace = self.id.rpartition("/")[0] or "modules"
        return context.jobs.submit(
            context,
            JobSpec(
                kind="node.module.prepare",
                arguments={
                    "module_id": self.name,
                    "source": self.source,
                    "overrides": dict(self.overrides),
                    "parent_namespace": parent_namespace,
                    "global_by_name": self._global_vars(),
                    "namespace": {
                        "name": namespace.name,
                        "path": namespace.path,
                    },
                },
                resources=[f"module-definition:{self.id}"],
                max_attempts=2,
            ),
        )

    def verify(self, mode: ResolveMode) -> NodeResult[NamespaceResult]:
        if self.dag is not None and self.id:
            entry = self.dag.get(self.id)
            terraform_id = f"{self.id}/terraform"
            terraform = self.dag.nodes.get(terraform_id)
            if (
                isinstance(entry.output, NamespaceResult)
                and terraform is not None
                and terraform.producer_id == self.id
            ):
                return NodeResult.success(entry.output)
        result = NodeResult()
        result.status = NodeStatus.PENDING
        return result


def execute_module_prepare_job(
    context: JobContext,
    arguments: Dict[str, Any],
) -> NodeResult[NamespaceResult]:
    """Inspect a pinned module and return its generated child definitions."""
    try:
        context.progress(0.05, {"phase": "fetch"})
        context.cancellation.raise_if_requested()
        plan = build_module_child_plan(
            str(arguments["module_id"]),
            str(arguments["source"]),
            overrides={
                str(key): str(value)
                for key, value in dict(arguments.get("overrides") or {}).items()
            },
            parent_namespace=str(arguments["parent_namespace"]),
            global_by_name={
                str(key): str(value)
                for key, value in dict(
                    arguments.get("global_by_name") or {}
                ).items()
            },
        )
        context.cancellation.raise_if_requested()
        context.progress(0.9, {"phase": "produce_graph"})
        namespace = dict(arguments["namespace"])
        result = NodeResult.success(NamespaceResult(
            name=str(namespace["name"]),
            path=str(namespace["path"]),
        ))
        result.produced_nodes.extend(plan.specs)
        context.progress(1.0, {"phase": "complete"})
        return result
    except Exception as error:
        logger.exception("module preparation failed")
        return NodeResult.failed(str(error))


MODULE_JOB_CONTROLS = {
    "cancellation": TerraformCancellation,
    "pausing": UnsupportedPause,
    "progress": TerraformProgress,
    "checkpoint": UnsupportedCheckpoint,
}
