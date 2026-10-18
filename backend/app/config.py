"""Settings from the environment and the repo-root .env (template: .env.example).

Optional secrets stay None here; the code that needs one raises a clear error when
it's missing.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    # FalkorDB
    falkordb_host: str = "localhost"
    falkordb_port: int = 6379
    lore_graph: str = "lore_herb"
    lore_demo_graph: str = "lore_demo"
    query_timeout_ms: int = 2000  # default timeout for reads (agent tools); writes can't take one
    # Redis ACL users (Phase 1.4, D018), created by docker-compose.yml from the same .env.
    falkordb_writer_user: str = "lore_writer"
    falkordb_writer_password: str | None = None
    falkordb_reader_user: str = "lore_reader"
    falkordb_reader_password: str | None = None
    falkordb_admin_password: str | None = None  # user "default": ops and contract tests only

    # LLM (Anthropic)
    anthropic_api_key: str | None = None
    lore_extract_model: str = "claude-haiku-4-5"
    lore_answer_model: str = "claude-sonnet-5-5"
    lore_daily_spend_cap_usd: float = 20.0

    # Embeddings
    lore_embed_model: str = "BAAI/bge-small-en-v1.5"
    lore_embed_dim: int = 384

    # Locations
    data_dir: Path = REPO_ROOT / "data"
    eval_dir: Path = REPO_ROOT / "eval"
    queries_dir: Path = REPO_ROOT / "backend" / "queries"


@lru_cache
def get_settings() -> Settings:
    return Settings()
