"""Node-type schema endpoints — describe each registered node class
plus any meta-operations (e.g. module install) that the UI offers
through the same picker.

Each schema describes:
  - `type`     — short name shown in the picker
  - `kind`     — "node" (constructor-params style) or "operation"
                 (the form fields ARE the request body)
  - `endpoint` — where the UI POSTs the assembled request
  - `fields`   — the form fields the user fills in
  - extra metadata (default_perms for nodes, doc for both)

Each field carries `readonly: bool`, derived from dataclass field
metadata (set via the `readonly()` helper in inode.py). Fields with
`readonly=true` cannot be PUT to — the mutation handler enforces this.

The UI renders any schema uniformly; the only per-kind logic is how
the form values are assembled into the request body when saving.
"""

from __future__ import annotations

import dataclasses
import inspect
import typing
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException

from zeropoint_agent.handlers import NODE_REGISTRY

router = APIRouter(prefix="/api/node-types", tags=["node-types"])


# --- type introspection --------------------------------------------------

def _type_name(annotation: Any) -> str:
    """Best-effort mapping from a Python annotation to a schema type name."""
    if annotation is inspect.Parameter.empty or annotation is None:
        return "any"

    origin = typing.get_origin(annotation)
    if origin is typing.Union:
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return _type_name(args[0])
        return "any"

    if origin in (list, typing.List, tuple, typing.Tuple, set, typing.Set, frozenset):
        return "list"
    if origin in (dict, typing.Dict):
        return "dict"

    if annotation is str:
        return "string"
    if annotation is int or annotation is float:
        return "number"
    if annotation is bool:
        return "bool"
    if annotation is bytes:
        return "string"

    # A bare TypeVar (e.g. Var[T] where T is unresolved) — treat as
    # 'string' since literals typed by the user via a text input are
    # almost always strings. Specific Var[int]/Var[bool] etc. resolve
    # via the type's __args__ above and don't reach this branch.
    if isinstance(annotation, typing.TypeVar):
        return "string"

    if isinstance(annotation, type):
        return annotation.__name__.lower()
    return "any"


def _is_optional(annotation: Any) -> bool:
    origin = typing.get_origin(annotation)
    if origin is typing.Union:
        return type(None) in typing.get_args(annotation)
    return False


def _dataclass_field_schema(f: dataclasses.Field, resolved_type: Any) -> Dict[str, Any]:
    """Schema for one dataclass field.

    `resolved_type` is the annotation resolved via `typing.get_type_hints`
    (string annotations turned into types).
    """
    schema: Dict[str, Any] = {
        "name": f.name,
        "type": _type_name(resolved_type),
    }
    # Required = no default and no default_factory.
    has_default = (f.default is not dataclasses.MISSING
                   or f.default_factory is not dataclasses.MISSING)
    schema["required"] = not has_default and not _is_optional(resolved_type)
    if f.default is not dataclasses.MISSING:
        try:
            import json
            json.dumps(f.default)
            schema["default"] = f.default
        except (TypeError, ValueError):
            pass
    if _is_optional(resolved_type):
        schema["nullable"] = True
    if f.metadata.get("readonly"):
        schema["readonly"] = True
    return schema


def _class_schema(short_name: str, cls: type) -> Dict[str, Any]:
    """Schema for a node class — kind='node', endpoint=POST /api/dag/nodes.

    Reads dataclass fields (not __init__) so the class is the single
    source of truth for what the API surface looks like. Read-only
    fields are marked via the `readonly()` helper in inode.py; the
    schema surfaces the flag and the PUT handler enforces it.

    The "node" kind tells the UI to wrap the form fields into
    `{id, type, config: {...fields...}, parents, perms}` at save time.
    `id` is computed by the UI from the parent path + a chosen name
    field; `parents` is implicit (the parent namespace the user clicked
    "add" in); `perms` is a separate widget.
    """
    if not dataclasses.is_dataclass(cls):
        # Non-dataclass node — emit an empty field list. The UI will
        # let the user create one if no required fields, or surface
        # the type as not-creatable otherwise.
        return {
            "type": short_name,
            "class_name": cls.__name__,
            "kind": "node",
            "endpoint": "/api/dag/nodes",
            "module": cls.__module__,
            "default_perms": getattr(cls, "default_perms", "***"),
            "doc": (inspect.getdoc(cls) or "").split("\n\n", 1)[0],
            "fields": [],
        }

    try:
        resolved = typing.get_type_hints(cls)
    except Exception:
        resolved = {}

    fields: List[Dict[str, Any]] = []
    for f in dataclasses.fields(cls):
        rtype = resolved.get(f.name, f.type)
        fields.append(_dataclass_field_schema(f, rtype))

    return {
        "type": short_name,
        "class_name": cls.__name__,
        "kind": "node",
        "endpoint": "/api/dag/nodes",
        "module": cls.__module__,
        "default_perms": getattr(cls, "default_perms", "***"),
        "doc": (inspect.getdoc(cls) or "").split("\n\n", 1)[0],
        "fields": fields,
    }


# --- operation schemas (hand-declared) -----------------------------------
#
# These are meta-operations the UI offers in the same type-picker as
# regular nodes. Each has kind="operation": its form fields ARE the
# request body (no wrapping). Add new operations here without touching
# the rest of the code path.

_OPERATION_SCHEMAS: List[Dict[str, Any]] = [
    {
        "type": "module",
        "kind": "operation",
        "endpoint": "/api/modules",
        "doc": ("Install a Terraform-managed module. Creates a namespace "
                "under the chosen parent (default 'modules'), Vars "
                "for every declared variable, and a Terraform that "
                "runs terraform apply."),
        "fields": [
            {"name": "module_id", "type": "string", "required": True,
             "description": "Unique id for this module instance (becomes the namespace name)."},
            {"name": "source", "type": "string", "required": True,
             "description": "Git URL with @<40-char-sha> ref, or a local path (/, ./, ~/, file://)."},
            {"name": "parent_namespace", "type": "string", "required": False,
             "default": "modules",
             "description": "Namespace to install under."},
            {"name": "overrides", "type": "dict", "required": False,
             "description": "Optional per-var overrides ({varname: value})."},
            {"name": "resolve", "type": "bool", "required": False, "default": False,
             "description": "Run terraform apply immediately after adding."},
        ],
    },
]


# --- endpoints -----------------------------------------------------------

def _all_schemas() -> Dict[str, Dict[str, Any]]:
    """Combined map of all schemas the picker should offer."""
    out: Dict[str, Dict[str, Any]] = {}
    for name, cls in NODE_REGISTRY.items():
        out[name] = _class_schema(name, cls)
    for op in _OPERATION_SCHEMAS:
        out[op["type"]] = op
    return out


@router.get("")
async def list_node_types():
    """Schemas for every registered type (nodes + operations)."""
    return {"ok": True, "types": _all_schemas()}


@router.get("/{type_name}")
async def get_node_type(type_name: str):
    """Schema for a single type (by short name)."""
    schemas = _all_schemas()
    if type_name not in schemas:
        raise HTTPException(
            status_code=404,
            detail=f"unknown type {type_name!r}; "
                   f"available: {sorted(schemas)}")
    return schemas[type_name]
