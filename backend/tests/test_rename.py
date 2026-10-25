"""Yaan was called Lore (D144): old setting names and old eval results still work."""

import json

from app.config import Settings
from app.eval import report


def test_an_old_lore_setting_is_read_under_its_yaan_name(monkeypatch, tmp_path):
    monkeypatch.setenv("LORE_DAILY_SPEND_CAP_USD", "7")
    monkeypatch.setenv("LORE_MCP_HOSTS", '["yaan.acme.example"]')  # a JSON list, as for YAAN_
    monkeypatch.delenv("YAAN_DAILY_SPEND_CAP_USD", raising=False)
    monkeypatch.delenv("YAAN_MCP_HOSTS", raising=False)
    s = Settings(_env_file=None)
    assert s.yaan_daily_spend_cap_usd == 7.0
    assert s.yaan_mcp_hosts == ["yaan.acme.example"]


def test_a_yaan_setting_wins_over_its_old_name(monkeypatch):
    monkeypatch.setenv("LORE_DAILY_SPEND_CAP_USD", "7")
    monkeypatch.setenv("YAAN_DAILY_SPEND_CAP_USD", "3")
    assert Settings(_env_file=None).yaan_daily_spend_cap_usd == 3.0


def test_an_old_name_in_the_env_file_counts_too(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("LORE_PERSON_SPEND_CAP_USD=0.5\n", encoding="utf-8")
    monkeypatch.delenv("YAAN_PERSON_SPEND_CAP_USD", raising=False)
    monkeypatch.delenv("LORE_PERSON_SPEND_CAP_USD", raising=False)
    monkeypatch.setitem(Settings.model_config, "env_file", env)
    assert Settings().yaan_person_spend_cap_usd == 0.5


def test_results_recorded_as_lore_load_as_yaan(tmp_path):
    for system in ("lore", "lore-search", "baseline"):
        row = {"system": system, "split": "dev", "avg": 1.0}
        (tmp_path / f"20261017_{system}_dev.json").write_text(json.dumps(row), encoding="utf-8")
    latest, _ = report.latest_results(sorted(tmp_path.glob("*.json")))
    assert set(latest) == {("yaan", "dev"), ("yaan-search", "dev"), ("baseline", "dev")}
