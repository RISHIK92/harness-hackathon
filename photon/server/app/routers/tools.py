from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.auth import get_current_user
from app.models import User
from app.tools.registry import TOOL_SCHEMAS

router = APIRouter()

# There used to be a `POST /{tool_name}` here that ran any tool with any
# arguments — including a `workspace_id` or `repo_id` the caller picked —
# with no login. That made every tenant's Slack, Jira, docs, past calls and
# code one unauthenticated request away. Tools run only inside the agent
# loop now, which forces the tenant scope from the authenticated principal;
# the code panel's file read-back has its own scoped route
# (GET /api/repos/{repo_id}/file).


@router.get("")
async def list_tools(current_user: User = Depends(get_current_user)):
    return {"tools": TOOL_SCHEMAS}
