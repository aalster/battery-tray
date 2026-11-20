"""Settings window. Tk lives in the main thread; other threads talk to it through app.ui_queue."""
import queue
import tkinter as tk
from tkinter import ttk

from . import autostart

LINGER_CHOICES = (0, 1, 5, 10)


class SettingsWindow:
    def __init__(self, app):
        self.app = app
        self.rows = {}  # key -> (enabled var, label var, status label)
        self.tick_id = None

        self.root = root = tk.Tk()
        root.withdraw()
        root.title("Battery Tray")
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", self.hide)

        body = ttk.Frame(root, padding=14)
        body.pack(fill="both", expand=True)

        ttk.Label(body, text="Показувати в треї", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self.list = ttk.Frame(body)
        self.list.pack(fill="x", pady=(6, 4))
        self.empty = ttk.Label(self.list, text="Пристроїв поки не знайдено…", foreground="#777")
        ttk.Label(
            body, foreground="#777", wraplength=440, justify="left",
            text="Назву можна змінити. Пристрої з однаковою назвою показуються однією іконкою — "
                 "так об'єднуються різні підключення одного пристрою (Bluetooth і донгл).",
        ).pack(anchor="w", pady=(0, 12))

        linger = ttk.Frame(body)
        linger.pack(anchor="w")
        ttk.Label(linger, text="Після відключення показувати ще:").pack(side="left")
        self.linger = tk.IntVar()
        for minutes in LINGER_CHOICES:
            ttk.Radiobutton(linger, text=f"{minutes} хв", value=minutes, variable=self.linger).pack(side="left", padx=(8, 0))

        self.autostart = tk.BooleanVar()
        ttk.Checkbutton(body, text="Запускати разом із Windows", variable=self.autostart).pack(anchor="w", pady=(10, 0))

        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=(14, 0))
        ttk.Button(buttons, text="Закрити", command=self.hide).pack(side="right")
        ttk.Button(buttons, text="Зберегти", command=self.save).pack(side="right", padx=(0, 8))

        root.after(200, self._pump)

    def run(self):
        self.root.mainloop()

    def _pump(self):
        try:
            while True:
                command = self.app.ui_queue.get_nowait()
                if command == "quit":
                    self.root.destroy()
                    return
                if command == "settings":
                    self.show()
        except queue.Empty:
            pass
        self.root.after(200, self._pump)

    def show(self):
        for child in self.list.winfo_children():
            if child is not self.empty:
                child.destroy()
        self.rows.clear()
        self.linger.set(self.app.settings["linger_minutes"])
        self.autostart.set(autostart.is_enabled())
        self._sync()
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        if self.tick_id is None:
            self.tick_id = self.root.after(2000, self._tick)

    def hide(self):
        if self.tick_id is not None:
            self.root.after_cancel(self.tick_id)
            self.tick_id = None
        self.root.withdraw()

    def _tick(self):
        self._sync()
        self.tick_id = self.root.after(2000, self._tick)

    def _sync(self):
        """Adds rows for newly found devices and refreshes the status column."""
        devices = self.app.snapshot()
        if devices:
            self.empty.grid_forget()
        else:
            self.empty.grid(row=0, column=0, sticky="w")
        for d in devices:
            if d["key"] not in self.rows:
                row = len(self.rows)
                enabled = tk.BooleanVar(value=d["enabled"])
                label = tk.StringVar(value=d["label"] or d["name"])
                ttk.Checkbutton(self.list, variable=enabled).grid(row=row, column=0, pady=2)
                ttk.Entry(self.list, textvariable=label, width=26).grid(row=row, column=1, padx=(2, 10))
                status = ttk.Label(self.list)
                status.grid(row=row, column=2, sticky="w")
                self.rows[d["key"]] = (enabled, label, status, d["name"])
            parts = [d["kind"], "підключено" if d["connected"] else "відключено"]
            if d["percent"] is not None:
                parts.append(f"{d['percent']}%")
            self.rows[d["key"]][2].config(text=" · ".join(parts), foreground="#000" if d["connected"] else "#888")

    def save(self):
        devices = {}
        for key, (enabled, label, _, name) in self.rows.items():
            text = label.get().strip()
            devices[key] = {"enabled": enabled.get(), "label": "" if text == name else text}
        self.app.apply(devices, self.linger.get())
        if self.autostart.get() != autostart.is_enabled():
            autostart.toggle()
        self.hide()
