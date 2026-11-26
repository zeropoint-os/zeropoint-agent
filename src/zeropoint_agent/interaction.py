"""Canonical durable interaction contract for Jobs and REST polling."""

from __future__ import annotations

from typing import Annotated, Any, Literal, Optional, TypedDict, Union

from pydantic import BaseModel, ConfigDict, Field


class UrlQrInteraction(TypedDict):
    id: str
    kind: Literal["url_qr"]
    title: str
    message: str
    url: str
    qr: str
    expires_at: str | None


class DeviceCodeInteraction(TypedDict):
    id: str
    kind: Literal["device_code"]
    title: str
    message: str
    verification_url: str
    user_code: str
    qr: str | None
    expires_at: str | None


class OpenUrlInteraction(TypedDict):
    id: str
    kind: Literal["open_url"]
    title: str
    message: str
    url: str
    expires_at: str | None


Interaction = UrlQrInteraction | DeviceCodeInteraction | OpenUrlInteraction


class UrlQrInteractionModel(BaseModel):
    id: str
    kind: Literal["url_qr"]
    title: str
    message: str
    url: str
    qr: str
    expires_at: Optional[str] = None


class DeviceCodeInteractionModel(BaseModel):
    id: str
    kind: Literal["device_code"]
    title: str
    message: str
    verification_url: str
    user_code: str
    qr: Optional[str] = None
    expires_at: Optional[str] = None


class OpenUrlInteractionModel(BaseModel):
    id: str
    kind: Literal["open_url"]
    title: str
    message: str
    url: str
    expires_at: Optional[str] = None


InteractionModel = Annotated[
    Union[
        UrlQrInteractionModel,
        DeviceCodeInteractionModel,
        OpenUrlInteractionModel,
    ],
    Field(discriminator="kind"),
]


class PromiseRestResource(BaseModel):
    model_config = ConfigDict(extra="allow")
    status: str
    interaction: Optional[InteractionModel] = None


class GraphRunNodeRestResource(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    status: str
    interaction: Optional[InteractionModel] = None
    promise: Optional[PromiseRestResource] = None


class GraphRunRestResource(BaseModel):
    model_config = ConfigDict(extra="allow")
    run_id: str
    status: str
    interaction_required: bool = False
    nodes: list[GraphRunNodeRestResource]


_ALLOWED_FIELDS = {
    "url_qr": {"id", "kind", "title", "message", "url", "qr", "expires_at"},
    "device_code": {
        "id", "kind", "title", "message", "verification_url",
        "user_code", "qr", "expires_at",
    },
    "open_url": {"id", "kind", "title", "message", "url", "expires_at"},
}
_REQUIRED_FIELDS = {
    "url_qr": {"id", "kind", "title", "message", "url", "qr"},
    "device_code": {
        "id", "kind", "title", "message", "verification_url", "user_code",
    },
    "open_url": {"id", "kind", "title", "message", "url"},
}


def _base(
    *,
    interaction_id: str,
    kind: str,
    title: str,
    message: str,
    expires_at: Optional[str],
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "id": interaction_id,
        "kind": kind,
        "title": title,
        "message": message,
    }
    if expires_at is not None:
        value["expires_at"] = expires_at
    return value


def url_qr(
    *,
    interaction_id: str,
    title: str,
    message: str,
    url: str,
    qr: str,
    expires_at: Optional[str] = None,
) -> UrlQrInteraction:
    return validate_interaction({
        **_base(
            interaction_id=interaction_id,
            kind="url_qr",
            title=title,
            message=message,
            expires_at=expires_at,
        ),
        "url": url,
        "qr": qr,
    })  # type: ignore[return-value]


def device_code(
    *,
    interaction_id: str,
    title: str,
    message: str,
    verification_url: str,
    user_code: str,
    qr: Optional[str] = None,
    expires_at: Optional[str] = None,
) -> DeviceCodeInteraction:
    value = {
        **_base(
            interaction_id=interaction_id,
            kind="device_code",
            title=title,
            message=message,
            expires_at=expires_at,
        ),
        "verification_url": verification_url,
        "user_code": user_code,
    }
    if qr is not None:
        value["qr"] = qr
    return validate_interaction(value)  # type: ignore[return-value]


def open_url(
    *,
    interaction_id: str,
    title: str,
    message: str,
    url: str,
    expires_at: Optional[str] = None,
) -> OpenUrlInteraction:
    return validate_interaction({
        **_base(
            interaction_id=interaction_id,
            kind="open_url",
            title=title,
            message=message,
            expires_at=expires_at,
        ),
        "url": url,
    })  # type: ignore[return-value]


def checkpoint(interaction: Interaction) -> dict[str, Any]:
    """Wrap one validated interaction for durable Job checkpoint storage."""
    return {"interaction": validate_interaction(dict(interaction))}


def interaction_from_checkpoint(value: Any) -> Optional[dict[str, Any]]:
    """Return a validated public interaction or None for ordinary checkpoints."""
    if not isinstance(value, dict) or "interaction" not in value:
        return None
    try:
        return validate_interaction(value["interaction"])
    except (TypeError, ValueError):
        return None


def validate_interaction(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError("interaction must be an object")
    kind = value.get("kind")
    if kind not in _ALLOWED_FIELDS:
        raise ValueError(f"unsupported interaction kind: {kind!r}")
    unknown = set(value) - _ALLOWED_FIELDS[kind]
    missing = _REQUIRED_FIELDS[kind] - set(value)
    if unknown:
        raise ValueError(f"unsupported interaction fields: {sorted(unknown)}")
    if missing:
        raise ValueError(f"missing interaction fields: {sorted(missing)}")
    for key in _REQUIRED_FIELDS[kind] - {"kind"}:
        if not isinstance(value.get(key), str) or not value[key]:
            raise ValueError(f"interaction field {key!r} must be a non-empty string")
    for key in set(value) - {"kind"}:
        if value[key] is not None and not isinstance(value[key], str):
            raise ValueError(f"interaction field {key!r} must be a string or null")
    return dict(value)
