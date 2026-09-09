"""claude_code backend with subprocess.run mocked: envelope parsing, retry on failure, batch halving."""
import json
import subprocess

import pytest

from scout import llm
from scout import classify as cl
from scout import db as dbm
from scout.sources.base import make_item


def _envelope(result, is_error=False, subtype="success"):
    return json.dumps({"type": "result", "subtype": subtype, "is_error": is_error, "result": result,
                       "session_id": "s", "num_turns": 1, "total_cost_usd": 0.0})


class FakeRun:
    """Stands in for subprocess.run; `script` is a list of (returncode, stdout, stderr) or an Exception."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        step = self.script.pop(0) if self.script else (0, _envelope("[]"), "")
        if isinstance(step, Exception):
            raise step
        rc, out, err = step
        return subprocess.CompletedProcess(cmd, rc, stdout=out, stderr=err)


@pytest.fixture()
def cc(monkeypatch, cfg, conn):
    cfg["llm"] = {"backend": "claude_code", "model": "sonnet", "evaluate_model": "opus", "timeout_s": 5, "max_parallel": 2}
    llm.configure(cfg, conn)
    monkeypatch.setattr(llm.shutil, "which", lambda name: "/usr/bin/claude")
    yield cfg


def test_strip_fences_and_extract():
    assert llm.strip_fences("```json\n[1]\n```") == "[1]"
    assert llm.strip_fences("json\n{\"a\": 1}") == '{"a": 1}'
    assert llm.extract_json("Sure:\n```json\n{\"a\": 1}\n```") == {"a": 1}
    with pytest.raises(ValueError):
        llm.extract_json("no json")


def test_envelope_parsing_and_command_shape(cc, monkeypatch):
    fake = FakeRun([(0, _envelope("[{\"idx\": 0}]"), "")])
    monkeypatch.setattr(llm.subprocess, "run", fake)
    assert llm.complete("SYS", "USER", "sonnet", stage="classify") == '[{"idx": 0}]'
    cmd = fake.calls[0]
    assert cmd[1:3] == ["-p", "USER"] and "--system-prompt" in cmd and cmd[cmd.index("--system-prompt") + 1] == "SYS"
    assert cmd[cmd.index("--output-format") + 1] == "json" and cmd[cmd.index("--model") + 1] == "sonnet"


def test_error_envelope_nonzero_exit_and_timeout(cc, monkeypatch):
    monkeypatch.setattr(llm.subprocess, "run", FakeRun([(0, _envelope("boom", is_error=True), "")]))
    with pytest.raises(llm.LLMError):
        llm.complete("s", "u", "sonnet")
    monkeypatch.setattr(llm.subprocess, "run", FakeRun([(1, "", "crash")]))
    with pytest.raises(llm.LLMError):
        llm.complete("s", "u", "sonnet")
    monkeypatch.setattr(llm.subprocess, "run", FakeRun([(1, _envelope("Authentication error", is_error=True), "")]))
    with pytest.raises(llm.LLMUnavailable):
        llm.complete("s", "u", "sonnet")
    monkeypatch.setattr(llm.subprocess, "run", FakeRun([subprocess.TimeoutExpired("claude", 5)]))
    with pytest.raises(llm.LLMError):
        llm.complete("s", "u", "sonnet")
    with pytest.raises(llm.PromptTooLong):
        llm.complete("s" * llm.MAX_PROMPT_CHARS, "u", "sonnet")


def _seed(conn, n):
    items = [make_item("hn", f"i{k}", f"https://x/{k}", "t", f"Body number {k} about a manual process that hurts.",
                       None, 1_757_000_000, {}) for k in range(n)]
    dbm.insert_items(conn, items)
    return [dict(r) for r in dbm.unclassified_items(conn)]


def _reply(rows):
    return _envelope(json.dumps([{"idx": i, "is_problem": True, "domain": "ops", "who_has_it": "smb",
                                  "problem_summary": f"Summary {i}", "pain_score": 3, "money_mentioned": False,
                                  "workaround_described": True, "solution_requested": False,
                                  "existing_solutions_named": []} for i in range(len(rows))]))


def test_retry_once_then_mark_failed(cc, conn, monkeypatch):
    rows = _seed(conn, 4)
    cc["classify"]["batch_size"] = 4
    # first call crashes, second succeeds -> all classified
    fake = FakeRun([(1, "", "flaky"), (0, _reply(rows), "")])
    monkeypatch.setattr(llm.subprocess, "run", fake)
    st = cl.classify(cc, conn)
    assert st["problems"] == 4 and st["failed"] == 0 and st["batch_failures"] == 0 and len(fake.calls) == 2
    # both attempts fail -> batch marked failed, nothing raised
    conn.execute("DELETE FROM problems")
    fake = FakeRun([(1, "", "down"), (1, "", "down")])
    monkeypatch.setattr(llm.subprocess, "run", fake)
    st = cl.classify(cc, conn)
    assert st["failed"] == 4 and st["batch_failures"] == 1 and len(fake.calls) == 2
    assert conn.execute("SELECT COUNT(*) FROM problems WHERE classification_failed = 1").fetchone()[0] == 4
    calls = list(conn.execute("SELECT stage, ok, items FROM llm_calls"))
    assert len(calls) == 4 and {c["stage"] for c in calls} == {"classify"}


def test_malformed_json_retried_with_reminder(cc, conn, monkeypatch):
    rows = _seed(conn, 2)
    fake = FakeRun([(0, _envelope("not json at all"), ""), (0, _reply(rows), "")])
    monkeypatch.setattr(llm.subprocess, "run", fake)
    st = cl.classify(cc, conn)
    assert st["problems"] == 2 and "not valid JSON" in fake.calls[1][2]


def test_batch_halving_when_prompt_too_long(cc, conn, monkeypatch):
    rows = _seed(conn, 8)
    cc["classify"]["batch_size"] = 8
    monkeypatch.setattr(llm, "MAX_PROMPT_CHARS", len(cl.SYSTEM_PROMPT) + 300)  # fits at most 2 items per call
    seen = []

    def run(cmd, **kw):
        user = cmd[2]
        n = len(json.loads(user.split("\n\n", 1)[1]))
        seen.append(n)
        assert len(cl.SYSTEM_PROMPT) + len(user) <= llm.MAX_PROMPT_CHARS
        return subprocess.CompletedProcess(cmd, 0, stdout=_reply(range(n)), stderr="")

    monkeypatch.setattr(llm.subprocess, "run", run)
    st = cl.classify(cc, conn)
    assert st["problems"] == 8 and st["failed"] == 0
    assert len(seen) >= 4 and max(seen) <= 2 and sum(seen) == 8


def test_rules_backend_skips_evaluate(cfg, conn):
    cfg["llm"] = {"backend": "rules"}
    llm.configure(cfg, conn)
    from scout.evaluate import evaluate
    st = evaluate(cfg, conn)
    assert st["skipped"] and "rules" in st["skipped"]
    with pytest.raises(llm.LLMUnavailable):
        llm.complete("s", "u")


def test_check_backend_messages(cfg, conn, monkeypatch):
    cfg["llm"] = {"backend": "claude_code", "model": "sonnet"}
    llm.configure(cfg, conn)
    monkeypatch.setattr(llm.shutil, "which", lambda name: None)
    with pytest.raises(llm.LLMUnavailable, match="not found"):
        llm.check_backend(probe=False)
    monkeypatch.setattr(llm.shutil, "which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr(llm.subprocess, "run", FakeRun([(0, "2.1.0 (Claude Code)\n", ""),
                                                        (1, _envelope("Authentication error", is_error=True), "")]))
    with pytest.raises(llm.LLMUnavailable, match="not logged in"):
        llm.check_backend(probe=True)
    monkeypatch.setattr(llm.subprocess, "run", FakeRun([(0, "2.1.0 (Claude Code)\n", ""), (0, _envelope("OK"), "")]))
    assert "2.1.0" in llm.check_backend(probe=True)
