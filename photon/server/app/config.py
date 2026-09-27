from __future__ import annotations
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, field_validator


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # App
    app_env: str = "development"
    secret_key: str = "changeme"
    api_key: str = "yasml-dev-key"

    # PostgreSQL
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "yasml"
    postgres_user: str = "yasml"
    postgres_password: str = "yasml_secret"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def sync_database_url(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Neo4j
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "neo4j_secret"

    # Qdrant
    qdrant_host: str = "localhost"
    qdrant_port: int = 6333

    # Gemini — unused. Kept only because the key is already in .env; every
    # actual LLM call (text AND vision) goes through OpenRouter below.
    # Gemini's own free tier is capped at 20 requests/DAY, confirmed to hit
    # that wall on BOTH the text model (gemini-2.5-*, Phase 3) and the
    # vision model (gemini-3.7-flash, screen-frame analysis) — nowhere near
    # enough for a live call. Don't route anything through this key again
    # without a paid tier.
    gemini_api_key: str = Field(default="", alias="GEMINI_API_KEY")
    gemini_chat_model: str = "gemini-2.5-pro"

    # OpenRouter — text generation (agent plan/compose, check_conflict judge,
    # web console query streaming) AND vision (screen-frame analysis). See
    # note above for why both live here instead of on Gemini directly.
    openrouter_api_key: str = Field(default="", alias="OPENROUTER_API_KEY")
    # Latency, measured with the agent's own plan/compose prompts against a
    # real 22-item evidence set (not vendor benchmarks): deepseek-v4-flash
    # 11.3-16.6s per turn of LLM time, gemini-3.7-flash 6.1-7.6s,
    # gemini-3.1-flash-lite 3.7-9.7s (unstable), gpt-oss-120b 16.8s+,
    # claude-haiku-4.5 4.6-6.1s -> gemini-3.5-flash-lite 2.43-2.49s with
    # clean JSON on every run. That is the whole reason this is the text
    # model; do not swap it without re-running the same measurement (the
    # bench lives in git history / CLAUDE.md).
    openrouter_chat_model: str = "google/gemini-3.5-flash-lite"
    # Measured on a real screen frame with known text (the bench scores
    # whether the description still READS the screen, not just speed):
    # gemini-3.7-flash 3.55-3.78s, gemini-3.1-flash-lite 1.27-1.57s,
    # gemini-3.5-flash-lite 1.03-1.96s — all 3/3 on the strings that had to
    # survive. Same model as the text path now, ~3.4x faster than before.
    #
    # Also measured, and deliberately NOT changed: shrinking the frame does
    # not help (768px was no faster; 512px was no faster AND dropped an
    # on-screen string), and a terser prompt with a 120-token cap was no
    # faster than the careful 300-token one — so the frame stays at 1024px
    # and the prompt keeps its "do not guess outside the frame" wording.
    openrouter_vision_model: str = "google/gemini-3.5-flash-lite"

    # Voyage AI (embeddings)
    voyage_api_key: str = Field(default="", alias="VOYAGE_API_KEY")
    voyage_embedding_model: str = "voyage-code-3"

    # GitHub — a single static PAT, used only as a fallback for repos
    # connected by pasting a URL. Real org/private-repo access goes through
    # the GitHub App below instead (app/services/github_app_auth.py).
    github_token: str = ""

    # GitHub App — "Sign in with GitHub" (routers/auth.py) and per-workspace
    # "Connect GitHub" installations (routers/github_app.py). Populated by
    # the one-time manifest bootstrap flow (routers/dev_github_setup.py);
    # empty until that's run once. github_app_private_key is a PEM string —
    # set it in .env with literal "\n" for newlines, e.g.
    # GITHUB_APP_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----\nMII...\n-----END RSA PRIVATE KEY-----\n"
    github_app_id: str = ""
    github_app_slug: str = ""
    github_app_client_id: str = ""
    github_app_client_secret: str = ""
    github_app_private_key: str = ""
    github_app_webhook_secret: str = ""  # verifies POST /api/integrations/github/webhook

    @field_validator("github_app_private_key")
    @classmethod
    def _normalise_pem(cls, value: str) -> str:
        """Turn a .env-friendly one-line PEM back into a real PEM.

        A private key is multi-line, and .env files are line-based, so the
        key has to be stored with literal backslash-n. Nothing unescaped it
        before, so the crypto library received actual backslashes and
        failed with `InvalidData(Invalid symbol 92, offset 0)` — 92 being
        the ASCII code for "\\". Doing it here means every consumer
        (JWT signing, and anything added later) gets a usable key rather
        than each having to remember.

        Tolerates all three shapes seen in practice: escaped one-liner,
        a value wrapped in quotes, and an already-real multi-line PEM.
        """
        if not value:
            return value
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if "\\n" in value:
            value = value.replace("\\n", "\n")
        return value.strip() + "\n"  # PEM parsers want a trailing newline

    # The fictional "Meridian" corpus used to rehearse the demo scenarios.
    # OFF by default: with it on, a workspace that has connected nothing
    # still answers questions — from invented accounts and invented
    # tickets, with citations indistinguishable from real ones.
    enable_demo_corpus: bool = False

    # ─── Slack ───────────────────────────────────────────────────────────
    # A Slack app is per-deployment, created once from the manifest at
    # /dev/slack-app/new. The bot token that OAuth returns is stored
    # ENCRYPTED (app/core/crypto.py), not in these settings — this is only
    # the app's own identity.
    slack_client_id: str = ""
    slack_client_secret: str = ""
    slack_signing_secret: str = ""

    # Used to build the manifest's redirect_url/hook_attributes.url and the
    # OAuth redirect_uri — must be a URL GitHub can redirect a browser back
    # to (localhost is fine for the manifest/OAuth flows below; it is NOT
    # reachable for the GitHub webhook, which needs a public URL).
    public_base_url: str = "http://localhost:8000"
    client_base_url: str = "http://localhost:3000"

    # The harness service (`python -m harness.service`) — the hands behind
    # agent jobs. Photon plans and fixes tickets through it and never runs
    # a model against a checkout itself. The token must match the service's
    # HARNESS_SERVICE_TOKEN; empty is only valid while both are on loopback.
    harness_service_url: str = "http://127.0.0.1:8765"
    harness_service_token: str = ""
    # How long a job may sit in one harness run before Photon stops waiting
    # and escalates. The harness keeps its own budget; this is the backstop.
    harness_run_timeout_s: int = 3600
    # A GitHub issue that is assigned to a member, or labelled with this,
    # becomes an agent job for the assignee's agent.
    agent_fix_label: str = "photon-fix"
    # Signing secret of the Linear webhook (Settings → API → Webhooks) that
    # posts Issue and Comment events to /api/integrations/linear/webhook.
    linear_webhook_secret: str = ""

    # Storage
    repos_storage_path: str = "/tmp/yasml-repos"

    # Chunk settings
    chunk_max_tokens: int = 512
    embedding_batch_size: int = 100
    # How many embedding batches to send at once. The work is network-bound
    # (embedding API + Qdrant), so this is the main lever on ingest time.
    # Kept modest because the provider rate-limits; raise only with evidence.
    embedding_concurrency: int = 6

    # Query settings
    top_k_vector: int = 10
    top_k_graph_hops: int = 3

    # Reranking. Dense retrieval overfetches `rerank_pool` candidates and a
    # cross-encoder picks the final top_k. Measured on the seed repo:
    # MRR 0.47 -> 0.85, top-1 2/8 -> 6/8, top-3 5/8 -> 8/8, for ~310ms.
    # pool=20 was the sweet spot — 15 lost recall, 30 bought nothing, and
    # latency is network-bound so it barely moves with pool size.
    # How much of a retrieved chunk reaches the compose step. 800 (the old
    # hard-coded value) truncated 80% of a real TypeScript repo's chunks and
    # caused the agent to abstain on facts it had already retrieved. Raising
    # it costs prompt size, so it is tunable rather than fixed.
    evidence_snippet_max_chars: int = 2400

    # Skip the planning LLM call when every available tool is a plain
    # search. Measured: the plan call is ~2.3s of a 5.3s turn, and a
    # trivial 20-token call to the same OpenRouter endpoint costs ~1.7s,
    # so it is almost entirely provider round-trip. Narrow by design — see
    # app/agent/loop.py::_fast_path_calls.
    agent_fast_path: bool = True

    # Who /dev/ask acts as when the request doesn't say. Only read by
    # that dev-only router (app/routers/dev_ask.py), which is not mounted
    # in production at all. Saves passing as_email on every call on a box
    # whose user table is mostly accounts the test suites left behind.
    dev_ask_email: str = ""

    # ─── Whisper mode ────────────────────────────────────────────────────
    # A meeting-bot vendor, only needed to join Meet/Teams/Zoom on our
    # behalf. Whisper works without it: Photon's own calls feed it
    # directly, and anything else can POST transcript lines to
    # /api/whisper/sessions/{id}/lines. Unset here — the adapter in
    # app/services/whisper/providers.py is written but unverified.
    recall_api_key: str = ""
    recall_api_base: str = "https://us-east-1.recall.ai"
    # recallai_streaming works on every platform with no host setup;
    # meeting_captions is free but needs captions switched on in the call.
    recall_transcript_provider: str = "recallai_streaming"

    rerank_enabled: bool = True
    rerank_model: str = "rerank-2.5-lite"
    rerank_pool: int = 20

    # JWT
    jwt_secret_key: str = Field(default="change-me-in-production-jwt-secret", alias="JWT_SECRET_KEY")
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 10080  # 7 days


@lru_cache
def get_settings() -> Settings:
    return Settings()
