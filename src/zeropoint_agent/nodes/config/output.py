"""OutputVar — a Var whose value comes from a parent's `outputs` dict.

Installer-created. For every terraform output, the module installer
creates one `OutputVar` parented to the Terraform, so each output
becomes a first-class `Var[T]` in the graph — pickable, linkable,
inspectable.

Not specific to terraform; any node whose result has an `outputs`
dict (or whose rehydrated dict form has an "outputs" key) is a
legitimate parent for an OutputVar.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Optional, TypeVar

from zeropoint_agent.inode import ResolveMode, NodeResult
from zeropoint_agent.nodes.config.var import Var, VarResult

logger = logging.getLogger(__name__)


T = TypeVar("T")


def _read_output(input_val: Any, key: str) -> Optional[Any]:
    """Pull a single named entry out of a parent's `outputs` dict.

    Handles both runtime dataclass form (attr access) and rehydrated
    dict form. Complex values (dict/list) are JSON-encoded so they
    can flow as strings through tfvars; primitive values are returned
    as-is (caller stringifies when needed).

    `key` may be dotted (``"main_ports.placeholder.port"``) to descend
    into nested dicts. Each segment looks up by key; numeric segments
    index into lists.
    """
    def _descend(value: Any, parts: list[str]) -> Optional[Any]:
        for part in parts:
            if isinstance(value, dict):
                if part not in value:
                    return None
                value = value[part]
            elif isinstance(value, list):
                try:
                    value = value[int(part)]
                except (ValueError, IndexError):
                    return None
            else:
                return None
        return value

    def _from_outputs(holder: Any) -> Optional[Any]:
        # Dataclass form (live runtime).
        outputs = getattr(holder, "outputs", None)
        # Dict form (rehydrated from store via asdict()).
        if outputs is None and isinstance(holder, dict):
            outputs = holder.get("outputs")
        if not isinstance(outputs, dict):
            return None
        parts = key.split(".")
        head = parts[0]
        if head not in outputs:
            return None
        val = outputs[head]
        if len(parts) > 1:
            val = _descend(val, parts[1:])
            if val is None:
                return None
        if isinstance(val, (dict, list)):
            return json.dumps(val)
        if val is None:
            return ""
        return val

    direct = _from_outputs(input_val)
    if direct is not None:
        return direct
    if isinstance(input_val, dict):
        for v in input_val.values():
            got = _from_outputs(v)
            if got is not None:
                return got
    return None


@dataclass
class OutputVar(Var[T]):
    """Var whose value is read from a parent's outputs dict."""

    key: str = ""

    def resolve(self, input: Any, mode: ResolveMode) -> NodeResult[VarResult[T]]:
        val = _read_output(input, self.key)
        if val is None:
            if mode == ResolveMode.MOCK:
                # Mocks can't know every module's specific outputs.
                # Emit a synthetic placeholder so downstream consumers
                # can still resolve in test/dev.
                return NodeResult.success(VarResult(
                    name=self.name,
                    value=f"mock-output-{self.key}"))  # type: ignore[arg-type]
            return NodeResult.failed(
                f"OutputVar {self.name} (key={self.key!r}) needs a parent "
                f"providing an 'outputs' dict containing that key")
        # Strings are still the wire form into terraform; primitive cast
        # happens here so the VarResult is honest.
        if not isinstance(val, str):
            val = str(val)
        result = NodeResult.success(VarResult(name=self.name, value=val))  # type: ignore[arg-type]

        # Ensure Service children for any {port, protocol} bundles in our value.
        # This is "I know my own shape, I create the nodes that follow from it" —
        # no external discover sweep needed.
        self._ensure_service_children(val, mode)
        return result

    def _ensure_service_children(self, raw_value: Any, mode: ResolveMode) -> None:
        """Walk our value for {port, protocol} bundles; place a Service per bundle.

        Idempotent: re-runs of resolve don't create duplicates. New services
        get resolved in the same pass so they're not left PENDING.

        Removal is NOT done here. If a bundle disappears, the existing
        Service stays and surfaces its own resolve error — preserving
        any user-pinned Exposure children.
        """
        if self.dag is None or not self.id:
            return  # not yet placed in a graph (e.g. constructor-time)

        # Decode JSON-encoded dict values (see _read_output which str-ifies them).
        decoded = raw_value
        if isinstance(decoded, str):
            try:
                decoded = json.loads(decoded)
            except (ValueError, TypeError):
                return

        # Defer the import to runtime: nodes.user depends on nodes.config,
        # so we can't import Service at module load time.
        from zeropoint_agent.nodes.user.service import Service

        bundles = list(_enumerate_bundles(decoded))
        if not bundles:
            return

        new_ids: list[str] = []
        for key, _bundle in bundles:
            child_id = f"{self.id}/{key}" if key else f"{self.id}/_self"
            if child_id in self.dag.nodes:
                continue
            leaf = child_id.rsplit("/", 1)[-1]
            self.dag.add(
                child_id,
                Service(name=leaf, key=key or ""),
                parents=[self.id],
                perms="r--",
            )
            new_ids.append(child_id)

        if new_ids:
            try:
                self.dag.resolve_subset(new_ids, mode=mode)
            except Exception:
                # Not fatal — the next full resolve will pick them up.
                pass


def _enumerate_bundles(value: Any) -> Any:
    """(key, bundle_dict) pairs from a value. See xds.discover history."""
    if isinstance(value, dict):
        if "port" in value and "protocol" in value:
            try:
                int(value["port"])
                if isinstance(value["protocol"], str) and value["protocol"]:
                    yield "", value
                    return
            except (TypeError, ValueError):
                pass
        for k, v in value.items():
            if not isinstance(v, dict):
                continue
            if "port" not in v or "protocol" not in v:
                continue
            try:
                int(v["port"])
            except (TypeError, ValueError):
                continue
            if not (isinstance(v.get("protocol"), str) and v["protocol"]):
                continue
            yield str(k), v
