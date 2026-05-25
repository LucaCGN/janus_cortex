from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.modules.agentic.runtime_control import (
    EventControlUpdateRequest,
    EventControlValidationError,
    event_control_to_aggregation_control,
    load_event_control_config,
    update_event_control_config,
)


router = APIRouter(prefix="/v1/runtime/event-controls", tags=["runtime-controls"])


@router.get("/{event_id}")
def get_event_control_config(
    event_id: str,
    session_date: str | None = Query(default=None),
) -> dict[str, Any]:
    config = load_event_control_config(event_id, day=session_date)
    return {
        "status": "ok",
        "event_id": event_id,
        "session_date": config.session_date,
        "config": config.model_dump(mode="json"),
        "aggregation_control": event_control_to_aggregation_control(config).model_dump(mode="json"),
        "live_order_impact": "none",
    }


@router.put("/{event_id}")
def put_event_control_config(
    event_id: str,
    payload: EventControlUpdateRequest,
    session_date: str | None = Query(default=None),
) -> dict[str, Any]:
    try:
        result = update_event_control_config(event_id, payload, day=session_date)
    except EventControlValidationError as exc:
        raise HTTPException(status_code=422, detail={"reason": exc.reason_code, **exc.detail}) from exc
    return {**result, "live_order_impact": "none"}
