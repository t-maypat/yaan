"""Settings from the environment and the repo-root .env (template: .env.example).

Optional secrets stay None here; the code that needs one raises a clear error when
it's missing.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    # FalkorDB
    falkordb_host: str = (
        "127.0.0.1"  # not "localhost": on Windows it tries IPv6 first, +2 s per connection
    )
    falkordb_port: int = 6379
    falkordb_tag: str = "unknown"  # the pinned image (D009); snapshots record it
    lore_graph: str = "lore_herb"
    lore_demo_graph: str = "lore_demo"
    # The graph the API serves (Ask, Inbox, live demo). Eval and batch ingest use lore_graph.
    lore_api_graph: str = "lore_demo"
    lore_ingest_secret: str | None = None  # POST /ingest/artifact needs it in X-Lore-Secret
    # Data mode (D097). "demo": the HERB company, and connectors take only labeled synthetic
    # items into lore_demo. "org": a team's own tools feed lore_org_graph, people come from
    # people.yaml or are registered on first sight, and nothing is copied from HERB.
    lore_data_mode: Literal["demo", "org"] = "demo"
    lore_org_graph: str = "yaan_org"
    # Who is asking (D100, app/auth.py): auto = token in org mode, none in the demo
    lore_auth: Literal["auto", "none", "token", "header"] = "auto"
    lore_auth_header: str = "X-Forwarded-Email"  # set by a sign-in proxy in `header` mode
    # The proxy also sends X-Lore-Proxy-Secret with this value, so a request that reaches Lore
    # around the proxy can't name itself (an IP check breaks once uvicorn trusts the proxy's
    # X-Forwarded-For for per-IP limits: the client address becomes the user's).
    lore_auth_proxy_secret: str | None = None
    # MCP server (17.1): offer `ask` (the full agent, an LLM call) besides the read-only tools;
    # extra Host values `/mcp` accepts beyond localhost (DNS-rebinding guard), as a JSON list
    lore_mcp_ask: bool = True
    lore_mcp_hosts: list[str] = []
    # Connectors (Phase 17, docs/connectors.md): a connector runs when its settings are set
    slack_bot_token: str | None = None
    slack_app_token: str | None = None
    github_token: str | None = None
    github_repo: str | None = None
    jira_base_url: str | None = None
    jira_email: str | None = None
    jira_api_token: str | None = None
    jira_project: str | None = None
    notion_token: str | None = None
    notion_page_id: str | None = None
    # Experimental connectors (D090): off unless all of a connector's settings are set. Google
    # and Zoom take an access token you already hold; the OAuth flow is not built.
    linear_api_key: str | None = None
    linear_team: str | None = None
    gitlab_token: str | None = None
    gitlab_project: str | None = None
    gitlab_base_url: str | None = None  # optional, default https://gitlab.com
    confluence_base_url: str | None = None
    confluence_email: str | None = None
    confluence_api_token: str | None = None
    confluence_space_id: str | None = None
    google_drive_access_token: str | None = None
    google_drive_folder_id: str | None = None
    gmail_access_token: str | None = None
    gmail_label: str | None = None
    google_meet_access_token: str | None = None
    google_meet_space: str | None = None
    zoom_access_token: str | None = None
    zoom_user_id: str | None = None  # optional, default "me"
    discord_bot_token: str | None = None
    discord_channel_id: str | None = None
    query_timeout_ms: int = 2000  # default timeout for reads (agent tools); writes can't take one
    # Redis ACL users (Phase 1.4, D018), created by docker-compose.yml from the same .env.
    falkordb_writer_user: str = "lore_writer"
    falkordb_writer_password: str | None = None
    falkordb_reader_user: str = "lore_reader"
    falkordb_reader_password: str | None = None
    falkordb_admin_password: str | None = None  # user "default": ops and contract tests only

    # LLM (Anthropic). Haiku 5.5 for both roles keeps the whole project at a few dollars (D033).
    anthropic_api_key: str | None = None
    lore_extract_model: str = "claude-haiku-5-5"
    lore_answer_model: str = "claude-haiku-5-5"
    lore_extract_effort: str = "low"
    lore_answer_effort: str = "medium"
    # The eval judge stands in for HERB's gpt-4o judge (Phase 5, D035).
    lore_judge_model: str = "claude-haiku-5-5"
    lore_judge_effort: str = "low"
    # Optional LiteLLM provider (D091, experimental): "anthropic" (default) is the SDK path with
    # the original cache keys; "litellm" sends every call through litellm, one model per role.
    lore_llm_provider: Literal["anthropic", "litellm"] = "anthropic"
    lore_litellm_model_extract: str | None = None
    lore_litellm_model_answer: str | None = None
    lore_litellm_model_judge: str | None = None
    lore_daily_spend_cap_usd: float = 2.0  # llm.py refuses a call that could cross it
    # Org mode (D124): answers may use this much of the daily cap (default: half), so reading
    # new items keeps the rest; and each signed-in person's answers this much a day (~70
    # questions at Haiku 5.5's measured ~$0.0035 each). The demo and the eval share one cap.
    lore_answer_spend_cap_usd: float | None = None
    lore_person_spend_cap_usd: float = 0.25
    # Optional YAML of {model: [input, output, cache write, cache read] USD per million tokens}
    # that overrides or extends llm.PRICES (D121): a new model, or a price change, needs no code.
    lore_prices_file: Path | None = None
    # Public deployment guards (D081). Cache-only: replay cached answers, never call the API.
    lore_llm_cache_only: bool = False
    lore_ask_per_ip_per_day: int = 60  # /ask, per client IP when nobody is signed in, 24 h
    lore_ask_global_per_minute: int = 30  # /ask, all clients together: a swarm of many IPs
    lore_ask_concurrency: int = 4  # answers in flight at once; more get 503
    lore_requests_per_ip_per_minute: int = 300  # API routes, per IP when nobody is signed in
    # A signed-in person has budgets of their own (D124), so a team behind one office address
    # or one TLS proxy doesn't share an IP's: /ask, MCP `ask` and Slack questions per person
    lore_ask_per_person_per_minute: int = 6
    lore_ask_per_person_per_day: int = 200
    lore_requests_per_person_per_minute: int = 600

    # Embeddings
    lore_embed_model: str = "BAAI/bge-small-en-v1.5"
    lore_embed_dim: int = 384

    # Locations
    data_dir: Path = REPO_ROOT / "data"
    models_dir: Path = REPO_ROOT / "data" / "models"  # downloaded embedding model files
    cache_dir: Path = REPO_ROOT / "data" / "cache"  # embedding (and later LLM) disk caches
    eval_dir: Path = REPO_ROOT / "eval"
    queries_dir: Path = REPO_ROOT / "backend" / "queries"

    def api_graph(self) -> str:
        """The graph the API and the MCP server serve: the org graph in org mode."""
        return self.lore_org_graph if self.lore_data_mode == "org" else self.lore_api_graph


@lru_cache
def get_settings() -> Settings:
    return Settings()
