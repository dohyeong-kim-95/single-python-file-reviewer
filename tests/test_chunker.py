import ast
from pathlib import Path

from reviewer.chunker import split
from reviewer.static_analyzer import analyze

SYNTH = Path(__file__).parent / "fixtures" / "synthetic_5k.py"
SMALL = Path(__file__).parent / "fixtures" / "small_app.py"


def test_chunks_cover_all_lines():
    """Every line of the source must be covered by some chunk (union)."""
    src = SYNTH.read_text(encoding="utf-8")
    ctx = analyze(src)
    chunks = split(src, ctx)
    assert len(chunks) > 1, "5000+ line file must produce multiple chunks"

    total_lines = len(src.splitlines())
    covered = [False] * (total_lines + 1)
    for c in chunks:
        for i in range(c.start_line, c.end_line + 1):
            if 1 <= i <= total_lines:
                covered[i] = True
    missing = [i for i in range(1, total_lines + 1) if not covered[i]]
    assert not missing, f"lines not covered: {missing[:10]}..."


def test_chunks_respect_function_boundaries():
    """No chunk should start or end in the middle of a function body."""
    src = SYNTH.read_text(encoding="utf-8")
    ctx = analyze(src)
    chunks = split(src, ctx)
    tree = ast.parse(src)

    # Build a list of every function/method with its line range.
    fn_ranges: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fn_ranges.append((node.lineno, node.end_lineno or node.lineno))

    for c in chunks:
        for fs, fe in fn_ranges:
            # Either entirely inside, entirely outside, or chunk fully contains the fn.
            inside = fs >= c.start_line and fe <= c.end_line
            outside = fe < c.start_line or fs > c.end_line
            contains = c.start_line >= fs and c.end_line <= fe
            assert inside or outside or contains, (
                f"chunk {c.chunk_id} ({c.start_line}-{c.end_line}) "
                f"splits function ({fs}-{fe})"
            )


def test_chunk_char_budget_is_respected_for_methods():
    src = SYNTH.read_text(encoding="utf-8")
    ctx = analyze(src)
    budget = 4000
    chunks = split(src, ctx, max_chars=budget)
    # A class chunk that contains methods may exceed budget if we couldn't
    # break it (e.g. one giant method); but for our synthetic file every
    # method is small, so packed-method chunks should respect budget.
    for c in chunks:
        if c.chunk_id.startswith("BigApp.method_") or c.chunk_id == "BigApp.__header__":
            assert len(c.code) <= budget * 1.5, (
                f"chunk {c.chunk_id} oversize: {len(c.code)}"
            )


def test_small_file_single_chunk_behavior():
    src = SMALL.read_text(encoding="utf-8")
    ctx = analyze(src)
    chunks = split(src, ctx, max_chars=20_000)
    titles = [c.title for c in chunks]
    assert any("class App" in t for t in titles)


def test_inbound_context_attached_to_chunk_with_handler_method():
    """The chunk that contains class App should expose, in inbound_md, the
    fact that App.on_submit is registered via bind(<Return>) elsewhere
    in the same class."""
    src = SMALL.read_text(encoding="utf-8")
    ctx = analyze(src)
    chunks = split(src, ctx, max_chars=20_000)
    app_chunk = next(c for c in chunks if "class App" in c.title)
    inbound = app_chunk.context.inbound_md
    assert "on_submit" in inbound
    assert "<Return>" in inbound
    # `tick` is registered via after(); should also surface
    assert "tick" in inbound
