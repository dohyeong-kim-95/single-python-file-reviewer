"""AST passes that pre-extract Tkinter structure and obvious anti-patterns.

The goal is to do all reasoning a weak LLM cannot do reliably:
build the widget tree, list event bindings, and flag mechanical smells.
"""

from __future__ import annotations

import ast
from typing import Optional

from .models import (
    ClassInfo,
    EventBinding,
    FuncInfo,
    ProjectContext,
    StaticSmell,
    WidgetNode,
)

TK_WIDGET_NAMES = {
    "Tk", "Toplevel", "Frame", "LabelFrame", "Label", "Button", "Entry",
    "Text", "Canvas", "Listbox", "Scrollbar", "Menu", "Menubutton", "Message",
    "Radiobutton", "Checkbutton", "Scale", "Spinbox", "PanedWindow", "OptionMenu",
    # ttk
    "Treeview", "Notebook", "Combobox", "Progressbar", "Separator", "Sizegrip",
    "Style",
    # images (treated as widget-like for memory tracking)
    "PhotoImage", "BitmapImage",
}

GEOMETRY_METHODS = {"pack", "grid", "place"}

BLOCKING_CALLS = {
    ("time", "sleep"),
    ("requests", "get"), ("requests", "post"), ("requests", "put"),
    ("requests", "delete"), ("requests", "patch"),
    ("urllib", "request"),  # heuristic
    ("subprocess", "run"), ("subprocess", "call"), ("subprocess", "check_output"),
}


def analyze(source: str) -> ProjectContext:
    tree = ast.parse(source)
    _set_parents(tree)
    ctx = ProjectContext(source=source, line_count=source.count("\n") + 1)

    classes, funcs = _collect_defs(tree)
    ctx.classes = classes
    ctx.top_level_funcs = funcs

    widgets, bindings, smells, flags = _WidgetVisitor().run(tree)
    ctx.widget_tree = widgets
    ctx.bindings = bindings
    ctx.smells.extend(smells)
    ctx.has_mainloop = flags["mainloop"]
    ctx.has_wm_delete_protocol = flags["wm_delete"]
    ctx.uses_update = flags["uses_update"]
    ctx.uses_update_idletasks = flags["uses_update_idletasks"]

    ctx.smells.extend(_geometry_mix_smells(bindings, widgets, tree))
    ctx.smells.extend(_after_self_chain_smells(tree))
    ctx.smells.extend(_lost_photoimage_smells(tree))
    ctx.smells.extend(_blocking_in_handler_smells(tree, bindings))

    if not ctx.has_wm_delete_protocol and ctx.has_mainloop:
        ctx.smells.append(StaticSmell(
            category="lifecycle",
            line=1,
            message="mainloop()를 사용하지만 protocol(\"WM_DELETE_WINDOW\", ...) 핸들러가 없음.",
            severity="low",
        ))
    if ctx.uses_update:
        for n in ast.walk(tree):
            if (isinstance(n, ast.Call) and _attr_name(n.func) == "update"
                    and not _attr_chain_endswith(n.func, "update_idletasks")):
                ctx.smells.append(StaticSmell(
                    category="performance",
                    line=getattr(n, "lineno", 1),
                    message=".update() 직접 호출은 재귀 이벤트 처리로 위험. update_idletasks() 권장.",
                    severity="medium",
                ))
                break

    return ctx


# ---------------------------------------------------------------------------
# Definition index
# ---------------------------------------------------------------------------

def _collect_defs(tree: ast.AST) -> tuple[list[ClassInfo], list[FuncInfo]]:
    classes: list[ClassInfo] = []
    funcs: list[FuncInfo] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            methods: list[FuncInfo] = []
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods.append(FuncInfo(
                        name=sub.name,
                        qualname=f"{node.name}.{sub.name}",
                        lineno=sub.lineno,
                        end_lineno=sub.end_lineno or sub.lineno,
                        decorators=tuple(_expr_repr(d) for d in sub.decorator_list),
                    ))
            classes.append(ClassInfo(
                name=node.name,
                lineno=node.lineno,
                end_lineno=node.end_lineno or node.lineno,
                bases=tuple(_expr_repr(b) for b in node.bases),
                methods=tuple(methods),
            ))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs.append(FuncInfo(
                name=node.name,
                qualname=node.name,
                lineno=node.lineno,
                end_lineno=node.end_lineno or node.lineno,
                decorators=tuple(_expr_repr(d) for d in node.decorator_list),
            ))
    return classes, funcs


# ---------------------------------------------------------------------------
# Widget tree / event bindings
# ---------------------------------------------------------------------------

class _WidgetVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.widgets: list[WidgetNode] = []
        self.bindings: list[EventBinding] = []
        self.smells: list[StaticSmell] = []
        self.flags = {
            "mainloop": False,
            "wm_delete": False,
            "uses_update": False,
            "uses_update_idletasks": False,
        }

    def run(self, tree: ast.AST):
        self.visit(tree)
        return self.widgets, self.bindings, self.smells, self.flags

    def visit_Call(self, node: ast.Call) -> None:
        name = _attr_name(node.func)
        full = _attr_chain(node.func)

        # Widget construction: catch every call to a known widget class and
        # try to recover an assigned variable name via the parent AST node.
        if name in TK_WIDGET_NAMES:
            var_name = _resolve_assigned_var(node)
            parent = _first_arg_var(node)
            self.widgets.append(WidgetNode(
                var_name=var_name or "",
                widget_class=name,
                parent_var=parent,
                line=node.lineno,
            ))

        if name == "mainloop":
            self.flags["mainloop"] = True
        elif name == "update":
            self.flags["uses_update"] = True
        elif name == "update_idletasks":
            self.flags["uses_update_idletasks"] = True

        if name == "bind" and node.args:
            seq = _const_str(node.args[0])
            handler = _expr_repr(node.args[1]) if len(node.args) > 1 else "<missing>"
            self.bindings.append(EventBinding(
                widget_var=_receiver_repr(node.func),
                sequence=seq,
                handler_repr=handler,
                line=node.lineno,
                kind="bind",
            ))
        elif name == "protocol" and node.args:
            seq = _const_str(node.args[0])
            handler = _expr_repr(node.args[1]) if len(node.args) > 1 else "<missing>"
            if seq == "WM_DELETE_WINDOW":
                self.flags["wm_delete"] = True
            self.bindings.append(EventBinding(
                widget_var=_receiver_repr(node.func),
                sequence=seq,
                handler_repr=handler,
                line=node.lineno,
                kind="protocol",
            ))
        elif name == "after" and node.args:
            handler = _expr_repr(node.args[1]) if len(node.args) > 1 else "<delay-only>"
            self.bindings.append(EventBinding(
                widget_var=_receiver_repr(node.func),
                sequence=_expr_repr(node.args[0]),
                handler_repr=handler,
                line=node.lineno,
                kind="after",
            ))
        elif name in {"trace_add", "trace_variable", "trace"} and node.args:
            handler = _expr_repr(node.args[-1])
            self.bindings.append(EventBinding(
                widget_var=_receiver_repr(node.func),
                sequence=_expr_repr(node.args[0]) if node.args else None,
                handler_repr=handler,
                line=node.lineno,
                kind="trace",
            ))

        # `command=` keyword args
        for kw in node.keywords or []:
            if kw.arg == "command" and kw.value is not None:
                # Catch the obvious `command=fn()` mistake
                if isinstance(kw.value, ast.Call):
                    self.smells.append(StaticSmell(
                        category="event",
                        line=node.lineno,
                        message="command=fn() 형태: 콜백이 아니라 즉시 호출 결과를 전달하고 있습니다.",
                        severity="high",
                    ))
                self.bindings.append(EventBinding(
                    widget_var=_receiver_repr(node.func),
                    sequence=None,
                    handler_repr=_expr_repr(kw.value),
                    line=node.lineno,
                    kind="command",
                ))

        # PhotoImage usage warning at construction site is handled separately.
        self.generic_visit(node)


# ---------------------------------------------------------------------------
# Targeted smell passes
# ---------------------------------------------------------------------------

def _geometry_mix_smells(
    bindings, widgets, tree: ast.AST
) -> list[StaticSmell]:
    """Detect pack+grid mixed under the same parent (a classic Tkinter freeze)."""
    parent_to_methods: dict[str, dict[str, int]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            method = _attr_name(node.func)
            if method in GEOMETRY_METHODS:
                receiver = _receiver_repr(node.func)
                if receiver is None:
                    continue
                # Find the widget's parent
                parent = next(
                    (w.parent_var for w in widgets if w.var_name == receiver),
                    None,
                )
                key = parent or "<unknown>"
                parent_to_methods.setdefault(key, {})
                parent_to_methods[key].setdefault(method, node.lineno)
    out: list[StaticSmell] = []
    for parent, methods in parent_to_methods.items():
        if "pack" in methods and "grid" in methods:
            line = min(methods["pack"], methods["grid"])
            out.append(StaticSmell(
                category="layout",
                line=line,
                message=(
                    f"부모 {parent!r} 자식들에서 pack과 grid가 혼용되었습니다 "
                    "(Tkinter가 무한 루프에 빠질 수 있음)."
                ),
                severity="high",
            ))
    return out


def _after_self_chain_smells(tree: ast.AST) -> list[StaticSmell]:
    """A function that calls .after(..., self.same_func) without obvious termination."""
    out: list[StaticSmell] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for sub in ast.walk(node):
            if not (isinstance(sub, ast.Call) and _attr_name(sub.func) == "after"):
                continue
            if len(sub.args) < 2:
                continue
            handler = _expr_repr(sub.args[1])
            if handler.endswith("." + node.name) or handler == node.name:
                # Look for any `if`/`return`/break inside the function body
                has_guard = any(
                    isinstance(n, (ast.If, ast.Return, ast.Raise, ast.Break))
                    for n in ast.walk(node)
                )
                if not has_guard:
                    out.append(StaticSmell(
                        category="performance",
                        line=sub.lineno,
                        message=(
                            f"{node.name}() 가 종료 조건 없이 after()로 자기 자신을 재호출합니다."
                        ),
                        severity="medium",
                    ))
                break
    return out


def _lost_photoimage_smells(tree: ast.AST) -> list[StaticSmell]:
    """PhotoImage 결과를 어디에도 묶지 않거나 지역 변수로만 두는 경우."""
    out: list[StaticSmell] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        cls = _called_class_name(node)
        if cls != "PhotoImage":
            continue
        parent = getattr(node, "parent", None)
        # We don't track parent in a single pass; conservative heuristic:
        # flag any PhotoImage call that is NOT directly assigned to self.<attr>.
        # Walk again to find assignments containing this call.
        ok = False
        for ass in ast.walk(tree):
            if isinstance(ass, ast.Assign) and ass.value is node:
                for tgt in ass.targets:
                    if isinstance(tgt, ast.Attribute) and isinstance(tgt.value, ast.Name) and tgt.value.id == "self":
                        ok = True
                        break
        if not ok:
            out.append(StaticSmell(
                category="memory",
                line=node.lineno,
                message=(
                    "PhotoImage 결과가 self.<attr> 등 영속 참조로 보관되지 않으면 "
                    "가비지 컬렉션으로 이미지가 사라질 수 있습니다."
                ),
                severity="medium",
            ))
    return out


def _blocking_in_handler_smells(tree: ast.AST, bindings) -> list[StaticSmell]:
    """time.sleep / requests.* 가 이벤트 핸들러로 등록된 함수 내부에서 호출되면 경고."""
    handler_names = set()
    for b in bindings:
        h = b.handler_repr
        # take last attribute segment, e.g. self.on_click -> on_click, fn -> fn
        if h:
            handler_names.add(h.split(".")[-1].rstrip("()"))
    out: list[StaticSmell] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name not in handler_names:
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                chain = _attr_chain(sub.func)
                head = chain.split(".")[0] if chain else ""
                tail = chain.split(".")[-1] if chain else ""
                if (head, tail) in BLOCKING_CALLS or chain == "time.sleep":
                    out.append(StaticSmell(
                        category="performance",
                        line=sub.lineno,
                        message=(
                            f"이벤트 핸들러 {node.name}() 안에서 블로킹 호출 {chain}() 사용. "
                            "UI 스레드를 멈춥니다."
                        ),
                        severity="high",
                    ))
                    break
    return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _set_parents(tree: ast.AST) -> None:
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            child._parent = node  # type: ignore[attr-defined]


def _resolve_assigned_var(call: ast.Call) -> Optional[str]:
    """If `call` is the RHS of `x = call`, return 'x'. Else None."""
    p = getattr(call, "_parent", None)
    if isinstance(p, ast.Assign) and p.value is call and p.targets:
        tgt = p.targets[0]
        if isinstance(tgt, ast.Name):
            return tgt.id
        if isinstance(tgt, ast.Attribute):
            return _expr_repr(tgt)
    return None


def _called_class_name(call: ast.Call) -> Optional[str]:
    name = _attr_name(call.func)
    return name


def _attr_name(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


def _attr_chain(node: ast.AST) -> str:
    parts: list[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    return ".".join(reversed(parts))


def _attr_chain_endswith(node: ast.AST, suffix: str) -> bool:
    return _attr_chain(node).endswith(suffix)


def _receiver_repr(func: ast.AST) -> Optional[str]:
    if isinstance(func, ast.Attribute):
        return _expr_repr(func.value)
    return None


def _first_arg_var(call: ast.Call) -> Optional[str]:
    if not call.args:
        return None
    return _expr_repr(call.args[0])


def _assign_target_repr(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return _expr_repr(node)
    return None


def _const_str(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _expr_repr(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return getattr(node, "id", type(node).__name__)
