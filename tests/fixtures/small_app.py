"""Hand-crafted Tkinter anti-patterns used by unit tests.

Each anti-pattern is annotated with a comment so tests can assert on
line numbers when needed. Keep this file deterministic and small.
"""

import time
import tkinter as tk
from tkinter import ttk


class App:
    def __init__(self, root):
        self.root = root
        # geometry mix on the same parent (anti-pattern)
        self.left = tk.Frame(root)
        self.right = tk.Frame(root)
        self.left.pack(side="left")        # pack on root
        self.right.grid(row=0, column=1)   # grid on root  -> mix!

        # command=fn() instead of command=fn (anti-pattern)
        self.btn = tk.Button(root, text="Go", command=self._go())

        # PhotoImage stored only locally -> GC will eat it (anti-pattern)
        local_img = tk.PhotoImage(width=10, height=10)
        tk.Label(root, image=local_img).pack()

        # bind a handler that performs blocking work
        self.entry = tk.Entry(root)
        self.entry.bind("<Return>", self.on_submit)

        # protocol NOT set on root -> WM_DELETE missing

    def _go(self):
        return "clicked"

    def on_submit(self, event):
        # blocking call inside event handler (anti-pattern)
        time.sleep(2)
        self.root.update()  # raw update() (anti-pattern)

    def tick(self):
        # after() recursing into self without termination guard
        self.root.after(1000, self.tick)


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
