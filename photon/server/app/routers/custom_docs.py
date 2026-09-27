"""Upload documents the agent should know about.

Text and markdown only, on purpose. PDF and DOCX extraction is a different
problem (layout, tables, scanned images) with its own failure modes, and
shipping half of it would produce silently garbled context that is worse
than no context at all.
"""
from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import get_current_user
from app.core.workspace import get_current_workspace, require_role
from app.database import get_session
from app.models import CustomDoc, CustomDocRead, User, Workspace, WorkspaceRole
from app.services.custom_docs import index_document

log = structlog.get_logger()
router = APIRouter()

_ALLOWED_SUFFIXES = (".md", ".markdown", ".txt", ".text")
_MAX_BYTES = 5 * 1024 * 1024


@router.get("", response_model=list[CustomDocRead])
async def list_docs(
    session: AsyncSession = Depends(get_session),
    workspace: Workspace = Depends(get_current_workspace),
):
    result = await session.execute(
        select(CustomDoc).where(CustomDoc.workspace_id == workspace.id).order_by(CustomDoc.created_at.desc())
    )
    return result.scalars().all()


@router.post("", response_model=CustomDocRead, status_code=201)
async def upload_doc(
    file: UploadFile | None = File(default=None),
    title: str = Form(default=""),
    text: str = Form(default=""),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
    workspace: Workspace = Depends(require_role(WorkspaceRole.MEMBER)),
):
    """Accepts a file OR pasted text — pasting is how most process notes
    actually arrive, and forcing someone to save a file first is friction
    for no benefit."""
    if file is not None:
        name = (file.filename or "").lower()
        if not name.endswith(_ALLOWED_SUFFIXES):
            raise HTTPException(
                status_code=415,
                detail="Only .md and .txt for now — PDF and DOCX extraction is a separate job and "
                       "half-done extraction produces garbled context, which is worse than none",
            )
        raw = await file.read()
        if len(raw) > _MAX_BYTES:
            raise HTTPException(status_code=413, detail="That file is larger than 5 MB")
        try:
            body = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise HTTPException(status_code=415, detail="That file isn't UTF-8 text")
        doc_title = title.strip() or (file.filename or "Untitled")
    else:
        body = text
        doc_title = title.strip() or "Pasted note"

    if not body.strip():
        raise HTTPException(status_code=422, detail="Nothing to index — the document is empty")

    doc = CustomDoc(
        workspace_id=workspace.id,
        title=doc_title,
        filename=file.filename if file else None,
        size_bytes=len(body.encode()),
        uploaded_by=current_user.id,
    )
    session.add(doc)
    await session.commit()
    await session.refresh(doc)

    # Indexed inline rather than queued: these are small, and a user who
    # just uploaded a doc expects to be able to ask about it immediately.
    import asyncio

    doc.chunk_count = await asyncio.get_event_loop().run_in_executor(
        None, lambda: index_document(workspace.id, doc.id, doc.title, body)
    )
    session.add(doc)
    await session.commit()
    await session.refresh(doc)
    return doc


@router.delete("/{doc_id}", status_code=204)
async def delete_doc(
    doc_id: str,
    session: AsyncSession = Depends(get_session),
    workspace: Workspace = Depends(require_role(WorkspaceRole.MEMBER)),
):
    doc = await session.get(CustomDoc, doc_id)
    if not doc or doc.workspace_id != workspace.id:
        raise HTTPException(status_code=404, detail="Document not found")

    # Remove the vectors too, or the agent keeps citing a document the UI
    # says was deleted — the worst kind of stale evidence.
    from qdrant_client.models import FieldCondition, Filter, MatchValue

    from app.core.embedding.embedder import get_qdrant
    from app.services.connectors.base import COLLECTION, ensure_collection

    ensure_collection()
    get_qdrant().delete(
        collection_name=COLLECTION,
        points_selector=Filter(
            must=[
                FieldCondition(key="workspace_id", match=MatchValue(value=workspace.id)),
                FieldCondition(key="provider", match=MatchValue(value="custom_docs")),
                FieldCondition(key="doc_id", match=MatchValue(value=doc_id)),
            ]
        ),
    )
    await session.delete(doc)
    await session.commit()
