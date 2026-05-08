import stat
from pathlib import Path

import pytest

from reviewer.chunker import split
from reviewer.opencode_client import OpencodeClient, OpencodeConfig, _extract_json
from reviewer.static_analyzer import analyze


def test_extract_json_handles_chatter():
    text = """Sure! Here is your review:
    ```json
    {"findings": [{"severity": "low", "category": "x", "line": 3, "message": "m", "suggestion": "s"}]}
    ```
    """
    payload = _extract_json(text)
    assert payload is not None
    assert payload["findings"][0]["category"] == "x"


def test_extract_json_balanced_with_braces_in_strings():
    text = 'noise {"k": "value with } brace"} trailing'
    payload = _extract_json(text)
    assert payload == {"k": "value with } brace"}


def test_extract_json_returns_none_when_missing():
    assert _extract_json("nothing useful here") is None


def _write_stub(tmp_path: Path, response: str) -> Path:
    """A fake `opencode` shell script that ignores stdin and prints `response`."""
    stub = tmp_path / "fake_opencode"
    # No leading whitespace: shebang must be at byte 0.
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
def small_chunk():
    src = (Path(__file__).parent / "fixtures" / "small_app.py").read_text()
    ctx = analyze(src)
    return split(src, ctx, max_chars=20_000)[0]


def test_review_chunk_parses_stub_output(tmp_path, small_chunk):
    response = (
        '{"findings": [{"severity": "high", "category": "test", '
        '"line": 1, "message": "stub said high", "suggestion": "fix it"}]}'
    )
    stub = _write_stub(tmp_path, response)
    client = OpencodeClient(OpencodeConfig(bin_path=str(stub), retries=0))
    findings, err = client.review_chunk(small_chunk)
    assert err is None
    assert len(findings) == 1
    assert findings[0].source == "llm"
    assert findings[0].category == "test"
    # Line is clamped into the chunk's range
    assert small_chunk.start_line <= findings[0].line <= small_chunk.end_line


def test_review_chunk_handles_garbage(tmp_path, small_chunk):
    stub = _write_stub(tmp_path, "this is not JSON at all")
    client = OpencodeClient(OpencodeConfig(bin_path=str(stub), retries=0))
    findings, err = client.review_chunk(small_chunk)
    assert findings == []
    assert err is not None and "JSON" in err
