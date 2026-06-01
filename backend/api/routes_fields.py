"""
Fields routes — read and update feed-fields.yaml (core toggles + custom CRUD).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from backend.config.loader import (
    load_fields, save_fields,
    load_ingest_all_fields, save_ingest_all_fields,
    load_flatten_max_depth, save_flatten_max_depth,
)

router = APIRouter(prefix="/api/fields", tags=["fields"])


@router.get("/ingest-all")
async def get_ingest_all_fields() -> dict[str, bool]:
    """Return the ingest_all_fields toggle."""
    return {"ingest_all_fields": load_ingest_all_fields()}


@router.put("/ingest-all")
async def set_ingest_all_fields(body: dict[str, bool]) -> dict[str, bool]:
    """Set the ingest_all_fields toggle. Body: {"ingest_all_fields": true|false}"""
    value = body.get("ingest_all_fields")
    if value is None:
        raise HTTPException(status_code=400, detail="Body must contain 'ingest_all_fields' boolean")
    save_ingest_all_fields(value)
    return {"ingest_all_fields": value}


@router.get("/flatten-depth")
async def get_flatten_depth() -> dict[str, int]:
    """Return the configured flatten_max_depth (nested-JSON flatten setting)."""
    return {"flatten_max_depth": load_flatten_max_depth()}


@router.put("/flatten-depth")
async def set_flatten_depth(body: dict[str, Any]) -> dict[str, int]:
    """Set flatten_max_depth. Body: {"flatten_max_depth": int 1..10}"""
    value = body.get("flatten_max_depth")
    if not isinstance(value, int) or isinstance(value, bool):
        raise HTTPException(status_code=400, detail="Body must contain 'flatten_max_depth' integer")
    try:
        save_flatten_max_depth(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"flatten_max_depth": value}


@router.get("")
async def get_fields() -> dict[str, Any]:
    """Return the full fields config."""
    return load_fields()


@router.put("/core/{field_name}/enabled")
async def toggle_core_field(field_name: str, body: dict[str, bool]) -> dict[str, Any]:
    """Enable or disable a core field. Body: {"enabled": true|false}"""
    enabled = body.get("enabled")
    if enabled is None:
        raise HTTPException(status_code=400, detail="Body must contain 'enabled' boolean")
    data = load_fields()
    for field in data.get("core_fields", []):
        if field["name"] == field_name:
            field["enabled"] = enabled
            save_fields(data)
            return field
    raise HTTPException(status_code=404, detail=f"Core field '{field_name}' not found")


@router.get("/custom")
async def list_custom_fields() -> list[dict[str, Any]]:
    return load_fields().get("custom_fields", [])


@router.post("/custom")
async def add_custom_field(field: dict[str, Any]) -> dict[str, Any]:
    if not field.get("name"):
        raise HTTPException(status_code=400, detail="Field must have a non-empty 'name'")
    data = load_fields()
    custom: list = data.setdefault("custom_fields", [])
    if any(f["name"] == field["name"] for f in custom):
        raise HTTPException(status_code=409, detail=f"Custom field '{field['name']}' already exists")
    # Also check against core field names
    core_names = {f["name"] for f in data.get("core_fields", [])}
    if field["name"] in core_names:
        raise HTTPException(status_code=409, detail=f"'{field['name']}' is already a core field")
    custom.append(field)
    save_fields(data)
    return field


@router.put("/custom/{field_name}")
async def update_custom_field(field_name: str, body: dict[str, Any]) -> dict[str, Any]:
    data = load_fields()
    custom: list = data.get("custom_fields", [])
    for i, f in enumerate(custom):
        if f["name"] == field_name:
            body["name"] = field_name
            custom[i] = body
            save_fields(data)
            return body
    raise HTTPException(status_code=404, detail=f"Custom field '{field_name}' not found")


@router.delete("/custom/{field_name}")
async def delete_custom_field(field_name: str) -> dict[str, str]:
    data = load_fields()
    custom: list = data.get("custom_fields", [])
    for i, f in enumerate(custom):
        if f["name"] == field_name:
            custom.pop(i)
            save_fields(data)
            return {"deleted": field_name}
    raise HTTPException(status_code=404, detail=f"Custom field '{field_name}' not found")
