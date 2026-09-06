"""Opt-in GUI integration check: python scripts/verify_preview.py."""
from pathlib import Path
import sys
import time
import tkinter as tk

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PIL import ImageGrab
from anime_layer_agent.storage import atomic_write, write_json, read_json
from anime_layer_agent.viewer import PreviewWindow


def main():
    active = read_json(ROOT / "docs/active_job.json")
    original = Path(active["psd"]).read_bytes()
    folder = ROOT / "work/preview_check"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"check_{time.time_ns()}.psd"
    root = tk.Tk()
    window = PreviewWindow(root, target, active.get("job"))
    started = time.monotonic()
    state = {"written": False, "overwritten": False, "captured": False}
    result = {"passed": False}
    def check():
        elapsed = time.monotonic() - started
        if elapsed > 1 and not state["written"]:
            atomic_write(target, original)
            state["written"] = True
        if window.updates >= 1 and not state["overwritten"]:
            atomic_write(target, original)
            state["overwritten"] = True
        if window.updates >= 2:
            root.update_idletasks()
            try:
                ImageGrab.grab(window=root.winfo_id()).save(folder / "preview.png")
                result["screenshot"] = str(folder / "preview.png")
            except OSError as exc:
                result["screenshot_error"] = str(exc)
            result.update(passed=True, updates=window.updates, tree_rows=len(window.tree.get_children()))
            write_json(folder / "result.json", result)
            window.close()
            return
        if elapsed > 25:
            result.update(error="GUI did not refresh twice", updates=window.updates)
            write_json(folder / "result.json", result)
            window.close()
            return
        root.after(200, check)
    root.after(200, check)
    root.mainloop()
    print(result)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
