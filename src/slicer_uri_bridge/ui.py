from __future__ import annotations

import contextlib
import shutil
import subprocess
import sys
from pathlib import Path

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
MACOS_DIALOG_SCRIPT_NAME = "macos-dialog.applescript"
_MACOS_ALERT_TYPES = {
    "showerror": "critical",
    "showwarning": "warning",
    "showinfo": "informational",
}


def show_message(message: str, kind: str) -> None:
    print(message, file=sys.stderr)
    _show_gui_message(message, kind)


def show_error(message: str) -> None:
    show_message(message, "showerror")


def show_warning(message: str) -> None:
    show_message(message, "showwarning")


def show_bundle_hint() -> None:
    print(BUNDLE_HINT, file=sys.stderr)
    _show_gui_message(BUNDLE_HINT, "showinfo", custom_hint=True)


def _show_gui_message(message: str, kind: str, *, custom_hint: bool = False) -> None:
    if _show_macos_dialog(message, kind):
        return
    if tkinter is not None:
        if custom_hint and _show_bundle_hint_dialog():
            return
        if _show_tk_messagebox(message, kind):
            return
    _show_linux_dialog(message, kind)


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
    root = None
    try:
        _ensure_dpi_aware()
        root = tkinter.Tk()
        root.withdraw()
        root.title(APP_TITLE)
        root.attributes("-topmost", True)
        root.lift()
        root.focus_force()
        getattr(messagebox, kind)(APP_TITLE, message, parent=root)
        return True
    except Exception:
        return False
    finally:
        if root is not None:
            with contextlib.suppress(Exception):
                root.destroy()


def _macos_dialog_script_file() -> Path:
    path = Path(__file__).resolve().parent / "resources" / MACOS_DIALOG_SCRIPT_NAME
    if not path.is_file():
        raise FileNotFoundError(f"macOS dialog script not found: {path}")
    return path


def _show_macos_dialog(message: str, kind: str) -> bool:
    if sys.platform != "darwin":
        return False
    alert_type = _MACOS_ALERT_TYPES.get(kind, "informational")
    try:
        completed = subprocess.run(
            ["/usr/bin/osascript", str(_macos_dialog_script_file()), APP_TITLE, message, alert_type],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
    except Exception:
        return False

    if completed.returncode == 0:
        return True
    stderr = (completed.stderr or "").lower()
    return "user canceled" in stderr or "user cancelled" in stderr


def _run_dialog_command(command: list[str]) -> bool:
    try:
        completed = subprocess.run(
            command,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return False
    return completed.returncode in {0, 1}


def _show_linux_dialog(message: str, kind: str) -> bool:
    if sys.platform in {"win32", "darwin"}:
        return False

    zenity_flag, kdialog_flag = {
        "showerror": ("--error", "--error"),
        "showwarning": ("--warning", "--sorry"),
        "showinfo": ("--info", "--msgbox"),
    }.get(kind, ("--info", "--msgbox"))

    zenity = shutil.which("zenity")
    if zenity and _run_dialog_command(
        [zenity, zenity_flag, "--title", APP_TITLE, "--text", message, "--no-markup"]
    ):
        return True

    kdialog = shutil.which("kdialog")
    return bool(kdialog and _run_dialog_command([kdialog, "--title", APP_TITLE, kdialog_flag, message]))


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
