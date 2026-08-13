from __future__ import annotations

import contextlib
import sys

try:
    import tkinter
    from tkinter import font as tkfont
    from tkinter import messagebox, ttk
except Exception:
    tkinter = tkfont = messagebox = ttk = None  # type: ignore[assignment]

APP_TITLE = "Slicer URI Bridge"
BUNDLE_HINT_MARKUP = (
    "The STL model pack will be opened in Bambu Studio.\n\n"
    "1. If asked to load the files as a single object with multiple parts, choose **No**.\n"
    "2. After the models finish loading, press **A** to auto-arrange them for your selected printer."
)
BUNDLE_HINT = BUNDLE_HINT_MARKUP.replace("**", "")
_DPI_READY = False


def show_message(message: str, kind: str) -> None:
    print(message, file=sys.stderr)
    if tkinter is None:
        return
    _show_tk_messagebox(message, kind)


def show_error(message: str) -> None:
    show_message(message, "showerror")


def show_warning(message: str) -> None:
    show_message(message, "showwarning")


def show_bundle_hint() -> None:
    print(BUNDLE_HINT, file=sys.stderr)
    if tkinter is None:
        return
    if not _show_bundle_hint_dialog():
        _show_tk_messagebox(BUNDLE_HINT, "showinfo")


def _ensure_dpi_aware() -> None:
    global _DPI_READY
    if _DPI_READY or sys.platform != "win32":
        return
    _DPI_READY = True
    import ctypes

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        with contextlib.suppress(Exception):
            ctypes.windll.user32.SetProcessDPIAware()


def center_dialog(window: tkinter.Tk) -> None:
    window.update_idletasks()
    width = window.winfo_reqwidth()
    height = window.winfo_reqheight()
    x = max((window.winfo_screenwidth() - width) // 2, 0)
    y = max((window.winfo_screenheight() - height) // 2, 0)
    window.geometry(f"{width}x{height}+{x}+{y}")


def _pack_markup(parent, markup: str) -> None:
    default = tkfont.nametofont("TkDefaultFont")
    bold = default.copy()
    bold.configure(weight="bold")
    for line in markup.split("\n"):
        row = ttk.Frame(parent)
        row.pack(anchor="w")
        if line == "":
            ttk.Label(row, text=" ").pack(anchor="w")
            continue
        for index, part in enumerate(line.split("**")):
            if part == "":
                continue
            ttk.Label(row, text=part, font=(bold if index % 2 else default)).pack(side="left")


def _show_tk_messagebox(message: str, kind: str) -> bool:
    try:
        _ensure_dpi_aware()
        root = tkinter.Tk()
        root.withdraw()
        getattr(messagebox, kind)(APP_TITLE, message, parent=root)
        root.destroy()
        return True
    except Exception:
        return False


def _show_bundle_hint_dialog() -> bool:
    root = None
    try:
        _ensure_dpi_aware()
        root = tkinter.Tk()
        root.withdraw()
        root.title(APP_TITLE)
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", root.destroy)

        body = ttk.Frame(root, padding=14)
        body.grid(sticky="nsew")
        ttk.Label(body, image="::tk::icons::information").grid(row=0, column=0, padx=(0, 14), sticky="n")
        text_frame = ttk.Frame(body)
        text_frame.grid(row=0, column=1, sticky="w")
        _pack_markup(text_frame, BUNDLE_HINT_MARKUP)

        buttons = ttk.Frame(body)
        buttons.grid(row=1, column=0, columnspan=2, sticky="e", pady=(14, 0))
        btn = ttk.Button(buttons, text="OK", command=root.destroy)
        btn.grid(row=0, column=0)
        root.bind("<Return>", lambda _event: root.destroy())
        root.bind("<Escape>", lambda _event: root.destroy())
        btn.focus_set()

        center_dialog(root)
        root.deiconify()
        root.lift()
        root.focus_force()
        root.attributes("-topmost", True)
        root.grab_set()
        root.wait_window()
        return True
    except Exception:
        return False
    finally:
        if root is not None:
            with contextlib.suppress(Exception):
                root.destroy()
