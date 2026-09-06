"""Independent Tk viewer. File reads and PSD compositing run off the UI thread."""
import argparse
import hashlib
from io import BytesIO
import logging
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, ttk

from PIL import Image, ImageTk
from psd_tools import PSDImage

from .storage import ROOT, DOCS, read_json


def file_stamp(path):
    stat = Path(path).stat()
    return stat.st_mtime_ns, stat.st_size, stat.st_ino


class StablePSDReader:
    """Retry partial writes and only publish a complete, stable snapshot."""
    def __init__(self, settle_seconds=0.5):
        self.settle_seconds = settle_seconds
        self.pending = None
        self.pending_since = 0.0
        self.loaded = None

    def read(self, path, force=False, generated_hash=None):
        path = Path(path)
        stamp = (str(path.resolve()), *file_stamp(path))
        if force:
            self.loaded = None
        if stamp == self.loaded:
            return None
        if stamp != self.pending:
            self.pending, self.pending_since = stamp, time.monotonic()
            return None
        if time.monotonic() - self.pending_since < self.settle_seconds:
            return None
        data = path.read_bytes()
        if (str(path.resolve()), *file_stamp(path)) != stamp:
            return None
        psd = PSDImage.open(BytesIO(data))
        # psd-tools regenerates the merged preview when our exporter saves.
        # Trust it only when the complete file matches that exported hash.
        trusted = generated_hash is not None and hashlib.sha256(data).hexdigest() == generated_hash
        picture = (psd.topil() if trusted else psd.composite(force=True)).convert("RGBA")
        if (str(path.resolve()), *file_stamp(path)) != stamp:
            return None
        layers = []
        def visit(parent, depth=0):
            for layer in reversed(list(parent)):
                layers.append({"name": layer.name, "depth": depth, "group": layer.is_group(), "mode": layer.blend_mode.name, "opacity": layer.opacity})
                if layer.is_group():
                    visit(layer, depth + 1)
        visit(psd)
        self.loaded = stamp
        return {"image": picture, "layers": layers, "size": psd.size, "stamp": stamp}


class PreviewWindow:
    def __init__(self, root, psd=None, job=None):
        self.root = root
        self.target = (str(Path(psd).resolve()), str(Path(job).resolve()) if job else None) if psd else None
        self.stop = threading.Event()
        self.refresh = threading.Event()
        self.messages = queue.Queue(maxsize=8)
        self.images = {}
        self.photo = None
        self.image_bounds = None
        self.updates = 0
        self.mode = tk.StringVar(value="PSD")
        root.title("PsdMaker — PSD進捗プレビュー")
        root.geometry("1180x820")
        root.minsize(780, 520)
        root.configure(background="#161b23")
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TFrame", background="#161b23")
        style.configure("TLabel", background="#161b23", foreground="#e5eaf1", font=("Yu Gothic UI", 10))
        style.configure("Title.TLabel", font=("Yu Gothic UI", 17, "bold"))
        style.configure("TButton", padding=(12, 7))
        style.configure("Treeview", background="#202733", fieldbackground="#202733", foreground="#e5eaf1", rowheight=24)
        header = ttk.Frame(root, padding=18)
        header.pack(fill="x")
        ttk.Label(header, text="PSD 進捗プレビュー", style="Title.TLabel").pack(side="left")
        ttk.Button(header, text="PSDを選択", command=self.choose).pack(side="right", padx=4)
        ttk.Button(header, text="最新ジョブに追従", command=self.follow).pack(side="right", padx=4)
        ttk.Button(header, text="再読み込み", command=self.refresh.set).pack(side="right", padx=4)
        self.path_label = ttk.Label(root, text="監視先を待っています", padding=(18, 0, 18, 10), wraplength=1080)
        self.path_label.pack(fill="x")
        toolbar = ttk.Frame(root, padding=(18, 0, 18, 10))
        toolbar.pack(fill="x")
        for name in ("PSD", "参照", "差分", "線画", "背景", "配色"):
            ttk.Radiobutton(toolbar, text=name, variable=self.mode, value=name, command=self.draw).pack(side="left", padx=7)
        self.detail_label = ttk.Label(toolbar, text="PSDが保存されると自動更新します")
        self.detail_label.pack(side="right")
        self.seed_label = ttk.Label(root, text="参照画像をクリックすると、色選択に使う原寸座標とRGBを確認できます", padding=(18,0,18,8))
        self.seed_label.pack(fill="x")
        pane = ttk.Panedwindow(root, orient="horizontal")
        pane.pack(fill="both", expand=True, padx=18)
        self.canvas = tk.Canvas(pane, background="#232a35", highlightthickness=0)
        pane.add(self.canvas, weight=4)
        side = ttk.Frame(pane, padding=(12, 0, 0, 0), width=340)
        side.pack_propagate(False)
        pane.add(side, weight=1)
        ttk.Label(side, text="レイヤー構造").pack(anchor="w", pady=(0, 8))
        tree_frame = ttk.Frame(side)
        tree_frame.pack(fill="both", expand=True)
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(tree_frame, show="tree", selectmode="browse")
        self.tree.column("#0", width=560, minwidth=300, stretch=False)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vertical = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.tree.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.status_label = ttk.Label(root, text="待機中", padding=18, wraplength=1100)
        self.status_label.pack(fill="x")
        self.canvas.bind("<Configure>", lambda event: self.draw())
        self.canvas.bind('<Button-1>', self.inspect_seed)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.worker = threading.Thread(target=self.poll, daemon=True, name="psd-preview-reader")
        self.worker.start()
        self.timer = root.after(100, self.consume)

    def choose(self):
        path = filedialog.askopenfilename(title="監視するPSD", filetypes=[("Photoshop", "*.psd"), ("All", "*.*")])
        if path:
            self.target = (path, None)
            self.refresh.set()

    def follow(self):
        self.target = None
        self.refresh.set()

    def send(self, payload):
        try:
            self.messages.put(payload, timeout=0.1)
        except queue.Full:
            # UI will consume soon; keep an image update instead of losing it.
            if payload[0] == "image":
                while not self.stop.is_set():
                    try:
                        self.messages.put(payload, timeout=0.2)
                        break
                    except queue.Full:
                        pass

    def poll(self):
        reader = StablePSDReader()
        last_target = None
        last_aux = {}
        while not self.stop.is_set():
            try:
                target = self.target
                if target is None:
                    if not (DOCS / "active_job.json").exists():
                        self.send(("status", "監視先未登録。Agentがmonitorを実行するか、PSDを選択してください。"))
                        self.stop.wait(0.5)
                        continue
                    active = read_json(DOCS / "active_job.json")
                    target = (active["psd"], active.get("job"))
                psd, job = target
                if target != last_target:
                    reader = StablePSDReader()
                    last_aux = {}
                    last_target = target
                    self.send(("target", psd))
                forced = self.refresh.is_set()
                self.refresh.clear()
                state = read_json(Path(job) / "status.json") if job and (Path(job) / "status.json").exists() else {}
                state_text = f"{state.get('phase', '監視中')}  |  {state.get('message', 'PSDの上書きを監視しています')}"
                if job:
                    for filename, name in (("reference.png", "参照"), ("diff.png", "差分"),
                                           ("editing_lineart_white.png", "線画"),
                                           ("background_only.png", "背景"), ("palette_review.png", "配色")):
                        path = Path(job) / filename
                        if path.exists() and (forced or last_aux.get(name) != file_stamp(path)):
                            stamp = file_stamp(path)
                            with Image.open(path) as source:
                                picture = source.convert("RGBA")
                            last_aux[name] = stamp
                            self.send(("aux", (name, picture)))
                if not Path(psd).exists():
                    self.send(("status", state_text + "  |  PSDの初回保存を待っています"))
                else:
                    manifest_path = Path(job) / "layers.json" if job else None
                    generated_hash = read_json(manifest_path).get("psd_hash") if manifest_path and manifest_path.exists() else None
                    result = reader.read(psd, forced, generated_hash)
                    if result:
                        self.send(("image", result))
                    self.send(("status", state_text))
            except Exception as exc:
                self.send(("status", f"保存完了を待って再試行します（前の表示を保持）: {str(exc)[:200]}"))
            self.stop.wait(0.5)

    def consume(self):
        try:
            while True:
                kind, payload = self.messages.get_nowait()
                if kind == "status":
                    self.status_label.configure(text=payload)
                elif kind == "target":
                    self.path_label.configure(text=payload)
                    self.images.clear()
                    self.updates = 0
                    self.tree.delete(*self.tree.get_children())
                    self.draw()
                elif kind == "aux":
                    self.images[payload[0]] = payload[1]
                    self.draw()
                elif kind == "image":
                    self.images["PSD"] = payload["image"]
                    self.updates += 1
                    self.detail_label.configure(text=f"{payload['size'][0]} × {payload['size'][1]} px  |  更新 {self.updates} 回  |  {time.strftime('%H:%M:%S')}")
                    self.tree.delete(*self.tree.get_children())
                    parents = {0: ""}
                    for layer in payload["layers"]:
                        node = self.tree.insert(parents[layer["depth"]], "end", text=f"{layer['name']}  [{layer['mode']} · {round(layer['opacity'] / 255 * 100)}%]", open=True)
                        parents[layer["depth"] + 1] = node
                    self.draw()
        except queue.Empty:
            pass
        self.timer = self.root.after(100, self.consume)

    def draw(self):
        self.canvas.delete("all")
        self.image_bounds = None
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        source = self.images.get(self.mode.get())
        if source is None:
            self.canvas.create_text(width / 2, height / 2, text="保存された画像がここに表示されます", fill="#a9b6c8", font=("Yu Gothic UI", 14))
            return
        picture = source.copy()
        picture.thumbnail((max(1, width - 30), max(1, height - 30)), Image.Resampling.LANCZOS)
        x0, y0 = (width - picture.width) // 2, (height - picture.height) // 2
        self.image_bounds = (x0, y0, picture.width, picture.height)
        for y in range(0, picture.height, 20):
            for x in range(0, picture.width, 20):
                color = "#e0e3e8" if ((x // 20 + y // 20) % 2) else "#bfc6cf"
                self.canvas.create_rectangle(x0 + x, y0 + y, x0 + min(x + 20, picture.width), y0 + min(y + 20, picture.height), fill=color, outline=color)
        self.photo = ImageTk.PhotoImage(picture)
        self.canvas.create_image(width / 2, height / 2, image=self.photo)

    def inspect_seed(self, event):
        if self.mode.get() != '参照' or not self.image_bounds:
            return
        x0, y0, width, height = self.image_bounds
        if not (x0 <= event.x < x0+width and y0 <= event.y < y0+height):
            return
        source = self.images['参照']
        x = min(source.width-1, int((event.x-x0)*source.width/width))
        y = min(source.height-1, int((event.y-y0)*source.height/height))
        rgb = source.getpixel((x,y))[:3]
        self.seed_label.configure(text=f'色選択の種: x={x}, y={y}  RGB={rgb}  |  select-color --x {x} --y {y}')

    def close(self):
        self.stop.set()
        self.root.after_cancel(self.timer)
        self.root.destroy()


def main():
    parser = argparse.ArgumentParser(description="Live PSD progress viewer")
    parser.add_argument("--psd")
    parser.add_argument("--job")
    args = parser.parse_args()
    (ROOT / "work").mkdir(exist_ok=True)
    logging.basicConfig(filename=ROOT / "work" / "preview.log", level=logging.WARNING, encoding="utf-8")
    try:
        root = tk.Tk()
        PreviewWindow(root, args.psd, args.job)
        root.mainloop()
    except Exception:
        logging.exception("Preview startup failed")
        raise


if __name__ == "__main__":
    main()
