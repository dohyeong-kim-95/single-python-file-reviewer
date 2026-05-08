"""AST-aware chunking for large single-file Tkinter scripts.

A weak LLM cannot digest 5000 lines at once, so we cut the file into
syntactically clean pieces (class/method) and attach a small
pre-computed context summary to each chunk.

Key invariants:
    1. Chunks never split a function or class body.
    2. The union of all chunk line ranges covers every non-blank line in
       the source (a "module preamble" chunk picks up imports/top-level
       code that lives outside any def/class).
    3. Each chunk's `code` length is bounded by `max_chars`.
"""

from __future__ import annotations

import ast
from typing import Iterable, Optional

from .models import Chunk, ContextSlice, ProjectContext

DEFAULT_MAX_CHARS = 6000  # ~1500 tokens at 4 chars/token, conservative


def split(
    source: str,
    ctx: ProjectContext,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> list[Chunk]:
    lines = source.splitlines(keepends=True)
    tree = ast.parse(source)

    top_level = list(_top_level_blocks(tree))
    chunks: list[Chunk] = []

    if not top_level:
        chunks.append(_make_chunk(
            chunk_id="module",
            title="module (no top-level defs)",
            start=1,
            end=len(lines),
            lines=lines,
            ctx=ctx,
        ))
        return chunks

    # Preamble = anything before the first top-level def/class.
    first_start = top_level[0].lineno
    if first_start > 1:
        chunks.append(_make_chunk(
            chunk_id="module-preamble",
            title="module preamble (imports/top-level)",
            start=1,
            end=first_start - 1,
            lines=lines,
            ctx=ctx,
        ))

    last_end = top_level[-1].end_lineno or top_level[-1].lineno

    for i, node in enumerate(top_level):
        chunks.extend(_chunk_node(node, lines, ctx, max_chars))

    if last_end < len(lines):
        chunks.append(_make_chunk(
            chunk_id="module-tail",
            title="module tail (top-level after last def)",
            start=last_end + 1,
            end=len(lines),
            lines=lines,
            ctx=ctx,
        ))

    # Stretch every chunk's end_line up to (next chunk's start - 1) so blank
    # lines between top-level defs are absorbed.
    for i in range(len(chunks) - 1):
        gap_end = chunks[i + 1].start_line - 1
        if gap_end > chunks[i].end_line:
            chunks[i] = _make_chunk(
                chunk_id=chunks[i].chunk_id,
                title=chunks[i].title,
                start=chunks[i].start_line,
                end=gap_end,
                lines=lines,
                ctx=ctx,
            )

    return chunks


def _top_level_blocks(tree: ast.AST) -> Iterable[ast.AST]:
    for n in tree.body:
        if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            yield n


def _chunk_node(
    node: ast.AST,
    lines: list[str],
    ctx: ProjectContext,
    max_chars: int,
) -> list[Chunk]:
    start = node.lineno
    end = node.end_lineno or start
    body_chars = sum(len(lines[i]) for i in range(start - 1, end))
    base_id = getattr(node, "name", "block")

    if body_chars <= max_chars or not isinstance(node, ast.ClassDef):
        # Whole class / function fits, OR it's a top-level function we won't split.
        title = (
            f"class {node.name}" if isinstance(node, ast.ClassDef)
            else f"function {node.name}"
        )
        return [_make_chunk(
            chunk_id=base_id,
            title=title,
            start=start,
            end=end,
            lines=lines,
            ctx=ctx,
        )]

    # Class is too big -> emit (1) class header chunk + (2) one chunk per
    # method (or grouped methods that fit).
    chunks: list[Chunk] = []
    methods = [
        m for m in node.body
        if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    header_end = methods[0].lineno - 1 if methods else end
    if header_end >= start:
        chunks.append(_make_chunk(
            chunk_id=f"{base_id}.__header__",
            title=f"class {node.name} (header / class-level body)",
            start=start,
            end=header_end,
            lines=lines,
            ctx=ctx,
        ))

    # Pack consecutive small methods into a single chunk to reduce LLM calls.
    bucket: list[ast.AST] = []
    bucket_chars = 0
    for m in methods:
        m_start = m.lineno
        m_end = m.end_lineno or m_start
        m_chars = sum(len(lines[i]) for i in range(m_start - 1, m_end))
        if m_chars > max_chars:
            if bucket:
                chunks.append(_pack_methods(node.name, bucket, lines, ctx, base_id))
                bucket, bucket_chars = [], 0
            chunks.append(_make_chunk(
                chunk_id=f"{base_id}.{m.name}",
                title=f"class {node.name}.{m.name} (oversize, kept whole)",
                start=m_start,
                end=m_end,
                lines=lines,
                ctx=ctx,
            ))
            continue
        if bucket_chars + m_chars > max_chars and bucket:
            chunks.append(_pack_methods(node.name, bucket, lines, ctx, base_id))
            bucket, bucket_chars = [], 0
        bucket.append(m)
        bucket_chars += m_chars
    if bucket:
        chunks.append(_pack_methods(node.name, bucket, lines, ctx, base_id))

    # Extend each chunk's end_line to absorb blank/decorator lines between
    # methods so the union covers the whole class.
    for i, c in enumerate(chunks):
        if i + 1 < len(chunks):
            new_end = chunks[i + 1].start_line - 1
        else:
            new_end = end
        if new_end > c.end_line:
            chunks[i] = _make_chunk(
                chunk_id=c.chunk_id,
                title=c.title,
                start=c.start_line,
                end=new_end,
                lines=lines,
                ctx=ctx,
            )

    return chunks


def _pack_methods(
    class_name: str,
    methods: list[ast.AST],
    lines: list[str],
    ctx: ProjectContext,
    base_id: str,
) -> Chunk:
    start = methods[0].lineno
    end = methods[-1].end_lineno or methods[-1].lineno
    names = ",".join(m.name for m in methods)
    return _make_chunk(
        chunk_id=f"{base_id}.{methods[0].name}+{len(methods) - 1}",
        title=f"class {class_name} methods: {names}",
        start=start,
        end=end,
        lines=lines,
        ctx=ctx,
    )


def _make_chunk(
    chunk_id: str,
    title: str,
    start: int,
    end: int,
    lines: list[str],
    ctx: ProjectContext,
) -> Chunk:
    code = "".join(lines[start - 1:end])
    return Chunk(
        chunk_id=chunk_id,
        title=title,
        start_line=start,
        end_line=end,
        code=code,
        context=_slice_context(ctx, start, end),
    )


def _slice_context(ctx: ProjectContext, start: int, end: int) -> ContextSlice:
    widgets_in = [w for w in ctx.widget_tree if start <= w.line <= end]
    bindings_in = [b for b in ctx.bindings if start <= b.line <= end]
    smells_in = [s for s in ctx.smells if start <= s.line <= end]

    widget_md = _md_table(
        ["line", "var", "class", "parent"],
        [[str(w.line), w.var_name, w.widget_class, str(w.parent_var)] for w in widgets_in],
    ) if widgets_in else "_(이 청크에 위젯 생성 없음)_"

    bind_md = _md_table(
        ["line", "kind", "widget", "sequence", "handler"],
        [[
            str(b.line), b.kind, str(b.widget_var),
            str(b.sequence), b.handler_repr,
        ] for b in bindings_in],
    ) if bindings_in else "_(이 청크에 이벤트 바인딩 없음)_"

    smell_md = _md_table(
        ["line", "severity", "category", "message"],
        [[str(s.line), s.severity, s.category, s.message] for s in smells_in],
    ) if smells_in else "_(정적 스멜 없음)_"

    inbound_md = _build_inbound_md(ctx, start, end)

    notes: list[str] = []
    if not ctx.has_wm_delete_protocol and ctx.has_mainloop:
        notes.append("프로젝트 전체적으로 WM_DELETE_WINDOW 프로토콜 누락.")
    if ctx.uses_update and not ctx.uses_update_idletasks:
        notes.append("프로젝트 전체적으로 .update() 만 사용, update_idletasks() 미사용.")

    return ContextSlice(
        widget_tree_md=widget_md,
        bindings_md=bind_md,
        smells_md=smell_md,
        inbound_md=inbound_md,
        notes=notes,
    )


def _build_inbound_md(ctx: ProjectContext, start: int, end: int) -> str:
    """For each method whose body lives inside [start, end], list every
    Tkinter binding (anywhere in the file) that registers it as a handler.
    """
    rows: list[list[str]] = []
    for cls in ctx.classes:
        for m in cls.methods:
            if not (start <= m.lineno and m.end_lineno <= end):
                continue
            for b in ctx.handler_inbound.get(m.name, ()):
                rows.append([
                    f"{cls.name}.{m.name}",
                    b.kind,
                    str(b.sequence) if b.sequence else "",
                    f"line {b.line}",
                    b.handler_repr,
                ])
    for fn in ctx.top_level_funcs:
        if not (start <= fn.lineno and fn.end_lineno <= end):
            continue
        for b in ctx.handler_inbound.get(fn.name, ()):
            rows.append([
                fn.name,
                b.kind,
                str(b.sequence) if b.sequence else "",
                f"line {b.line}",
                b.handler_repr,
            ])
    if not rows:
        return "_(이 청크의 메서드는 Tkinter 핸들러로 등록된 흔적이 없음)_"
    return _md_table(
        ["method", "kind", "sequence", "registered_at", "handler_expr"], rows
    )


def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    head = "| " + " | ".join(headers) + " |"
    sep = "| " + " | ".join("---" for _ in headers) + " |"
    body = "\n".join("| " + " | ".join(_esc(c) for c in row) + " |" for row in rows)
    return f"{head}\n{sep}\n{body}"


def _esc(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")
