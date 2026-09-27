from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_session
from app.models import Repo
from app.core.graph.builder import Neo4jClient
from app.core.graph.layout import compute_layout
from app.core.graph.community import detect_communities

router = APIRouter()


@router.get("/{repo_id}")
async def get_graph(
    repo_id: str,
    depth: int = Query(default=2, ge=1, le=5),
    session: AsyncSession = Depends(get_session),
):
    """Return the full node-link graph for a repo with layout coordinates and clusters."""
    repo = await session.get(Repo, repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repo not found")
    if repo.status.value != "READY":
        raise HTTPException(status_code=409, detail="Repo not yet ready")

    client = Neo4jClient()
    raw = await client.get_repo_graph(repo_id, depth=depth)
    await client.close()

    # Compute layout + communities
    nodes_with_layout = compute_layout(raw["nodes"], raw["edges"])
    nodes_with_community = detect_communities(nodes_with_layout, raw["edges"])

    return {
        "repo_id": repo_id,
        "nodes": nodes_with_community,
        "edges": raw["edges"],
    }


@router.get("/{repo_id}/subgraph/{node_id:path}")
async def get_subgraph(
    repo_id: str,
    node_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Expand a single cluster node into its children."""
    repo = await session.get(Repo, repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repo not found")

    client = Neo4jClient()
    raw = await client.get_subgraph(node_id, repo_id=repo_id)
    await client.close()

    nodes_with_layout = compute_layout(raw["nodes"], raw["edges"])
    nodes_with_community = detect_communities(nodes_with_layout, raw["edges"])

    return {
        "repo_id": repo_id,
        "parent_node_id": node_id,
        "nodes": nodes_with_community,
        "edges": raw["edges"],
    }


@router.get("/{repo_id}/entry-points")
async def get_entry_points(
    repo_id: str,
    limit: int = Query(default=8, ge=1, le=20),
    session: AsyncSession = Depends(get_session),
):
    """Return modules with zero fan-in — the natural entry points of the repo."""
    repo = await session.get(Repo, repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repo not found")

    client = Neo4jClient()
    result = await client.get_entry_points(repo_id, limit=limit)
    await client.close()
    return result


@router.get("/{repo_id}/impact/{node_id:path}")
async def get_impact(
    repo_id: str,
    node_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Return impact analysis for a module node."""
    repo = await session.get(Repo, repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repo not found")

    client = Neo4jClient()
    result = await client.analyze_impact(node_id, repo_id)
    await client.close()
    return result
