import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("SCOUT_HASH_SALT", "test-salt")


@pytest.fixture()
def conn():
    from scout import db as dbm
    c = dbm.connect(":memory:")
    yield c
    c.close()


@pytest.fixture()
def cfg():
    from scout.config import DEFAULTS, _deep_merge
    from scout import llm
    c = _deep_merge(DEFAULTS, {"llm": {"backend": "rules"}})
    llm.configure(c)  # tests never shell out unless they configure claude_code themselves
    return c
