from pathlib import Path

from reviewer.static_analyzer import analyze

FIXTURE = Path(__file__).parent / "fixtures" / "small_app.py"


def _ctx():
    return analyze(FIXTURE.read_text(encoding="utf-8"))


def test_widgets_extracted():
    ctx = _ctx()
    classes = {w.widget_class for w in ctx.widget_tree}
    assert {"Frame", "Button", "PhotoImage", "Label", "Entry"}.issubset(classes)


def test_geometry_mix_detected():
    ctx = _ctx()
    cats = {(s.category, s.severity) for s in ctx.smells}
    assert ("layout", "high") in cats


def test_command_call_mistake_detected():
    ctx = _ctx()
    msgs = " ".join(s.message for s in ctx.smells)
    assert "command=fn()" in msgs


def test_lost_photoimage_detected():
    ctx = _ctx()
    cats = {s.category for s in ctx.smells}
    assert "memory" in cats


def test_blocking_in_handler_detected():
    ctx = _ctx()
    msgs = " ".join(s.message for s in ctx.smells)
    assert "블로킹" in msgs


def test_after_self_chain_detected():
    ctx = _ctx()
    cats_msgs = [(s.category, s.message) for s in ctx.smells]
    assert any("after()" in m for _, m in cats_msgs)


def test_mainloop_and_missing_wm_delete():
    ctx = _ctx()
    assert ctx.has_mainloop is True
    assert ctx.has_wm_delete_protocol is False
    assert any(s.category == "lifecycle" for s in ctx.smells)


def test_update_smell_detected():
    ctx = _ctx()
    assert ctx.uses_update is True
    assert any(s.category == "performance" and "update_idletasks" in s.message
               for s in ctx.smells)


def test_event_bindings_captured():
    ctx = _ctx()
    kinds = {b.kind for b in ctx.bindings}
    assert {"bind", "command", "after"}.issubset(kinds)


def test_handler_inbound_index_built():
    ctx = _ctx()
    # `bind("<Return>", self.on_submit)` → on_submit indexed
    assert "on_submit" in ctx.handler_inbound
    inbound = ctx.handler_inbound["on_submit"]
    assert any(b.kind == "bind" and b.sequence == "<Return>" for b in inbound)
    # `after(1000, self.tick)` → tick indexed
    assert "tick" in ctx.handler_inbound
    assert any(b.kind == "after" for b in ctx.handler_inbound["tick"])


def test_handler_inbound_skips_lambdas_and_call_results():
    """`command=self._go()` is the immediate-call mistake; we must not index
    `_go` from its handler_repr `self._go()`."""
    ctx = _ctx()
    assert "_go" not in ctx.handler_inbound
