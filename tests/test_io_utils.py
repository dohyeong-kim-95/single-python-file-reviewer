from reviewer.io_utils import read_source_text


def test_reads_utf8(tmp_path):
    p = tmp_path / "a.py"
    p.write_text("# 안녕\nprint(1)\n", encoding="utf-8")
    text, enc = read_source_text(p)
    assert "안녕" in text
    assert enc == "utf-8-sig"  # utf-8-sig handles BOM-less utf-8 too


def test_reads_utf8_with_bom_strips_it(tmp_path):
    p = tmp_path / "b.py"
    p.write_text("# bom\nprint(1)\n", encoding="utf-8-sig")
    text, enc = read_source_text(p)
    # BOM character should be stripped by utf-8-sig decoder
    assert text.startswith("# bom")
    assert "﻿" not in text
    assert enc == "utf-8-sig"


def test_reads_cp949_when_not_utf8(tmp_path):
    p = tmp_path / "c.py"
    # CP949-encoded Korean that is invalid as UTF-8
    p.write_bytes("# 한글 주석\nx = 1\n".encode("cp949"))
    text, enc = read_source_text(p)
    assert "한글" in text
    assert enc == "cp949"


def test_latin1_final_fallback(tmp_path):
    p = tmp_path / "d.py"
    # Bytes that aren't valid UTF-8 or CP949 (use a sequence with 0x81 unmapped in CP949)
    # 0xFF is a typical sentinel; cp949/euc-kr won't decode 0xFF stand-alone.
    p.write_bytes(b"\xffhello\n")
    text, enc = read_source_text(p)
    assert "hello" in text
    assert enc == "latin-1"
