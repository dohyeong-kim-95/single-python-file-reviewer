"""Generate a deterministic ~5000-line Tkinter app for chunker/e2e tests.

Run:
    python tests/fixtures/make_synthetic_5k.py [out.py]
"""

from __future__ import annotations

import sys
from pathlib import Path

HEADER = '''\
"""Auto-generated synthetic Tkinter app, ~5000 lines.

Do not edit by hand; regenerate via tests/fixtures/make_synthetic_5k.py.
"""

import time
import tkinter as tk
from tkinter import ttk


class BigApp:
    def __init__(self, root):
        self.root = root
        self.frames = []
        self._build()

    def _build(self):
        for i in range({n_frames}):
            f = tk.Frame(self.root)
            f.pack()
            self.frames.append(f)

'''

METHOD_TEMPLATE = '''\
    def method_{idx:04d}(self, event=None):
        """Auto-generated method #{idx}."""
        local_var_a = {idx} * 2
        local_var_b = local_var_a + {idx}
        if local_var_b > 0:
            self.frames[{idx} % len(self.frames)].configure(text=str(local_var_b))
        else:
            self.frames[0].configure(text="negative")
        # pad to make the method substantial
        for i in range(3):
            local_var_b += i
        return local_var_b

'''

FOOTER = '''\

def main():
    root = tk.Tk()
    BigApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
'''


def build(target_lines: int = 5200, n_frames: int = 20) -> str:
    body = HEADER.format(n_frames=n_frames)
    idx = 0
    while body.count("\n") < target_lines:
        body += METHOD_TEMPLATE.format(idx=idx)
        idx += 1
    body += FOOTER
    return body


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("synthetic_5k.py")
    out.write_text(build(), encoding="utf-8")
    print(f"wrote {out} ({out.read_text().count(chr(10))} lines)")
