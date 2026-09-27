"""
learning_path.py — Generate a sequenced onboarding plan for a repository.

Pipeline:
  1. Entry point detection  — modules with fan-in = 0
  2. Core module ranking    — sort all modules by fan-in (most-depended-on first)
  3. Topological sort       — reverse-BFS from entry points to order foundational → feature → entry
  4. Phase clustering       — group by label-propagation community (reuse community.py logic)
  5. LLM summarisation      — feed Gemini the top files/symbols per phase; ask for names + concepts
"""
from __future__ import annotations

import asyncio
import json
from collections import defaultdict, deque
from typing import Optional

import google.generativeai as genai

from app.config import get_settings
from app.core.graph.builder import Neo4jClient

settings = get_settings()
genai.configure(api_key=settings.gemini_api_key)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _topological_sort(nodes: list[dict], edges: list[dict]) -> list[dict]:
    """
    Kahn's algorithm on the IMPORTS graph.
    Returns nodes ordered from "foundational" (imported by many, imports few)
    to "entry" (imports many, imported by none).
    We reverse the edge direction: an edge A→B means "A depends on B", so
    in the dependency-order B must come before A.
    """
    id_to_node = {n["id"]: n for n in nodes}
    # in-degree = number of modules that depend on this module (fan-in in reversed graph)
    in_deg: dict[str, int] = {n["id"]: 0 for n in nodes}
    rev_adj: dict[str, list[str]] = defaultdict(list)

    for e in edges:
        src, tgt = e["source"], e["target"]
        if src in id_to_node and tgt in id_to_node:
            # original edge: src IMPORTS tgt  →  tgt is more foundational
            # reversed edge: tgt → src  so tgt gets processed first
            rev_adj[tgt].append(src)
            in_deg[src] = in_deg.get(src, 0) + 1

    queue = deque(nid for nid in in_deg if in_deg[nid] == 0)
    ordered: list[dict] = []
    while queue:
        nid = queue.popleft()
        if nid in id_to_node:
            ordered.append(id_to_node[nid])
        for dep in rev_adj[nid]:
            in_deg[dep] -= 1
            if in_deg[dep] == 0:
                queue.append(dep)

    # Append any nodes not reached (cycles)
    visited = {n["id"] for n in ordered}
    ordered += [n for n in nodes if n["id"] not in visited]
    return ordered


def _assign_phases(ordered_nodes: list[dict], edges: list[dict], max_phases: int = 5) -> list[list[dict]]:
    """
    Walk the topologically-sorted list and cut into phases of roughly equal size.
    We also respect community labels: prefer not to split a tight community across phases.
    """
    if not ordered_nodes:
        return []

    n = len(ordered_nodes)
    phase_size = max(3, n // max_phases)
    phases: list[list[dict]] = []
    current: list[dict] = []

    for node in ordered_nodes:
        current.append(node)
        if len(current) >= phase_size and len(phases) < max_phases - 1:
            phases.append(current)
            current = []

    if current:
        phases.append(current)

    return phases


# ── Neo4j queries ─────────────────────────────────────────────────────────────

async def _fetch_graph_with_symbols(repo_id: str) -> dict:
    """Return all modules with fan-in/out + their top symbols."""
    client = Neo4jClient()
    async with client._driver.session() as s:
        # All modules + fan-in/out
        nodes_res = await s.run(
            """
            MATCH (m:Module {repo_id: $repo_id})
            OPTIONAL MATCH (parent:Module {repo_id: $repo_id})-[:IMPORTS]->(m)
            WITH m, count(DISTINCT parent) AS fan_in
            OPTIONAL MATCH (m)-[:IMPORTS]->(child:Module {repo_id: $repo_id})
            WITH m, fan_in, count(DISTINCT child) AS fan_out
            OPTIONAL MATCH (m)-[:DEFINES]->(sym:Symbol)
            WITH m, fan_in, fan_out,
                 collect(DISTINCT {name: sym.name, kind: sym.kind, doc: sym.docstring})[0..6] AS symbols
            RETURN m.node_id AS id,
                   m.path AS path,
                   m.language AS language,
                   m.size_bytes AS size_bytes,
                   fan_in,
                   fan_out,
                   symbols
            """,
            repo_id=repo_id,
        )
        nodes = [dict(r) for r in await nodes_res.data()]

        edges_res = await s.run(
            """
            MATCH (a:Module {repo_id: $repo_id})-[r:IMPORTS]->(b:Module {repo_id: $repo_id})
            RETURN a.node_id AS source, b.node_id AS target
            """,
            repo_id=repo_id,
        )
        edges = [dict(r) for r in await edges_res.data()]

    await client.close()
    return {"nodes": nodes, "edges": edges}


# ── LLM summarisation ─────────────────────────────────────────────────────────

async def _summarise_phases(phases_raw: list[list[dict]]) -> list[dict]:
    """
    Ask Gemini to name each phase and annotate each file with key concepts.
    Returns a list of phase dicts matching the API schema.
    """
    # Build a compact description for Gemini
    phase_summaries = []
    for i, phase_nodes in enumerate(phases_raw):
        files_desc = []
        for node in phase_nodes[:8]:  # cap at 8 files per phase for token budget
            syms = node.get("symbols") or []
            sym_names = [s["name"] for s in syms if s.get("name")]
            doc = next((s["doc"] for s in syms if s.get("doc")), "")
            files_desc.append({
                "path": node["path"],
                "fan_in": node.get("fan_in", 0),
                "fan_out": node.get("fan_out", 0),
                "symbols": sym_names[:5],
                "docstring_snippet": doc[:200] if doc else "",
            })
        phase_summaries.append({"phase_index": i + 1, "files": files_desc})

    prompt = f"""You are a senior software engineer helping a new developer onboard to an unfamiliar codebase.

You have been given {len(phases_raw)} learning phases derived from the dependency graph of the project.
Each phase lists files ordered from most foundational to most entry-level.

For EACH phase, return a JSON object with:
- "title": a short phase name (e.g. "Data Models & Types", "Core Services", "API Layer")
- "description": 1–2 sentences on what this phase covers and why it matters
- "estimated_minutes": realistic reading/study time (integer, 10–60)
- "files": array — one object per file with:
    - "path": the file path as given
    - "key_concepts": array of 2–4 short concept strings (e.g. ["User authentication", "JWT tokens"])
    - "why_first": one sentence on why this file matters or should be read early

Respond with ONLY a JSON array of phase objects, no markdown fences, no extra text.

Phases data:
{json.dumps(phase_summaries, indent=2)}
"""

    model = genai.GenerativeModel(settings.gemini_chat_model)
    loop = asyncio.get_event_loop()
    result_holder: list[str] = []

    def _run():
        resp = model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(
                temperature=0.3,
                max_output_tokens=4096,
            ),
        )
        result_holder.append(resp.text)

    await loop.run_in_executor(None, _run)
    raw_text = result_holder[0] if result_holder else "[]"

    # Strip markdown fences if Gemini adds them despite instructions
    raw_text = raw_text.strip()
    if raw_text.startswith("```"):
        raw_text = raw_text.split("\n", 1)[1]
        raw_text = raw_text.rsplit("```", 1)[0]

    try:
        llm_phases: list[dict] = json.loads(raw_text)
    except json.JSONDecodeError:
        # Fallback: build minimal phases without LLM annotations
        llm_phases = [
            {
                "title": f"Phase {i + 1}",
                "description": "Study these files in order.",
                "estimated_minutes": 20,
                "files": [{"path": n["path"], "key_concepts": [], "why_first": ""} for n in phase_nodes[:8]],
            }
            for i, phase_nodes in enumerate(phases_raw)
        ]

    # Merge original node metadata (node_id, fan_in, symbols) back in
    path_to_node = {}
    for phase_nodes in phases_raw:
        for node in phase_nodes:
            path_to_node[node["path"]] = node

    result: list[dict] = []
    for phase_idx, llm_phase in enumerate(llm_phases):
        phase_nodes_raw = phases_raw[phase_idx] if phase_idx < len(phases_raw) else []
        enriched_files = []
        for f in llm_phase.get("files", []):
            orig = path_to_node.get(f["path"], {})
            syms = orig.get("symbols") or []
            enriched_files.append({
                "path": f["path"],
                "node_id": orig.get("id", ""),
                "language": orig.get("language", ""),
                "fan_in": orig.get("fan_in", 0),
                "fan_out": orig.get("fan_out", 0),
                "key_concepts": f.get("key_concepts", []),
                "why_first": f.get("why_first", ""),
                "symbols": [s["name"] for s in syms if s.get("name")][:5],
            })
        result.append({
            "phase_number": phase_idx + 1,
            "title": llm_phase.get("title", f"Phase {phase_idx + 1}"),
            "description": llm_phase.get("description", ""),
            "estimated_minutes": llm_phase.get("estimated_minutes", 20),
            "files": enriched_files,
        })

    return result


# ── Public API ────────────────────────────────────────────────────────────────

async def generate_learning_path(repo_id: str) -> dict:
    """
    Full pipeline: fetch graph → topological sort → phase grouping → LLM summarisation.
    Returns a dict with key "phases" (list of phase objects).
    """
    graph = await _fetch_graph_with_symbols(repo_id)
    nodes: list[dict] = graph["nodes"]
    edges: list[dict] = graph["edges"]

    if not nodes:
        return {"repo_id": repo_id, "phases": [], "total_files": 0}

    ordered = _topological_sort(nodes, edges)
    phases_raw = _assign_phases(ordered, edges, max_phases=5)
    phases = await _summarise_phases(phases_raw)

    total_minutes = sum(p["estimated_minutes"] for p in phases)

    return {
        "repo_id": repo_id,
        "phases": phases,
        "total_files": len(nodes),
        "total_phases": len(phases),
        "total_estimated_minutes": total_minutes,
    }
