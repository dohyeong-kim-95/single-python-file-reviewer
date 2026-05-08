import json
import stat
from pathlib import Path

import pytest

from reviewer.cli import main


def _make_stub(tmp_path: Path, response: str) -> Path:
    stub = tmp_path / "fake_opencode"
    stub.write_text(
        "#!/bin/bash\n"
        "cat > /dev/null\n"
        "cat <<'__OUT__'\n"
        f"{response}\n"
        "__OUT__\n"
    )
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return stub


@pytest.fixture
def fake_opencode_valid(tmp_path):
    """Stub that returns a finding with evidence matching the small_app fixture."""
    payload = {"findings": [{
        "severity": "low", "category": "stub",
        "line": 16, "message": "from stub",
        "suggestion": "ok", "confidence": "high",
        "evidence": "self.left = tk.Frame(root)",
    }]}
    return _make_stub(tmp_path, json.dumps(payload))


@pytest.fixture
def fake_opencode_bogus(tmp_path):
    """Stub that returns a finding with line/evidence outside the file."""
    payload = {"findings": [{
        "severity": "high", "category": "stub",
        "line": 99999, "message": "from stub but bogus",
        "suggestion": "ignore me", "confidence": "low",
        "evidence": "this string is not in the source at all",
    }]}
    return _make_stub(tmp_path, json.dumps(payload))


def test_cli_no_llm_runs_on_small_fixture(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    src = Path(__file__).parent / "fixtures" / "small_app.py"
    out = tmp_path / "report.md"
    rc = main([str(src), "--no-llm", "--out", str(out), "--no-artifacts"])
    assert rc == 0
    md = out.read_text(encoding="utf-8")
    assert "Tkinter Code Review" in md
    assert "위젯 트리" in md
    assert "pack과 grid" in md
    assert "command=fn()" in md


def test_cli_with_valid_llm_finding_writes_artifacts(tmp_path, monkeypatch, fake_opencode_valid):
    monkeypatch.chdir(tmp_path)
    src = Path(__file__).parent / "fixtures" / "small_app.py"
    rc = main([
        str(src),
        "--opencode-bin", str(fake_opencode_valid),
        "--max-workers", "2",
        "--timeout", "10",
    ])
    assert rc == 0
    run_dirs = list((tmp_path / "reviews").iterdir())
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]
    assert run_dir.name.endswith("_small_app")
    md = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "from stub" in md
    # Static context dumped
    assert (run_dir / "static_context.json").is_file()
    # Per-chunk artifacts
    chunks_dir = run_dir / "chunks"
    assert any(p.suffix == ".txt" and p.name.endswith(".prompt.txt")
               for p in chunks_dir.iterdir())
    assert any(p.suffix == ".txt" and p.name.endswith(".stdout.txt")
               for p in chunks_dir.iterdir())


def test_cli_drops_bogus_llm_finding_and_records_it(tmp_path, monkeypatch, fake_opencode_bogus):
    monkeypatch.chdir(tmp_path)
    src = Path(__file__).parent / "fixtures" / "small_app.py"
    rc = main([
        str(src),
        "--opencode-bin", str(fake_opencode_bogus),
        "--max-workers", "1",
        "--timeout", "10",
    ])
    assert rc == 0
    run_dir = next(iter((tmp_path / "reviews").iterdir()))
    md = (run_dir / "report.md").read_text(encoding="utf-8")
    # The bogus finding must NOT be in the final report
    assert "bogus" not in md
    # But it must be persisted in dropped_findings.jsonl
    dropped = (run_dir / "dropped_findings.jsonl").read_text(encoding="utf-8")
    assert dropped.strip(), "dropped_findings.jsonl should not be empty"
    record = json.loads(dropped.splitlines()[0])
    assert record["reason"] in ("out-of-range", "evidence-missing", "schema")
    # Summary mentions rejected count
    assert "evidence/range 검증 실패" in md


def test_cli_rejects_missing_file(capsys):
    rc = main(["/nonexistent/path/to/file.py", "--no-llm", "--no-artifacts"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "file not found" in err


def test_cli_rejects_non_py_file(tmp_path, capsys):
    target = tmp_path / "notes.txt"
    target.write_text("hello")
    rc = main([str(target), "--no-llm", "--no-artifacts"])
    assert rc == 2
    assert "not a .py" in capsys.readouterr().err


def test_cli_runs_on_synthetic_5k_no_llm(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    src = Path(__file__).parent / "fixtures" / "synthetic_5k.py"
    out = tmp_path / "report.md"
    rc = main([str(src), "--no-llm", "--out", str(out), "--no-artifacts"])
    assert rc == 0
    md = out.read_text(encoding="utf-8")
    assert "Summary" in md


def test_cli_default_run_dir_under_reviews(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    src = Path(__file__).parent / "fixtures" / "small_app.py"
    rc = main([str(src), "--no-llm"])
    assert rc == 0
    runs = list((tmp_path / "reviews").iterdir())
    assert len(runs) == 1
    run_dir = runs[0]
    assert (run_dir / "report.md").is_file()
    assert (run_dir / "static_context.json").is_file()


def test_cli_loop_absorbs_unexpected_chunk_exception(tmp_path, monkeypatch):
    """If review_chunk raises (e.g. a stray UnicodeDecodeError), the CLI
    must log it as a chunk failure and still produce report.md."""
    monkeypatch.chdir(tmp_path)
    src = Path(__file__).parent / "fixtures" / "small_app.py"

    from reviewer import cli as cli_mod

    class BoomClient:
        def __init__(self, *_a, **_k): pass
        def review_chunk(self, chunk):
            raise UnicodeDecodeError("cp949", b"\xe2", 0, 1, "boom from review_chunk")

    monkeypatch.setattr(cli_mod, "OpencodeClient", BoomClient)
    rc = main([str(src), "--max-workers", "1", "--timeout", "5"])
    assert rc == 0
    run_dir = next(iter((tmp_path / "reviews").iterdir()))
    md = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "Tkinter Code Review" in md
    # Failures section lists each chunk's error
    assert "boom from review_chunk" in md or "UnicodeDecodeError" in md


def test_cli_handles_cp949_source(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "korean.py"
    src.write_bytes(
        "# 한글 주석\n"
        "import tkinter as tk\n"
        "root = tk.Tk()\n"
        "root.mainloop()\n".encode("cp949")
    )
    rc = main([str(src), "--no-llm"])
    assert rc == 0
    runs = list((tmp_path / "reviews").iterdir())
    assert len(runs) == 1
    md = (runs[0] / "report.md").read_text(encoding="utf-8")
    assert "Tkinter Code Review" in md
