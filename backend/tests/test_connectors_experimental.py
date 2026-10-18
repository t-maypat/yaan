"""The experimental connectors (D090) are OFF by default: `runner.experimental` returns a
connector only when ALL its settings are set, so an unchanged .env adds nothing. No network."""

import importlib

import pytest

from app.config import Settings
from app.connectors import runner

MODULES = ("linear", "gitlab", "confluence", "gdrive", "gmail", "gmeet", "zoom", "discord")
NEW_FIELDS = [
    f
    for f in Settings.model_fields
    if f.split("_")[0] in {"linear", "gitlab", "confluence", "gmail", "zoom", "discord"}
    or f.startswith(("google_drive_", "google_meet_"))
]

# connector name -> the settings it needs (the optional ones are not listed)
REQUIRED = {
    "linear": {"linear_api_key": "k", "linear_team": "ENG"},
    "gitlab": {"gitlab_token": "t", "gitlab_project": "g/p"},
    "confluence": {
        "confluence_base_url": "https://x.atlassian.net",
        "confluence_email": "e@x.io",
        "confluence_api_token": "t",
        "confluence_space_id": "1",
    },
    "gdrive": {"google_drive_access_token": "t", "google_drive_folder_id": "f"},
    "gmail": {"gmail_access_token": "t", "gmail_label": "Label_1"},
    "gmeet": {"google_meet_access_token": "t", "google_meet_space": "spaces/x"},
    "zoom": {"zoom_access_token": "t"},
    "discord": {"discord_bot_token": "t", "discord_channel_id": "5"},
}


@pytest.fixture(autouse=True)
def no_maps(monkeypatch):
    """Constructing a connector reads connectors.yaml; give it a stub, not the real file."""
    from app.connectors import base

    stub = base.Maps(product="VizForce", default_person="eid_bd791efc")
    monkeypatch.setattr(base, "maps", lambda: stub)


def settings(**values) -> Settings:
    return Settings(_env_file=None, **values)


@pytest.mark.parametrize("module", MODULES)
def test_every_module_imports_and_names_itself(module):
    mod = importlib.import_module(f"app.connectors.{module}")
    classes = [
        c
        for c in vars(mod).values()
        if isinstance(c, type)
        and getattr(c, "name", None) == module
        and c.__module__ == mod.__name__
    ]
    assert len(classes) == 1


def test_the_new_settings_default_to_none_and_are_in_env_example():
    from app.config import REPO_ROOT

    assert len(NEW_FIELDS) == 19
    assert all(Settings.model_fields[f].default is None for f in NEW_FIELDS)
    example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    for f in NEW_FIELDS:
        assert f"\n{f.upper()}=" in example


def test_with_none_of_them_set_nothing_is_added():
    assert runner.experimental("lore_demo", settings()) == []


@pytest.mark.parametrize("name", REQUIRED)
def test_a_connector_needs_every_one_of_its_settings(name):
    needed = REQUIRED[name]
    got = runner.experimental("lore_demo", settings(**needed))
    assert [c.name for c in got] == [name]
    for missing in needed:
        partial = {k: v for k, v in needed.items() if k != missing}
        assert runner.experimental("lore_demo", settings(**partial)) == [], missing


def test_the_old_connectors_are_unchanged_and_the_new_ones_follow_them(monkeypatch):
    monkeypatch.setattr("app.config.get_settings", lambda: settings())
    assert runner.configured("lore_demo") == []  # exactly what an unchanged .env gives
    monkeypatch.setattr(
        "app.config.get_settings",
        lambda: settings(
            github_token="t", github_repo="a/b", discord_bot_token="d", discord_channel_id="5"
        ),
    )
    assert [c.name for c in runner.configured("lore_demo")] == ["github", "discord"]


def test_their_items_raise_alert_cards_like_the_old_ones():
    for source in MODULES:
        assert runner.from_connector({"artifact_id": f"msg:synthetic-{source}-x"})
    assert not runner.from_connector({"artifact_id": "msg:herb_x"})
