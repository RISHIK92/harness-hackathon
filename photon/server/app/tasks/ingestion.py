from __future__ import annotations
import json
import asyncio
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Optional

import structlog
import redis
from sqlmodel import create_engine, Session, select

from app.tasks.celery_app import celery_app
from app.config import get_settings
from app.models import Repo, Job, RepoStatus, JobStatus, RepoSourceType

log = structlog.get_logger()
settings = get_settings()

_sync_engine = None

def get_sync_engine():
    global _sync_engine
    if _sync_engine is None:
        from sqlalchemy import create_engine as ce
        _sync_engine = ce(settings.sync_database_url)
    return _sync_engine


def _publish(r: redis.Redis, repo_id: str, job_id: str, phase: str, progress: int, message: str):
    """Publish a progress event to Redis pub/sub."""
    payload = json.dumps({
        "repo_id": repo_id,
        "job_id": job_id,
        "phase": phase,
        "progress": progress,
        "message": message,
    })
    r.publish(f"job:{repo_id}", payload)
    r.publish(f"job:{job_id}", payload)


def _update_job(session: Session, job_id: str, **kwargs):
    job = session.get(Job, job_id)
    if job:
        for k, v in kwargs.items():
            setattr(job, k, v)
        session.commit()


def _update_repo(session: Session, repo_id: str, **kwargs):
    repo = session.get(Repo, repo_id)
    if repo:
        for k, v in kwargs.items():
            setattr(repo, k, v)
        session.commit()


@celery_app.task(bind=True, name="app.tasks.ingestion.run_ingestion", max_retries=2)
def run_ingestion(self, repo_id: str, job_id: str):
    """
    Full ingestion pipeline:
    1. Fetch repo
    2. Build manifest
    3. Parse files (Tree-sitter)
    4. Build Neo4j graph
    5. Chunk + embed → Qdrant
    6. Update summary card
    """
    started_at = time.monotonic()
    r = redis.from_url(settings.redis_url)
    engine = get_sync_engine()

    def publish(phase: str, progress: int, message: str):
        _publish(r, repo_id, job_id, phase, progress, message)
        with Session(engine) as s:
            _update_job(s, job_id, phase=phase, progress=progress,
                       status=JobStatus.RUNNING, message=message)

    try:
        publish("starting", 0, "Ingestion started")

        with Session(engine) as session:
            repo: Optional[Repo] = session.get(Repo, repo_id)
            if not repo:
                raise ValueError(f"Repo {repo_id} not found")
            repo_source = repo.source_type
            repo_url = repo.source_url
            repo_name = repo.name
            existing_path = repo.local_path
            github_installation_id = repo.github_installation_id
            workspace_id = repo.workspace_id

        # ── Phase 1: Fetch ─────────────────────────────────────────────────
        publish("cloning", 5, "Fetching repository...")
        from app.services.repo_fetcher import clone_github_repo, use_local_path

        if existing_path:
            local_path = existing_path
        elif repo_source == RepoSourceType.GITHUB:
            if github_installation_id:
                # Imported via the GitHub App picker — use a short-lived,
                # narrowly-scoped installation token instead of the single
                # static GITHUB_TOKEN (which may not even have access to a
                # private org repo brought in this way).
                from app.services.github_app_auth import get_installation_token
                clone_token = get_installation_token(github_installation_id)
            else:
                clone_token = settings.github_token or None
            local_path = clone_github_repo(repo_url, repo_id, token=clone_token)
        elif repo_source == RepoSourceType.LOCAL:
            local_path = use_local_path(repo_url, repo_id)
        else:
            raise ValueError(f"Cannot fetch source type {repo_source}")

        with Session(engine) as s:
            _update_repo(s, repo_id, local_path=local_path, status=RepoStatus.INGESTING)

        publish("cloning", 15, f"Repository ready at {local_path}")

        # ── Phase 2: Manifest ──────────────────────────────────────────────
        publish("scanning", 20, "Building file manifest...")
        from app.services.manifest_builder import build_manifest
        manifest = build_manifest(local_path)
        file_count = len(manifest)
        publish("scanning", 25, f"Found {file_count} source files")

        # Language breakdown
        from collections import Counter
        lang_counter = Counter(f["language"] for f in manifest)
        lang_breakdown = dict(lang_counter.most_common(10))

        # ── Phase 3: Parse + Graph ─────────────────────────────────────────
        publish("parsing", 30, "Parsing ASTs and building dependency graph...")
        from app.core.parser.tree_sitter_parser import parse_file
        from app.core.parser.language_detector import PARSEABLE_LANGUAGES
        from app.core.graph.builder import Neo4jClient

        graph = Neo4jClient()

        # Use asyncio to run async Neo4j operations from sync Celery context
        loop = asyncio.new_event_loop()
        loop.run_until_complete(graph.ensure_schema())

        total_functions = 0
        all_modules: dict[str, str] = {}  # rel_path -> module_node_id

        # ── Pass 3a: Upsert ALL module nodes first ─────────────────────────
        # Must complete before creating any import edges so that every
        # potential IMPORTS target already exists in Neo4j.
        for idx, file_info in enumerate(manifest):
            rel_path = file_info["path"]
            language = file_info["language"]

            module_id = loop.run_until_complete(
                graph.upsert_module(repo_id, rel_path, language, file_info["size_bytes"])
            )
            all_modules[rel_path] = module_id

            if idx % 50 == 0:
                progress = 30 + int((idx / max(file_count, 1)) * 15)
                publish("parsing", progress, f"Indexed {idx + 1}/{file_count} modules")

        publish("parsing", 45, f"All {file_count} module nodes created. Resolving imports...")

        # ── Pass 3b: Parse symbols + create IMPORTS edges ─────────────────
        parsed_cache: dict[str, object] = {}
        total_imports_resolved = 0
        total_imports_skipped = 0
        
        for idx, file_info in enumerate(manifest):
            rel_path = file_info["path"]
            language = file_info["language"]
            abs_path = file_info["abs_path"]
            module_id = all_modules[rel_path]

            if language in PARSEABLE_LANGUAGES:
                try:
                    parsed = parse_file(abs_path, language)
                    parsed_cache[rel_path] = parsed
                except Exception as exc:
                    log.warning("parse.failed", path=rel_path, error=str(exc))
                    continue

                # Upsert symbols into graph
                for sym in parsed.symbols:
                    loop.run_until_complete(
                        graph.upsert_symbol(
                            repo_id, module_id,
                            sym.name, sym.kind,
                            sym.start_line, sym.end_line, sym.docstring
                        )
                    )
                    if sym.kind in ("function", "method"):
                        total_functions += 1

                # Resolve imports and create IMPORTS edges
                # All target modules now exist from Pass 3a
                for imp in parsed.imports:
                    resolved = _resolve_import(imp, rel_path, language)
                    if resolved:
                        # debug, not info: this fires once per resolved
                        # import — 5,366 times for an 83-file repo — and
                        # every line is formatted and written through
                        # Celery's logging on the critical path.
                        log.debug("import.resolved", from_path=rel_path, to=resolved, raw=imp, language=language)
                        loop.run_until_complete(
                            graph.upsert_import_edge(module_id, resolved, repo_id)
                        )
                        total_imports_resolved += 1
                    else:
                        log.debug("import.skipped", from_path=rel_path, raw=imp, language=language)
                        total_imports_skipped += 1

            progress = 45 + int((idx / max(file_count, 1)) * 17)
            if idx % 20 == 0:
                publish("parsing", progress, f"Parsed {idx + 1}/{file_count} files")

        log.info("imports.summary", resolved=total_imports_resolved, skipped=total_imports_skipped, files=file_count)
        loop.run_until_complete(graph.close())
        loop.close()

        publish("graphing", 62, "Dependency graph built")

        # ── Phase 4: Chunk + Embed ─────────────────────────────────────────
        publish("embedding", 65, "Generating embeddings...")
        from app.core.embedding.chunker import chunk_file
        from app.core.embedding.embedder import delete_repo_chunks, upsert_chunks

        # Evict this repo's previous chunks before writing the new ones.
        #
        # Chunk ids are position-based (`{repo_id}:{path}:{idx}`), so a
        # re-ingest OVERWRITES chunk 0..n and orphans everything past n —
        # and it orphans every chunk whose boundaries moved, which is most of
        # them whenever the chunker itself changes. Measured on the live
        # corpus: drillController.ts held 50 chunks with duplicate ranges
        # (L298-L340 twice, L346-L387 twice) and one survivor from the old
        # chunker claiming L1-L1315 while holding 72 lines — a fabricated
        # locator, which the standing rule forbids outright, still being
        # retrieved and cited months later.
        #
        # Deleting first makes a re-ingest authoritative instead of additive.
        # The cost is a window where the repo has no chunks: a question asked
        # mid-ingest gets "nothing found" rather than stale-but-present code,
        # which is the better failure — an honest gap beats a confident
        # citation of a file that no longer looks like that.
        delete_repo_chunks(repo_id)

        all_chunks = []
        for file_info in manifest:
            rel_path = file_info["path"]
            language = file_info["language"]
            abs_path = file_info["abs_path"]

            # Reuse already-parsed result from Pass 3b where available
            parsed = parsed_cache.get(rel_path)
            if parsed is None:
                if language in PARSEABLE_LANGUAGES:
                    try:
                        parsed = parse_file(abs_path, language)
                    except Exception:
                        continue
                else:
                    from app.core.parser.tree_sitter_parser import ParsedFile
                    try:
                        with open(abs_path, encoding="utf-8", errors="replace") as f:
                            raw = f.read()
                        parsed = ParsedFile(path=abs_path, language=language, raw_text=raw)
                    except Exception:
                        continue

            chunks = chunk_file(repo_id, rel_path, parsed, local_path)
            # Tagged here rather than threaded through chunk_file()'s
            # signature — metadata is spread straight into the Qdrant
            # payload by upsert_chunks(), and this is the only thing a
            # cross-repo workspace search (app.agent.loop's multi-repo
            # disambiguation) needs beyond what chunker.py already builds.
            for c in chunks:
                c.metadata["workspace_id"] = workspace_id
            all_chunks.extend(chunks)

        publish("embedding", 70, f"Chunked into {len(all_chunks)} segments. Embedding...")

        # Upsert in batches, CONCURRENTLY.
        #
        # Measured: embedding was ~19s of a ~40s ingest for an 83-file repo,
        # and every second of it was spent waiting on a network round-trip
        # to the embedding API and then to Qdrant — ~19 batches, strictly
        # one after another. The work is I/O-bound, not CPU-bound, so the
        # GIL is not the limit here and threads are the right tool.
        #
        # Concurrency is deliberately modest: the embedding provider rate-
        # limits, and a failed batch loses that slice of the repo's
        # searchable content. Per-batch failures are still swallowed and
        # logged individually, exactly as before, so one bad batch cannot
        # take down an otherwise good ingest.
        batch_size = settings.embedding_batch_size
        batches = [all_chunks[i : i + batch_size] for i in range(0, len(all_chunks), batch_size)]
        completed = 0
        if batches:
            with ThreadPoolExecutor(max_workers=settings.embedding_concurrency) as pool:
                futures = {pool.submit(upsert_chunks, b): n for n, b in enumerate(batches)}
                for future in as_completed(futures):
                    n = futures[future]
                    try:
                        future.result()
                    except Exception as exc:
                        log.error("embed.batch_failed", batch=n, error=str(exc))
                    completed += 1
                    # Progress is now by COMPLETED batches, since they no
                    # longer finish in order.
                    progress = 70 + int((completed / len(batches)) * 25)
                    publish(
                        "embedding",
                        progress,
                        f"Embedded {min(completed * batch_size, len(all_chunks))}/{len(all_chunks)} chunks",
                    )

        # ── Phase 5: Summary card ──────────────────────────────────────────
        publish("finalizing", 97, "Generating summary card...")
        top_modules = sorted(manifest, key=lambda x: x["size_bytes"], reverse=True)
        top_module_paths = [m["path"] for m in top_modules[:10]]

        with Session(engine) as s:
            _update_repo(
                s, repo_id,
                status=RepoStatus.READY,
                # Feeds the pre-import time estimate; see services/estimate.py.
                ingest_seconds=round(time.monotonic() - started_at, 1),
                file_count=file_count,
                function_count=total_functions,
                language_breakdown=lang_breakdown,
                top_modules=top_module_paths,
            )
            _update_job(
                s, job_id,
                status=JobStatus.DONE,
                progress=100,
                phase="done",
                message="Ingestion complete",
                finished_at=datetime.utcnow(),
            )

        publish("done", 100, "Ingestion complete! Repository is ready.")
        log.info(
            "ingestion.complete",
            repo_id=repo_id,
            files=file_count,
            seconds=round(time.monotonic() - started_at, 1),
        )

    except Exception as exc:
        log.error("ingestion.failed", repo_id=repo_id, error=str(exc))
        with Session(engine) as s:
            _update_repo(s, repo_id, status=RepoStatus.FAILED, error_message=str(exc))
            _update_job(
                s, job_id,
                status=JobStatus.FAILED,
                phase="failed",
                message=str(exc),
                finished_at=datetime.utcnow(),
            )
        _publish(r, repo_id, job_id, "failed", 0, str(exc))
        raise


def _resolve_import(import_str: str, current_file: str, language: str) -> Optional[str]:
    """
    Extract a resolvable module path from an import statement string.
    Returns a path fragment to match against module paths in the graph,
    or None if the import is unresolvable (stdlib, third-party, etc.).
    """
    import re
    import_str = import_str.strip()

    if language == "python":
        # Skip Python stdlib and known third-party packages
        _PYTHON_STDLIB = {
            "os", "sys", "re", "io", "abc", "ast", "copy", "csv", "math",
            "json", "time", "uuid", "enum", "typing", "types", "pathlib",
            "hashlib", "logging", "inspect", "itertools", "functools",
            "datetime", "dataclasses", "collections", "contextlib",
            "threading", "multiprocessing", "subprocess", "asyncio",
            "socket", "ssl", "http", "urllib", "email", "html", "xml",
            "struct", "pickle", "shelve", "sqlite3", "unittest", "warnings",
            "traceback", "platform", "shutil", "tempfile", "glob",
            "string", "textwrap", "pprint", "decimal", "fractions",
            "random", "statistics", "base64", "binascii", "codecs",
        }
        _KNOWN_THIRD_PARTY = {
            "fastapi", "pydantic", "sqlmodel", "sqlalchemy", "celery",
            "redis", "neo4j", "qdrant_client", "voyageai", "google",
            "structlog", "httpx", "uvicorn", "starlette", "alembic",
            "pathspec", "gitpython", "tiktoken", "networkx",
            "tree_sitter", "psycopg2", "asyncpg",
        }

        # from .utils import foo  (relative) → resolve against current_file dir
        m = re.match(r'from\s+(\.+)([a-zA-Z0-9_.]*)\s+import', import_str)
        if m:
            dots = len(m.group(1))
            mod = m.group(2).replace(".", "/")
            # Walk up `dots` levels from current file's directory
            parts = current_file.replace("\\", "/").split("/")
            base_parts = parts[: max(0, len(parts) - dots)]
            if mod:
                base_parts.append(mod)
            return "/".join(base_parts) if base_parts else None

        # from app.auth import bar  /  import app.auth
        m = re.match(r'from\s+([a-zA-Z0-9_.]+)\s+import', import_str) or \
            re.match(r'import\s+([a-zA-Z0-9_.]+)', import_str)
        if m:
            top_level = m.group(1).split(".")[0]
            if top_level in _PYTHON_STDLIB or top_level in _KNOWN_THIRD_PARTY:
                return None
            return m.group(1).replace(".", "/")

    elif language in ("javascript", "typescript", "tsx", "jsx"):
        m = re.search(r'(?:from|require\()\s*[\'"]([^\'"]+)[\'"]', import_str)
        if not m:
            return None
        path = m.group(1)

        # Relative imports: ./foo  ../bar/baz
        if path.startswith("."):
            # Resolve relative to current file's directory
            from pathlib import Path
            current_dir = Path(current_file).parent
            
            # Resolve the relative path
            try:
                resolved_path = (current_dir / path).resolve()
                # Convert back to string and get relative to root
                # Since current_file is already relative, we need to resolve relative to it
                current_parts = current_file.replace("\\", "/").split("/")
                # Remove the filename, keep directory
                current_dir_parts = current_parts[:-1]
                
                # Process .. and . in the import path
                import_parts = path.replace("\\", "/").split("/")
                result_parts = current_dir_parts[:]
                
                for part in import_parts:
                    if part == "..":
                        if result_parts:
                            result_parts.pop()
                    elif part == ".":
                        continue
                    else:
                        result_parts.append(part)
                
                # Join and strip extensions
                fragment = "/".join(result_parts)
                fragment = re.sub(r'\.(ts|tsx|js|jsx|mjs|cjs)$', "", fragment)
                return fragment if fragment else None
            except Exception:
                # Fallback to old logic
                fragment = re.sub(r'^\.\.?/', "", path)
                fragment = re.sub(r'^\.\.?/', "", fragment)
                fragment = re.sub(r'\.(ts|tsx|js|jsx|mjs|cjs)$', "", fragment)
                return fragment if fragment else None

        # Absolute imports starting with @ are usually aliases or node_modules
        # Only keep @/ style aliases (common in Next.js / Vite)
        if path.startswith("@/"):
            fragment = path[2:]
            fragment = re.sub(r'\.(ts|tsx|js|jsx)$', "", fragment)
            return fragment
        # All other bare specifiers (node_modules) → skip
        return None

    elif language == "go":
        # import "github.com/org/repo/pkg/foo" → keep only local sub-paths
        m = re.search(r'"([^"]+)"', import_str)
        if m:
            path = m.group(1)
            # Heuristic: local packages have short paths (no domain-like prefix)
            parts = path.split("/")
            if "." not in parts[0]:  # no domain in first segment → local
                return path
        return None

    elif language == "rust":
        # use crate::module::sub  /  use super::foo
        m = re.match(r'use\s+(crate|super|self)::([a-zA-Z0-9_:]+)', import_str)
        if m:
            return m.group(2).replace("::", "/")
        return None

    elif language == "java":
        # import com.example.project.SomeClass  → keep non-stdlib
        m = re.match(r'import\s+(?:static\s+)?([a-zA-Z0-9_.]+)', import_str)
        if m:
            top = m.group(1).split(".")[0]
            if top in ("java", "javax", "org", "com", "sun"):
                # Only keep if it looks like an in-repo path
                pass
            return m.group(1).replace(".", "/")
        return None

    return None
