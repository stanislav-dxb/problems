import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture()
def root(tmp_path):
    """A small hot_startups folder: tiny limits, one region with two queries, no feeds, no list sites."""
    (tmp_path / "config.yaml").write_text(yaml.safe_dump({
        "limits": {"searches_per_week": 4, "pages_per_week": 3, "startups_judged_per_week": 2, "run_minutes": 90},
        "first_run": {"searches_per_week": 4, "pages_per_week": 3, "startups_judged_per_week": 2, "run_minutes": 90},
        "discovery": {"share_of_searches": 0.5},
        "research": {"searches_per_startup": 1, "pages_per_startup": 0, "refresh_after_days": 30, "refresh_share": 0.5},
        "struggling": {"searches": 0},
    }), encoding="utf-8")
    (tmp_path / "sources.yaml").write_text(yaml.safe_dump({
        "regions": {"europe": {"share": 1.0, "feeds": [], "queries": {"en": ["startups {month_year}", "promising startups {year}"]}}},
        "struggling_queries": {}, "research_queries": {"en": ['"{name}" startup {country}']}, "country_languages": {},
    }), encoding="utf-8")
    (tmp_path / "RULEBOOK.md").write_text("# rules\nBe fair.\n", encoding="utf-8")
    return tmp_path


@pytest.fixture()
def ctx(root):
    from hotstartups.config import load_config, load_sources
    from hotstartups.store import Store
    cfg = load_config(root)
    src = load_sources(root)
    store = Store(root)
    store.init()
    return cfg, src, store
