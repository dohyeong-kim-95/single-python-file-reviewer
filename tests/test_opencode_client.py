import json
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
def chunk_with_pack():
    """Pick the chunk containing 'self.left.pack(' so we have known evidence."""
    src = (Path(__file__).parent / "fixtures" / "small_app.py").read_text()
    ctx = analyze(src)
    chunks = split(src, ctx, max_chars=20_000)
    for c in chunks:
        if "self.left.pack" in c.code:
            return c
    raise AssertionError("expected fixture to contain self.left.pack(")


def _finding_obj(line, evidence, **overrides):
    base = {
        "severity": "high", "category": "test",
        "line": line, "message": "stub finding",
        "suggestion": "fix", "confidence": "high",
        "evidence": evidence,
    }
    base.update(overrides)
    return base


def test_valid_finding_kept(tmp_path, chunk_with_pack):
    line = next(i for i, ln in enumerate(chunk_with_pack.code.splitlines(), chunk_with_pack.start_line)
                if "self.left.pack" in ln)
    payload = {"findings": [_finding_obj(line, "self.left.pack(side=\"left\")")]}
    stub = _write_stub(tmp_path, json.dumps(payload))
    client = OpencodeClient(OpencodeConfig(bin_path=str(stub), retries=0))
    result = client.review_chunk(chunk_with_pack)
    assert result.error is None
    assert len(result.findings) == 1
    assert result.findings[0].evidence.startswith("self.left.pack")
    assert result.findings[0].confidence == "high"
    assert result.rejected == []


def test_out_of_range_line_rejected(tmp_path, chunk_with_pack):
    payload = {"findings": [_finding_obj(99999, "self.left.pack(side=\"left\")")]}
    stub = _write_stub(tmp_path, json.dumps(payload))
    client = OpencodeClient(OpencodeConfig(bin_path=str(stub), retries=0))
    result = client.review_chunk(chunk_with_pack)
    assert result.findings == []
    assert len(result.rejected) == 1
    assert result.rejected[0].reason == "out-of-range"


def test_evidence_mismatch_rejected(tmp_path, chunk_with_pack):
    # Valid line but evidence string that does not appear near it.
    line = chunk_with_pack.start_line + 1
    payload = {"findings": [_finding_obj(line, "this string is not in the source at all")]}
    stub = _write_stub(tmp_path, json.dumps(payload))
    client = OpencodeClient(OpencodeConfig(bin_path=str(stub), retries=0))
    result = client.review_chunk(chunk_with_pack)
    assert result.findings == []
    assert len(result.rejected) == 1
    assert result.rejected[0].reason == "evidence-missing"


def test_evidence_missing_rejected(tmp_path, chunk_with_pack):
    line = chunk_with_pack.start_line + 1
    payload = {"findings": [_finding_obj(line, "")]}
    stub = _write_stub(tmp_path, json.dumps(payload))
    client = OpencodeClient(OpencodeConfig(bin_path=str(stub), retries=0))
    result = client.review_chunk(chunk_with_pack)
    assert result.findings == []
    assert result.rejected and result.rejected[0].reason == "evidence-missing"


def test_garbage_returns_error(tmp_path, chunk_with_pack):
    stub = _write_stub(tmp_path, "this is not JSON at all")
    client = OpencodeClient(OpencodeConfig(bin_path=str(stub), retries=0))
    result = client.review_chunk(chunk_with_pack)
    assert result.findings == []
    assert result.error is not None and "JSON" in result.error
    assert result.parsed is None
    assert result.stdout != ""  # captured for artifact
