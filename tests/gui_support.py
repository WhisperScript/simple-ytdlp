"""Shared helpers for the tests that open the real window (test_gui.py, test_resume_e2e.py).

The app keeps its settings, queue and history in a fixed folder that is worked out when the module is imported,
so the module is loaded here with that folder redirected to a temporary one - a test run never touches the
data of an installed copy.
"""
import atexit
import importlib.util
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_ENV = ("HOME", "USERPROFILE", "XDG_DATA_HOME", "LOCALAPPDATA")

_cache: dict = {}


def load_app():
    """(module, data_dir): simple-ytdlp.py imported once with its data folder inside a temp dir."""
    if not _cache:
        home = Path(tempfile.mkdtemp(prefix="simple-ytdlp-test-"))
        atexit.register(shutil.rmtree, home, ignore_errors=True)
        saved = {k: os.environ.get(k) for k in _ENV}
        os.environ.update({"HOME": str(home), "USERPROFILE": str(home),
                           "XDG_DATA_HOME": str(home / "share"), "LOCALAPPDATA": str(home / "local")})
        try:
            spec = importlib.util.spec_from_file_location("simple_ytdlp_gui", ROOT / "simple-ytdlp.py")
            module = importlib.util.module_from_spec(spec)
            sys.modules["simple_ytdlp_gui"] = module
            spec.loader.exec_module(module)
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        _cache.update(module=module, data=module.data_dir(), home=home)
    return _cache["module"], _cache["data"]


def scratch_dir(prefix: str) -> Path:
    """A fresh temporary folder that is removed when the test run ends."""
    path = Path(tempfile.mkdtemp(prefix=prefix, dir=_cache["home"] if _cache else None))
    return path


def require_gui():
    """Skip the calling test module unless a window can be opened (Tk, sv-ttk, Pillow and a display)."""
    try:
        import tkinter
    except ImportError:
        raise unittest.SkipTest("tkinter is not installed")
    for name in ("sv_ttk", "PIL"):
        try:
            __import__(name)
        except ImportError:
            raise unittest.SkipTest(f"{name} is not installed")
    try:
        probe = tkinter.Tk()
    except tkinter.TclError as e:
        raise unittest.SkipTest(f"no display available ({e})")
    probe.destroy()


def start_app(settings: dict):
    """(module, root, app): the window with a clean data folder and the given settings."""
    require_gui()
    import tkinter as tk
    module, data = load_app()
    shutil.rmtree(data, ignore_errors=True)
    data.mkdir(parents=True, exist_ok=True)
    module.save_settings({"theme": "dark", **settings})
    root = tk.Tk()
    root.geometry("920x760+20+20")
    app = module.App(root)
    pump(root, 100)
    return module, root, app


def pump(root, ms: int = 50) -> None:
    """Run the real main loop for a while. Worker threads hand their results to Tk with after(), and Tk only
    accepts that from another thread while mainloop() is running - root.update() would not do."""
    root.after(ms, root.quit)
    root.mainloop()


def wait_until(root, condition, timeout: float = 20.0) -> bool:
    """Run the main loop until condition() is true; False after the timeout."""
    failure = []
    end = time.monotonic() + timeout

    def tick() -> None:
        try:
            if condition() or time.monotonic() > end:
                root.quit()
                return
        except BaseException as e:                    # an exception in a Tk callback would only be printed
            failure.append(e)
            root.quit()
            return
        root.after(10, tick)

    root.after(0, tick)
    root.mainloop()
    if failure:
        raise failure[0]
    return bool(condition())


def close_app(root) -> None:
    """Destroy the window without leaving timers behind: Tcl keeps them per thread, so they would fire (and
    fail) in the event loop of the next window."""
    try:
        for ident in root.tk.splitlist(root.tk.call("after", "info")):
            root.tk.call("after", "cancel", ident)
    finally:
        root.destroy()
