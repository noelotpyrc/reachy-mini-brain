import json

from reachy_mini_brain import reception


def _manifest(tmp_path, run_id):
    return json.loads((tmp_path / "runs" / f"run-{run_id}.json").read_text())


def test_pydantic_manifest_records_resolved_openrouter_model(tmp_path, monkeypatch):
    monkeypatch.setattr(reception, "ARTIFACTS", tmp_path)

    reception.ReceptionDaemon(
        reception.MockSession(),
        brain=True,
        brain_backend="pydantic",
        brain_model="sonnet",
        run_id="pydantic-test",
    )

    config = _manifest(tmp_path, "pydantic-test")["config"]
    assert config["brain_backend"] == "pydantic"
    assert config["brain_model"] == "openai/gpt-oss-20b"
    assert config["brain_model_requested"] == "sonnet"


def test_claude_manifest_records_requested_model(tmp_path, monkeypatch):
    monkeypatch.setattr(reception, "ARTIFACTS", tmp_path)

    reception.ReceptionDaemon(
        reception.MockSession(),
        brain=True,
        brain_backend="claude",
        brain_model="haiku",
        run_id="claude-test",
    )

    config = _manifest(tmp_path, "claude-test")["config"]
    assert config["brain_backend"] == "claude"
    assert config["brain_model"] == "haiku"
    assert config["brain_model_requested"] == "haiku"
