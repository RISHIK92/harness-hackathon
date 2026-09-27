from __future__ import annotations
import json
import uuid
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_session
from app.models import QueryRequest, QueryIntent, Repo
from app.core.query_engine.intent_classifier import classify_intent
from app.core.query_engine.retrieval import hybrid_retrieve
from app.core.query_engine.context_assembler import assemble_context
from app.core.query_engine.llm_orchestrator import stream_answer

router = APIRouter()


async def _stream_question(repo_id: str, question: str, session: AsyncSession, file_context_path: Optional[str] = None):
    """Shared SSE streamer: classify → retrieve → assemble → stream."""
    # Prepend file context hint so retrieval + LLM stay scoped to that file
    effective_question = question
    if file_context_path:
        effective_question = (
            f"[Focus your answer specifically on the file `{file_context_path}`]\n\n{question}"
        )
    session_id = str(uuid.uuid4())
    intent = await classify_intent(effective_question)
    chunks, graph_nodes = await hybrid_retrieve(repo_id=repo_id, question=effective_question, intent=intent)
    context = await assemble_context(chunks, graph_nodes, effective_question)

    async def gen():
        meta = {
            "type": "meta",
            "session_id": session_id,
            "intent": intent.value,
            "cited_chunks": context["cited_chunks"],
            "graph_nodes": context["graph_nodes"],
        }
        yield f"data: {json.dumps(meta)}\n\n"
        async for token in stream_answer(context["prompt"], question):
            yield f"data: {json.dumps({'type': 'token', 'text': token})}\n\n"
        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("")
async def query_repo(payload: QueryRequest, session: AsyncSession = Depends(get_session)):
    """Main NL query endpoint. Returns a streaming SSE response."""
    return await _stream_question(payload.repo_id, payload.question, session, payload.file_context_path)


# ── Quick Action models ───────────────────────────────────────────────────────

class TraceFunctionRequest(BaseModel):
    repo_id: str
    function_name: str

class FindUsagesRequest(BaseModel):
    repo_id: str
    symbol: str

class ExplainFileRequest(BaseModel):
    repo_id: str
    file_path: str

class ImpactRequest(BaseModel):
    repo_id: str
    symbol: str


# ── Quick Action endpoints ────────────────────────────────────────────────────

@router.post("/trace-function")
async def trace_function(payload: TraceFunctionRequest, session: AsyncSession = Depends(get_session)):
    """Trace all callers and callees of a function."""
    repo = await session.get(Repo, payload.repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repo not found")
    question = (
        f"Trace the function `{payload.function_name}`. "
        f"Show who calls it, what it calls internally, and explain the full call chain. "
        f"Include file paths and line-level context."
    )
    return await _stream_question(payload.repo_id, question, session)


@router.post("/find-usages")
async def find_usages(payload: FindUsagesRequest, session: AsyncSession = Depends(get_session)):
    """Find all usages/references of a symbol across the codebase."""
    repo = await session.get(Repo, payload.repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repo not found")
    question = (
        f"Find all usages and references of `{payload.symbol}` across the codebase. "
        f"List every file, function, and line where it is used or imported. "
        f"Explain the context of each usage."
    )
    return await _stream_question(payload.repo_id, question, session)


@router.post("/explain-file")
async def explain_file(payload: ExplainFileRequest, session: AsyncSession = Depends(get_session)):
    """Generate an AI summary of a file."""
    repo = await session.get(Repo, payload.repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repo not found")
    question = (
        f"Explain the file `{payload.file_path}` in detail. "
        f"Describe its purpose, the key functions and classes it defines, "
        f"what it imports, and how it fits into the overall architecture."
    )
    return await _stream_question(payload.repo_id, question, session)


@router.post("/impact-analysis")
async def impact_analysis(payload: ImpactRequest, session: AsyncSession = Depends(get_session)):
    """Simulate the blast radius of changing a symbol."""
    repo = await session.get(Repo, payload.repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repo not found")
    question = (
        f"Run an impact analysis for `{payload.symbol}`. "
        f"If this symbol were changed or removed, which files and functions would break? "
        f"List all downstream dependents ranked by criticality, and estimate the risk level."
    )
    return await _stream_question(payload.repo_id, question, session)
