from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select

from app.database import get_session
from app.models import Pin, PinCreate, PinRead

router = APIRouter()


@router.post("", response_model=PinRead, status_code=201)
async def create_pin(
    payload: PinCreate,
    session: AsyncSession = Depends(get_session),
):
    pin = Pin(**payload.model_dump())
    session.add(pin)
    await session.commit()
    await session.refresh(pin)
    return pin


@router.get("/repo/{repo_id}", response_model=list[PinRead])
async def list_pins(repo_id: str, session: AsyncSession = Depends(get_session)):
    result = await session.exec(select(Pin).where(Pin.repo_id == repo_id))
    return result.all()


@router.delete("/{pin_id}", status_code=204)
async def delete_pin(pin_id: str, session: AsyncSession = Depends(get_session)):
    pin = await session.get(Pin, pin_id)
    if not pin:
        raise HTTPException(status_code=404, detail="Pin not found")
    await session.delete(pin)
    await session.commit()


@router.post("/repo/{repo_id}/export")
async def export_report(repo_id: str, session: AsyncSession = Depends(get_session)):
    """Generate a Markdown architecture report for the repo."""
    from app.services.report_generator import generate_report
    from app.models import Repo
    repo = await session.get(Repo, repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repo not found")
    pins_result = await session.exec(select(Pin).where(Pin.repo_id == repo_id))
    pins = pins_result.all()
    md = await generate_report(repo, pins)
    from fastapi.responses import Response
    return Response(
        content=md,
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{repo_id}-report.md"'},
    )
