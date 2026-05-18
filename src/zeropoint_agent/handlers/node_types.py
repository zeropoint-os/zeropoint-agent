"""Node-type schema endpoints — describe each registered node class.

Schemas are derived by introspecting each class's __init__ signature
(parameters, type hints, defaults) plus class-level attributes like
`default_perms`. The schema is per-type, not per-instance: the UI
fetches `/api/node-types` once and uses it to render any node of that
type.

The schema format is intentionally simple — minimal subset of JSON
Schema, plus zeropoint-specific fields like `default_perms`. The UI
maps type names to widget components.
"""

from __future__ import annotations

import inspect
import typing
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException

from zeropoint_agent.handlers import NODE_REGISTRY

router = APIRouter(prefix="/api/node-types", tags=["node-types"])


# --- type introspection --------------------------------------------------

# Python type → string we send to the UI. Used to pick a widget.
def _type_name(annotation: Any) -> str:
    """Best-effort mapping from a Python annotation to a schema type name."""
    if annotation is inspect.Parameter.empty or annotation is None:
        return "any"

    # Unwrap Optional[X] → X (keep nullability info separately)
    origin = typing.get_origin(annotation)
    if origin is typing.Union:
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return _type_name(args[0])
        # genuine union — just call it "any"
        return "any"

    if origin in (list, typing.List):
        return "list"
    if origin in (dict, typing.Dict):
        return "dict"
    if origin in (tuple, typing.Tuple):
        return "list"
    if origin in (set, typing.Set, frozenset):
        return "list"

    if annotation is str:
        return "string"
    if annotation is int or annotation is float:
        return "number"
    if annotation is bool:
        return "bool"
    if annotation is bytes:
        return "string"

    # Class or fallback
    if isinstance(annotation, type):
        return annotation.__name__.lower()
    return "any"


def _is_optional(annotation: Any) -> bool:
    """True if the annotation is Optional[X] / Union[..., None]."""
    if annotation is inspect.Parameter.empty:
        return False
    origin = typing.get_origin(annotation)
    if origin is typing.Union:
        return type(None) in typing.get_args(annotation)
    return False


def _field_schema(name: str, param: inspect.Parameter) -> Dict[str, Any]:
    """Schema for one __init__ parameter."""
    schema: Dict[str, Any] = {
        "name": name,
        "type": _type_name(param.annotation),
    }
    has_default = param.default is not inspect.Parameter.empty
    required = not has_default and not _is_optional(param.annotation)
    schema["required"] = required
    if has_default:
        # Only include if it's JSON-serializable; otherwise drop it.
        try:
            import json
            json.dumps(param.default)
            schema["default"] = param.default
        except (TypeError, ValueError):
            pass
    if _is_optional(param.annotation):
        schema["nullable"] = True
    return schema


def _class_schema(cls: type) -> Dict[str, Any]:
    """Full schema for a node class."""
    sig = inspect.signature(cls.__init__)
    # Resolve string-form annotations (PEP 563 / `from __future__ import
    # annotations`) into actual types so _type_name can introspect them.
    try:
        resolved = typing.get_type_hints(cls.__init__)
    except Exception:
        resolved = {}

    fields: List[Dict[str, Any]] = []
    for pname, param in sig.parameters.items():
        if pname == "self":
            continue
        if param.kind in (inspect.Parameter.VAR_POSITIONAL,
                          inspect.Parameter.VAR_KEYWORD):
            continue
        # Substitute the resolved annotation when we have one.
        if pname in resolved:
            param = param.replace(annotation=resolved[pname])
        fields.append(_field_schema(pname, param))

    schema: Dict[str, Any] = {
        "type": cls.__name__,
        "module": cls.__module__,
        "default_perms": getattr(cls, "default_perms", "***"),
        "doc": (inspect.getdoc(cls) or "").split("\n\n", 1)[0],
        "fields": fields,
    }
    return schema


# --- endpoints -----------------------------------------------------------

@router.get("")
async def list_node_types():
    """Schemas for every registered node type, keyed by short type name."""
    return {
        "ok": True,
        "types": {
            name: _class_schema(cls)
            for name, cls in NODE_REGISTRY.items()
        }
    }


@router.get("/{type_name}")
async def get_node_type(type_name: str):
    """Schema for a single node type (by short registry name)."""
    cls = NODE_REGISTRY.get(type_name)
    if cls is None:
        raise HTTPException(
            status_code=404,
            detail=f"unknown node type {type_name!r}; "
                   f"available: {sorted(NODE_REGISTRY)}")
    return _class_schema(cls)
