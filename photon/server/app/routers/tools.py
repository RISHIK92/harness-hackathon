from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.tools.registry import TOOL_SCHEMAS, UnknownToolError, dispatch

router = APIRouter()


@router.get("")
async def list_tools():
    return {"tools": TOOL_SCHEMAS}


class ToolCallRequest(BaseModel):
    args: dict[str, Any] = {}


@router.post("/{tool_name}")
async def call_tool(tool_name: str, payload: ToolCallRequest):
    try:
        return await dispatch(tool_name, payload.args)
    except UnknownToolError:
        raise HTTPException(status_code=404, detail=f"unknown tool '{tool_name}'")
    except TypeError as exc:
        raise HTTPException(status_code=422, detail=f"bad arguments for '{tool_name}': {exc}")
