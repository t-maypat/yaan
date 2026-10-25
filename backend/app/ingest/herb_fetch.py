"""Download HERB into data/herb at a pinned revision. Built in Phase 2.1.

HERB (Salesforce AI Research; synthetic; CC BY-NC 4.0) is public and ungated on the Hugging
Face Hub. The files sit at the repo root: metadata/*.json and products/<Product>.json (30).
The revision is pinned so every rebuild and every eval run sees the same bytes.
Spec: docs/CONTEXT.md §6; verified layout: docs/herb_format.md.
"""

from pathlib import Path

from huggingface_hub import snapshot_download

from app.config import get_settings

HERB_REPO = "Salesforce/HERB"
HERB_REVISION = "a00bca08f9118e482e6de9951fdcb654fbed5343"  # Hub commit of 2025-07-01
METADATA_FILES = ("customers_data.json", "employee.json", "salesforce_team.json")
PRODUCT_COUNT = 30


def herb_dir() -> Path:
    return get_settings().data_dir / "herb"


def fetch_herb(dest: Path | None = None, revision: str = HERB_REVISION) -> Path:
    """Download (or reuse) the pinned snapshot into `dest` and check it is complete."""
    dest = dest or herb_dir()
    snapshot_download(
        repo_id=HERB_REPO,
        repo_type="dataset",
        revision=revision,
        local_dir=dest,
        allow_patterns=["metadata/*.json", "products/*.json", "README.md"],
    )
    check_complete(dest)
    return dest


def check_complete(dest: Path) -> None:
    missing = [f"metadata/{f}" for f in METADATA_FILES if not (dest / "metadata" / f).is_file()]
    products = product_files(dest)
    if missing or len(products) != PRODUCT_COUNT:
        raise RuntimeError(
            f"HERB at {dest} is incomplete: missing {missing or 'no metadata'}, "
            f"{len(products)} of {PRODUCT_COUNT} product files. Rerun `yaan fetch-herb`."
        )


def product_files(dest: Path | None = None) -> list[Path]:
    return sorted(((dest or herb_dir()) / "products").glob("*.json"))
