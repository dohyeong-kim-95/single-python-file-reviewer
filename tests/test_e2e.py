import stat
from pathlib import Path

import pytest

from reviewer.cli import main


@pytest.fixture
def fake_opencode(tmp_path):
    stub = tmp_path / "fake_opencode"
    response = (
        '{"findings": [{"severity": "low", "category": "stub", '
        '"line": 1, "message": "from stub", "suggestion": ""}]}'
    )
    stub.write_text(
        "#!/bin/bash\n"
        "cat > /dev/null\n"
        "cat <<'__OUT__'\n"
        f"{response}\n"
        "__OUT__\n"
    )
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return stub


def test_cli_no_llm_runs_on_small_fixture(tmp_path, capsys):
    src = Path(__file__).parent / "fixtures" / "small_app.py"
    out = tmp_path / "report.md"
    rc = main([str(src), "--no-llm", "--out", str(out)])
    assert rc == 0
    md = out.read_text(encoding="utf-8")
    assert "Tkinter Code Review" in md
    assert "위젯 트리" in md
    # Static smells must show up
    assert "pack과 grid" in md
    assert "command=fn()" in md


def test_cli_with_fake_opencode_includes_llm_finding(tmp_path, fake_opencode):
    src = Path(__file__).parent / "fixtures" / "small_app.py"
    out = tmp_path / "report.md"
    rc = main([
        str(src),
        "--out", str(out),
        "--opencode-bin", str(fake_opencode),
        "--max-workers", "2",
        "--timeout", "10",
    ])
    assert rc == 0
    md = out.read_text(encoding="utf-8")
    assert "from stub" in md


def test_cli_rejects_missing_file(capsys):
    rc = main(["/nonexistent/path/to/file.py", "--no-llm"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "file not found" in err


def test_cli_rejects_non_py_file(tmp_path, capsys):
    target = tmp_path / "notes.txt"
    target.write_text("hello")
    rc = main([str(target), "--no-llm"])
    assert rc == 2
    assert "not a .py" in capsys.readouterr().err


def test_cli_runs_on_synthetic_5k_no_llm(tmp_path):
    src = Path(__file__).parent / "fixtures" / "synthetic_5k.py"
    out = tmp_path / "report.md"
    rc = main([str(src), "--no-llm", "--out", str(out)])
    assert rc == 0
    md = out.read_text(encoding="utf-8")
    assert "5218" in md or "5217" in md or "Summary" in md
