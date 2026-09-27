from __future__ import annotations
from typing import Optional
from collections import deque
import structlog

from neo4j import AsyncGraphDatabase, AsyncDriver

from app.config import get_settings

settings = get_settings()
log = structlog.get_logger()


class Neo4jClient:
    """Async Neo4j client for graph operations."""

    def __init__(self):
        self._driver: AsyncDriver = AsyncGraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
        )

    async def close(self):
        await self._driver.close()

    async def ensure_schema(self):
        """Create indexes and constraints."""
        async with self._driver.session() as s:
            await s.run(
                "CREATE CONSTRAINT IF NOT EXISTS FOR (m:Module) REQUIRE m.node_id IS UNIQUE"
            )
            await s.run(
                "CREATE CONSTRAINT IF NOT EXISTS FOR (sym:Symbol) REQUIRE sym.node_id IS UNIQUE"
            )
            await s.run(
                "CREATE INDEX IF NOT EXISTS FOR (m:Module) ON (m.repo_id)"
            )

    async def upsert_module(
        self,
        repo_id: str,
        rel_path: str,
        language: str,
        size_bytes: int,
    ) -> str:
        """Create or update a Module node. Returns the node_id."""
        node_id = f"{repo_id}::{rel_path}"
        async with self._driver.session() as s:
            await s.run(
                """
                MERGE (m:Module {node_id: $node_id})
                SET m.repo_id   = $repo_id,
                    m.path      = $path,
                    m.language  = $language,
                    m.size_bytes = $size_bytes
                """,
                node_id=node_id,
                repo_id=repo_id,
                path=rel_path,
                language=language,
                size_bytes=size_bytes,
            )
        return node_id

    async def upsert_symbol(
        self,
        repo_id: str,
        module_node_id: str,
        name: str,
        kind: str,
        start_line: int,
        end_line: int,
        docstring: Optional[str],
    ) -> str:
        node_id = f"{module_node_id}::{kind}::{name}"
        async with self._driver.session() as s:
            await s.run(
                """
                MERGE (sym:Symbol {node_id: $node_id})
                SET sym.repo_id    = $repo_id,
                    sym.name       = $name,
                    sym.kind       = $kind,
                    sym.start_line = $start_line,
                    sym.end_line   = $end_line,
                    sym.docstring  = $docstring
                WITH sym
                MATCH (m:Module {node_id: $module_node_id})
                MERGE (m)-[:DEFINES]->(sym)
                """,
                node_id=node_id,
                repo_id=repo_id,
                name=name,
                kind=kind,
                start_line=start_line,
                end_line=end_line,
                docstring=docstring or "",
                module_node_id=module_node_id,
            )
        return node_id

    async def upsert_import_edge(
        self,
        from_module_id: str,
        target_path_fragment: str,
        repo_id: str,
    ) -> None:
        """Create an IMPORTS edge between modules if the target exists."""
        async with self._driver.session() as s:
            # Try multiple matching strategies in order of precision:
            # 1. Exact match (without extension)
            # 2. Ends with the fragment (for resolved relative paths)
            # 3. Contains the fragment (fallback)
            
            # First try exact match (path equals fragment or fragment.ext)
            find_result = await s.run(
                """
                MATCH (tgt:Module)
                WHERE tgt.repo_id = $repo_id
                  AND (tgt.path = $fragment 
                       OR tgt.path = $fragment + '.ts'
                       OR tgt.path = $fragment + '.tsx'
                       OR tgt.path = $fragment + '.js'
                       OR tgt.path = $fragment + '.jsx'
                       OR tgt.path = $fragment + '.py'
                       OR tgt.path = $fragment + '.go'
                       OR tgt.path = $fragment + '.rs'
                       OR tgt.path = $fragment + '.java')
                RETURN tgt.node_id AS id, tgt.path AS path
                LIMIT 5
                """,
                repo_id=repo_id,
                fragment=target_path_fragment,
            )
            matches = await find_result.data()
            
            # If no exact match, try ends-with pattern
            if not matches:
                find_result = await s.run(
                    """
                    MATCH (tgt:Module)
                    WHERE tgt.repo_id = $repo_id
                      AND tgt.path ENDS WITH $fragment
                    RETURN tgt.node_id AS id, tgt.path AS path
                    LIMIT 5
                    """,
                    repo_id=repo_id,
                    fragment=target_path_fragment,
                )
                matches = await find_result.data()
            
            # If still no match, try contains as final fallback
            if not matches:
                find_result = await s.run(
                    """
                    MATCH (tgt:Module)
                    WHERE tgt.repo_id = $repo_id
                      AND tgt.path CONTAINS $fragment
                    RETURN tgt.node_id AS id, tgt.path AS path
                    LIMIT 5
                    """,
                    repo_id=repo_id,
                    fragment=target_path_fragment,
                )
                matches = await find_result.data()
            
            if not matches:
                # No match found - log for debugging
                log.debug("import.no_match", from_id=from_module_id, fragment=target_path_fragment, repo_id=repo_id)
                return
            
            if len(matches) > 1:
                # Multiple matches - prefer shortest path (most specific)
                matches = sorted(matches, key=lambda m: len(m["path"]))
                log.debug("import.multiple_matches", from_id=from_module_id, fragment=target_path_fragment, 
                         matches=[m["path"] for m in matches], selected=matches[0]["path"])
            
            # Create edge to the best match
            target_id = matches[0]["id"]
            await s.run(
                """
                MATCH (src:Module {node_id: $from_id})
                MATCH (tgt:Module {node_id: $target_id})
                MERGE (src)-[:IMPORTS]->(tgt)
                """,
                from_id=from_module_id,
                target_id=target_id,
            )

    async def get_repo_graph(self, repo_id: str, depth: int = 2) -> dict:
        """Return all Module nodes and IMPORTS edges for a repo."""
        async with self._driver.session() as s:
            node_result = await s.run(
                """
                MATCH (m:Module {repo_id: $repo_id})
                RETURN m.node_id AS id, m.path AS path,
                       m.language AS language, m.size_bytes AS size_bytes
                """,
                repo_id=repo_id,
            )
            nodes = [dict(r) for r in await node_result.data()]

            edge_result = await s.run(
                """
                MATCH (a:Module {repo_id: $repo_id})-[r:IMPORTS]->(b:Module {repo_id: $repo_id})
                RETURN a.node_id AS source, b.node_id AS target, type(r) AS label
                """,
                repo_id=repo_id,
            )
            edges = [dict(r) for r in await edge_result.data()]

        return {"nodes": nodes, "edges": edges}

    async def get_subgraph(self, node_id: str, repo_id: str) -> dict:
        """Return the immediate neighbors of a node."""
        async with self._driver.session() as s:
            result = await s.run(
                """
                MATCH (m:Module {node_id: $node_id})-[r:IMPORTS]-(neighbor:Module {repo_id: $repo_id})
                RETURN
                  m.node_id AS src_id, m.path AS src_path, m.language AS src_lang, m.size_bytes AS src_size,
                  neighbor.node_id AS nb_id, neighbor.path AS nb_path,
                  neighbor.language AS nb_lang, neighbor.size_bytes AS nb_size,
                  type(r) AS label,
                  startNode(r).node_id AS edge_src, endNode(r).node_id AS edge_tgt
                """,
                node_id=node_id,
                repo_id=repo_id,
            )
            rows = await result.data()

        seen_nodes: dict[str, dict] = {}
        edges: list[dict] = []
        for row in rows:
            # Extract filename from path for label
            src_label = row["src_path"].split("/")[-1]
            nb_label = row["nb_path"].split("/")[-1]
            
            seen_nodes[row["src_id"]] = {
                "id": row["src_id"], 
                "path": row["src_path"], 
                "label": src_label,
                "language": row["src_lang"],
                "size_bytes": row["src_size"],
                "repo_id": repo_id
            }
            seen_nodes[row["nb_id"]] = {
                "id": row["nb_id"], 
                "path": row["nb_path"], 
                "label": nb_label,
                "language": row["nb_lang"],
                "size_bytes": row["nb_size"],
                "repo_id": repo_id
            }
            edges.append({"source": row["edge_src"], "target": row["edge_tgt"], "label": row["label"]})

        return {"nodes": list(seen_nodes.values()), "edges": edges}

    async def get_neighbors(self, node_id: str, repo_id: str, hops: int = 2) -> list[dict]:
        """Return node metadata for nodes within `hops` of a given node."""
        async with self._driver.session() as s:
            result = await s.run(
                f"""
                MATCH (src:Module {{node_id: $node_id}})
                MATCH (src)-[*1..{hops}]-(neighbor:Module {{repo_id: $repo_id}})
                RETURN DISTINCT neighbor.node_id AS id,
                       neighbor.path AS path,
                       neighbor.language AS language
                """,
                node_id=node_id,
                repo_id=repo_id,
            )
            return [dict(r) for r in await result.data()]

    async def search_modules_by_path(self, repo_id: str, fragment: str) -> list[dict]:
        async with self._driver.session() as s:
            result = await s.run(
                """
                MATCH (m:Module {repo_id: $repo_id})
                WHERE m.path CONTAINS $fragment
                RETURN m.node_id AS id, m.path AS path, m.language AS language
                LIMIT 20
                """,
                repo_id=repo_id,
                fragment=fragment,
            )
            return [dict(r) for r in await result.data()]

    async def get_entry_points(self, repo_id: str, limit: int = 8) -> list[dict]:
        """
        Return modules that are NOT imported by any other module in this repo
        (fan-in = 0), i.e. the natural entry points / top-level files.
        For each, also fetch up to 3 of its defined symbols (functions/classes).
        """
        async with self._driver.session() as s:
            result = await s.run(
                """
                MATCH (m:Module {repo_id: $repo_id})
                WHERE NOT EXISTS {
                    MATCH (:Module {repo_id: $repo_id})-[:IMPORTS]->(m)
                }
                // Prefer files that actually export symbols
                OPTIONAL MATCH (m)-[:DEFINES]->(sym:Symbol)
                WITH m,
                     count(sym) AS sym_count,
                     collect(sym.name)[0..3] AS top_symbols
                // Prefer files that import others (real entry files, not leaf utils with no imports)
                OPTIONAL MATCH (m)-[:IMPORTS]->(:Module {repo_id: $repo_id})
                WITH m, sym_count, top_symbols, count(*) AS fan_out
                ORDER BY fan_out DESC, sym_count DESC
                LIMIT $limit
                RETURN m.node_id  AS id,
                       m.path     AS path,
                       m.language AS language,
                       sym_count,
                       top_symbols,
                       fan_out
                """,
                repo_id=repo_id,
                limit=limit,
            )
            rows = await result.data()

        return [
            {
                "id": r["id"],
                "path": r["path"],
                "language": r["language"],
                "sym_count": r["sym_count"],
                "top_symbols": r["top_symbols"] or [],
                "fan_out": r["fan_out"],
            }
            for r in rows
        ]

    async def analyze_impact(self, node_id: str, repo_id: str) -> dict:
        """
        Compute impact analysis for a module node, scored relative to the
        actual distribution of all modules in the same repo.

        Risk is classified by percentile rank — not hardcoded thresholds —
        so a module in the top 20% of the repo's raw score distribution is
        HIGH regardless of the absolute number.
        """
        async with self._driver.session() as s:
            # ── Target node metrics ─────────────────────────────────────────

            fwd = await s.run(
                """
                MATCH (src:Module {node_id: $node_id})
                MATCH path = (src)-[:IMPORTS*1..]->(downstream:Module {repo_id: $repo_id})
                RETURN DISTINCT downstream.node_id AS id,
                       downstream.path AS path,
                       length(path) AS depth
                """,
                node_id=node_id, repo_id=repo_id,
            )
            fwd_rows = await fwd.data()

            rev = await s.run(
                """
                MATCH (src:Module {node_id: $node_id})
                MATCH (upstream:Module {repo_id: $repo_id})-[:IMPORTS*1..]->(src)
                RETURN DISTINCT upstream.node_id AS id, upstream.path AS path
                """,
                node_id=node_id, repo_id=repo_id,
            )
            rev_rows = await rev.data()

            fanout_res = await s.run(
                "MATCH (src:Module {node_id: $node_id})-[:IMPORTS]->(c) RETURN count(c) AS n",
                node_id=node_id,
            )
            fan_out = ((await fanout_res.data()) or [{"n": 0}])[0]["n"]

            fanin_res = await s.run(
                "MATCH (p)-[:IMPORTS]->(src:Module {node_id: $node_id}) RETURN count(p) AS n",
                node_id=node_id,
            )
            fan_in = ((await fanin_res.data()) or [{"n": 0}])[0]["n"]

            # ── Repo-wide distribution (raw scores for every module) ────────
            # Keep `m` in scope through every WITH so each OPTIONAL MATCH
            # binds to the correct module node.
            repo_dist_res = await s.run(
                """
                MATCH (m:Module {repo_id: $repo_id})
                OPTIONAL MATCH (up:Module {repo_id: $repo_id})-[:IMPORTS*1..]->(m)
                WITH m, count(DISTINCT up) AS up_count
                OPTIONAL MATCH (m)-[:IMPORTS*1..]->(dn:Module {repo_id: $repo_id})
                WITH m, up_count, count(DISTINCT dn) AS dn_count
                OPTIONAL MATCH (m)-[:IMPORTS]->(direct:Module {repo_id: $repo_id})
                WITH m, up_count, dn_count, count(DISTINCT direct) AS fo
                OPTIONAL MATCH (parent:Module {repo_id: $repo_id})-[:IMPORTS]->(m)
                RETURN m.node_id AS nid,
                       up_count,
                       dn_count,
                       fo,
                       count(DISTINCT parent) AS fi
                """,
                repo_id=repo_id,
            )
            repo_rows = await repo_dist_res.data()

        # ── Compute raw score for the target node ──────────────────────────
        affected_count = len(fwd_rows)
        upstream_count = len(rev_rows)
        max_depth = max((r["depth"] for r in fwd_rows), default=0)

        # Score uses only signals available for every node in the bulk query
        # so target and repo rows are on the same scale.
        def _raw(up: int, dn: int, fo: int, fi: int) -> float:
            return (up * 4) + (dn * 2) + (fo * 2) + (fi * 3)

        target_raw = _raw(upstream_count, affected_count, fan_out, fan_in)

        # ── Build repo distribution of raw scores ──────────────────────────
        all_raws: list[float] = []
        for row in repo_rows:
            all_raws.append(_raw(
                row.get("up_count") or 0,
                row.get("dn_count") or 0,
                row.get("fo") or 0,
                row.get("fi") or 0,
            ))

        total_modules = len(all_raws)

        # Percentile rank: fraction of repo modules this node scores higher than
        if total_modules <= 1:
            percentile = 100.0
        else:
            rank = sum(1 for s in all_raws if target_raw > s)
            percentile = (rank / (total_modules - 1)) * 100

        # impact_score = percentile (0–100), represents position in the repo
        impact_score = round(percentile)

        # ── Risk relative to repo distribution ─────────────────────────────
        # TOP 20 %  → HIGH   (this module is more critical than 80 % of repo)
        # 20–60 %   → MEDIUM
        # BOTTOM 40 % → LOW
        if percentile >= 80:
            risk_level, risk_emoji = "HIGH", "�"
        elif percentile >= 40:
            risk_level, risk_emoji = "MEDIUM", "🟡"
        else:
            risk_level, risk_emoji = "LOW", "�"

        explanation = (
            f"This module ranks in the {impact_score}th percentile of the repo by impact. "
            f"{upstream_count} module(s) transitively depend on it — changes here "
            f"can break {upstream_count} upstream caller(s). "
            f"It reaches {affected_count} downstream module(s) at a max depth of {max_depth}. "
            f"Direct fan-in: {fan_in}, fan-out: {fan_out}. "
            f"Risk is {'elevated' if risk_level != 'LOW' else 'low'} relative to the "
            f"{total_modules} modules in this repo."
        )

        return {
            "node_id": node_id,
            "impact_score": impact_score,
            "percentile": round(percentile, 1),
            "total_modules_in_repo": total_modules,
            "risk_level": risk_level,
            "risk_emoji": risk_emoji,
            "metrics": {
                "affected_count": affected_count,
                "upstream_count": upstream_count,
                "max_depth": max_depth,
                "fan_out": fan_out,
                "fan_in": fan_in,
            },
            "affected_nodes": [{"id": r["id"], "path": r["path"], "depth": r["depth"]} for r in fwd_rows],
            "upstream_nodes": [{"id": r["id"], "path": r["path"]} for r in rev_rows],
            "explanation": explanation,
        }


