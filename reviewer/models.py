"""Dataclasses shared across the harness."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

Severity = Literal["info", "low", "medium", "high"]


@dataclass(frozen=True)
class WidgetNode:
    var_name: str
    widget_class: str
    parent_var: Optional[str]
    line: int


@dataclass(frozen=True)
class EventBinding:
    widget_var: Optional[str]
    sequence: Optional[str]
    handler_repr: str
    line: int
    kind: str  # "bind" | "command" | "protocol" | "after" | "trace"


@dataclass(frozen=True)
class StaticSmell:
    category: str
    line: int
    message: str
    severity: Severity = "medium"


@dataclass
class ProjectContext:
    source: str
    line_count: int
    widget_tree: list[WidgetNode] = field(default_factory=list)
    bindings: list[EventBinding] = field(default_factory=list)
    smells: list[StaticSmell] = field(default_factory=list)
    classes: list["ClassInfo"] = field(default_factory=list)
    top_level_funcs: list["FuncInfo"] = field(default_factory=list)
    has_mainloop: bool = False
    has_wm_delete_protocol: bool = False
    uses_update: bool = False  # raw .update() call
    uses_update_idletasks: bool = False


@dataclass(frozen=True)
class FuncInfo:
    name: str
    qualname: str
    lineno: int
    end_lineno: int
    decorators: tuple[str, ...] = ()


@dataclass(frozen=True)
class ClassInfo:
    name: str
    lineno: int
    end_lineno: int
    bases: tuple[str, ...]
    methods: tuple[FuncInfo, ...]


@dataclass
class ContextSlice:
    """Pre-computed summary that travels with each chunk into the prompt."""

    widget_tree_md: str
    bindings_md: str
    smells_md: str
    notes: list[str] = field(default_factory=list)


@dataclass
class Chunk:
    chunk_id: str
    title: str            # e.g. "class App.method __init__"
    start_line: int
    end_line: int
    code: str
    context: ContextSlice


@dataclass(frozen=True)
class Finding:
    severity: Severity
    category: str
    line: int
    message: str
    suggestion: str
    source: str  # "static" | "llm" | "parse-error"
    chunk_id: Optional[str] = None


@dataclass
class Report:
    file_path: str
    line_count: int
    project: ProjectContext
    findings: list[Finding]
    chunk_failures: list[str] = field(default_factory=list)
