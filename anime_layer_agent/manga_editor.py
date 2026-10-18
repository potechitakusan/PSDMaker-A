"""Tk controls for post-generation manga finishing. Recompute only on Apply."""
import argparse
import copy
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

from . import manga_adjust as adjust, manga_tone as tone, storage
from .storage import read_json

NODE_CONTROLS = [
    ('screen_lines', '網の細かさ（画像幅基準）', 40, 600, 10),
    ('white_point', '白にする境界（下げると白が増える）', .3, 1, .01),
    ('black_point', '黒にする境界（上げると黒が増える）', 0, .7, .01),
    ('light_tone', '薄いトーンの濃さ', .02, .9, .01),
    ('dark_tone', '濃いトーンの濃さ', .02, .95, .01),
    ('gradation', 'なめらかな陰影', 0, 1, .05),
    ('pen_threshold', '線を拾う境界（下げると線が増える）', .05, .95, .05),
    ('white_lines', 'ベタ内の白線', 0, 1, .05),
]
COMMON_CONTROLS = [
    ('min_region', '小さな明暗をまとめる面積', 0, 20000, 10),
    ('beta_min_area', '黒い粒を除く面積', 0, 5000, 1),
    ('beta_min_ratio', 'ベタを残す最小面積比', 0, .5, .001),
]
ZOOMS = ('全体', '12.5%', '25%', '50%', '75%', '100%')
DPI_NOTES = {
    'node': 'MangaScreen方式では、PSDに記録する解像度情報としてだけ使います。網点の計算には影響しません'
            '（網の細かさは「網の細かさ（画像幅基準）」で決まります）。',
    'posterize': '階調化方式では、網の間隔（px）＝解像度 ÷ トーン線数（LPI）の計算に使い、網点の細かさが変わります。'
                 'PSDの解像度情報にも記録します。',
}


class PosterizeControl(ttk.Frame):
    """Movable brightness boundaries and editable densities; the darkest step is beta (100%)
    and the lightest is white (0%), both fixed."""
    def __init__(self, parent, changed):
        super().__init__(parent)
        self.changed = changed
        self.stops = list(tone.DEFAULT_SETTINGS['posterize_thresholds'])
        self.densities = list(tone.DEFAULT_SETTINGS['posterize_densities'])
        self.selected = 0
        self.loading = False
        ttk.Label(self, text='境界の三角をドラッグ（左：暗い → 右：明るい）').pack(anchor='w')
        self.canvas = tk.Canvas(self, width=390, height=112, bg='#f1f3f5', highlightthickness=0)
        self.canvas.pack(fill='x', pady=5)
        self.canvas.bind('<Configure>', lambda e: self.draw())
        self.canvas.bind('<Button-1>', self.pick)
        self.canvas.bind('<B1-Motion>', self.drag)
        row = ttk.Frame(self); row.pack(fill='x')
        ttk.Label(row, text='選択した境界').pack(side='left')
        self.threshold = tk.StringVar(value='85')
        ttk.Spinbox(row, from_=1, to=254, increment=1, width=7, textvariable=self.threshold).pack(side='left', padx=8)
        ttk.Label(row, text='明るさ 0〜255').pack(side='left')
        self.threshold.trace_add('write', self.threshold_changed)
        row = ttk.Frame(self); row.pack(fill='x', pady=8)
        ttk.Button(row, text='境界を追加', command=self.add).pack(side='left')
        ttk.Button(row, text='選択境界を削除', command=self.remove).pack(side='left', padx=6)
        self.density_frame = ttk.Frame(self); self.density_frame.pack(fill='x')
        self.density_vars = []
        self.rebuild_densities()

    def load(self, stops, densities):
        if densities[0] != 1 or densities[-1] != 0:
            # Saved before beta/white became fixed ends: start from the tool defaults.
            stops, densities = tone.DEFAULT_SETTINGS['posterize_thresholds'], tone.DEFAULT_SETTINGS['posterize_densities']
        self.stops, self.densities = list(stops), list(densities)
        self.selected = min(self.selected, len(stops) - 1)
        self.sync_selection(); self.rebuild_densities(); self.draw()

    def sync_selection(self):
        self.loading = True
        self.threshold.set(f'{self.stops[self.selected]:g}')
        self.loading = False

    def draw(self):
        c = self.canvas; c.delete('all')
        width = max(300, c.winfo_width()) - 30
        for i in range(256):
            color = f'#{i:02x}{i:02x}{i:02x}'
            c.create_rectangle(15 + i * width / 256, 20, 15 + (i + 1) * width / 256, 42, fill=color, outline=color)
        bounds = [0, *self.stops, 255]
        for i, density in enumerate(self.densities):
            gray = round(255 * (1 - density)); color = f'#{gray:02x}{gray:02x}{gray:02x}'
            x0, x1 = 15 + bounds[i] * width / 255, 15 + bounds[i + 1] * width / 255
            c.create_rectangle(x0, 49, x1, 70, fill=color, outline='#999999')
            c.create_text((x0 + x1) / 2, 60, text=f'{density * 100:g}%', fill='white' if density > .5 else 'black')
        for i, stop in enumerate(self.stops):
            x = 15 + stop * width / 255
            c.create_polygon(x, 73, x - 7, 87, x + 7, 87,
                             fill='#be6500' if i == self.selected else '#555555')
            c.create_text(x, 99, text=f'{stop:g}', fill='#222222')

    def pick(self, event):
        value = (event.x - 15) * 255 / (max(300, self.canvas.winfo_width()) - 30)
        self.selected = min(range(len(self.stops)), key=lambda i: abs(self.stops[i] - value))
        self.sync_selection(); self.draw()

    def drag(self, event):
        value = round((event.x - 15) * 255 / (max(300, self.canvas.winfo_width()) - 30))
        low = 1 if self.selected == 0 else self.stops[self.selected - 1] + 1
        high = 254 if self.selected == len(self.stops) - 1 else self.stops[self.selected + 1] - 1
        self.threshold.set(str(max(low, min(high, value))))

    def threshold_changed(self, *_):
        if self.loading: return
        self.changed()
        try:
            value = float(self.threshold.get())
        except ValueError:
            return
        low = 0 if self.selected == 0 else self.stops[self.selected - 1]
        high = 255 if self.selected == len(self.stops) - 1 else self.stops[self.selected + 1]
        if low < value < high:
            self.stops[self.selected] = value
            self.draw()

    def add(self):
        if len(self.stops) >= 7: return
        bounds = [0, *self.stops, 255]
        i = max(range(len(bounds) - 1), key=lambda n: bounds[n + 1] - bounds[n])
        if bounds[i + 1] - bounds[i] <= 2: return
        self.stops.insert(i, round((bounds[i] + bounds[i + 1]) / 2))
        self.densities.insert(i, self.densities[i])
        # Splitting the beta or white step adds a tone step beside it, never a second fixed end.
        if i == 0:
            self.densities[1] = round((self.densities[0] + self.densities[2]) / 2, 2)
        elif i + 1 == len(self.densities) - 1:
            self.densities[i] = round((self.densities[i - 1] + self.densities[i + 1]) / 2, 2)
        self.selected = i
        self.sync_selection(); self.rebuild_densities(); self.draw(); self.changed()

    def remove(self):
        if len(self.stops) <= 1: return
        # Merge the two steps around the boundary; a fixed end (beta/white) survives the merge.
        self.densities.pop(self.selected if self.selected + 1 == len(self.densities) - 1 else self.selected + 1)
        self.stops.pop(self.selected)
        self.selected = min(self.selected, len(self.stops) - 1)
        self.sync_selection(); self.rebuild_densities(); self.draw(); self.changed()

    def rebuild_densities(self):
        for child in self.density_frame.winfo_children(): child.destroy()
        self.density_vars = {}
        last = len(self.densities) - 1
        for i, value in enumerate(self.densities):
            row = ttk.Frame(self.density_frame); row.pack(fill='x', pady=2)
            if i in (0, last):
                ttk.Label(row, text='ベタ' if i == 0 else 'ホワイト', width=14).pack(side='left')
                ttk.Label(row, text='100%（固定）' if i == 0 else '0%（固定）').pack(side='left')
                continue
            ttk.Label(row, text='暗い側の濃度' if i == 1 else f'中間 {i - 1}', width=14).pack(side='left')
            var = tk.StringVar(value=f'{value * 100:g}')
            self.density_vars[i] = var
            ttk.Scale(row, from_=0, to=100, variable=var,
                      command=lambda v, n=i: self.set_density(n, v)).pack(side='left', fill='x', expand=True)
            ttk.Spinbox(row, from_=0, to=100, increment=1, width=6, textvariable=var).pack(side='left', padx=5)
            var.trace_add('write', lambda *_, n=i: self.density_changed(n))
            ttk.Label(row, text='%').pack(side='left')

    def set_density(self, index, value):
        self.density_vars[index].set(str(round(float(value))))

    def density_changed(self, index):
        self.changed()
        try: value = float(self.density_vars[index].get()) / 100
        except ValueError: return
        if 0 <= value <= 1:
            self.densities[index] = value; self.draw()

    def values(self):
        # Validate entry contents as well as drag state, so invalid typed text is never ignored.
        stops = list(self.stops); stops[self.selected] = float(self.threshold.get())
        last = len(self.densities) - 1
        return stops, [1.0, *(float(self.density_vars[i].get()) / 100 for i in range(1, last)), 0.0]


class MangaEditor:
    def __init__(self, root, job=None):
        self.root, self.job = root, None
        self.messages = queue.Queue()
        self.busy = False; self.dirty = True; self.loading = False; self.closed = False
        self.revision = 0; self.applied = None; self.picture = None; self.photo = None
        self.vars = {}; self.updates = 0
        self.transfer_active = False
        root.title('PsdMaker — 漫画トーン調整')
        root.geometry('1380x920'); root.minsize(1060, 700)
        style = ttk.Style(root); style.theme_use('clam')
        style.configure('TLabel', font=('Yu Gothic UI', 10))
        style.configure('TButton', padding=(9, 5))
        top = ttk.Frame(root, padding=10); top.pack(fill='x')
        ttk.Label(top, text='漫画トーン調整', font=('Yu Gothic UI', 17, 'bold')).pack(side='left')
        self.job_button = ttk.Button(top, text='ジョブを開く', command=self.choose_job)
        self.job_button.pack(side='right')
        self.top_buttons = [self.job_button]
        from .tone_transfer_editor import create_dialog, TransferControls
        new_transfer = ttk.Button(top, text='別線画へトーン', command=lambda: create_dialog(self))
        new_transfer.pack(side='right', padx=8); self.top_buttons.append(new_transfer)
        for text, command in (('プリセットを読み込む', self.load_preset), ('プリセットとして保存', self.save_preset),
                              ('ツール初期値にリセット', self.reset_defaults)):
            button = ttk.Button(top, text=text, command=command)
            button.pack(side='right', padx=(0, 8)); self.top_buttons.append(button)
        self.path_label = ttk.Label(root, text='第1機能（トーン版）のjobを開くか、「別線画へトーン」で第2機能を開始します', padding=(12, 0, 12, 10))
        self.path_label.pack(fill='x')
        body = ttk.Panedwindow(root, orient='horizontal'); body.pack(fill='both', expand=True, padx=10)
        controls = ttk.Frame(body, width=470); body.add(controls, weight=0)
        self.scroll = tk.Canvas(controls, width=455, highlightthickness=0)
        sb = ttk.Scrollbar(controls, orient='vertical', command=self.scroll.yview)
        self.scroll.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y'); self.scroll.pack(side='left', fill='both', expand=True)
        self.form = ttk.Frame(self.scroll, padding=(6, 0, 16, 10))
        form_id = self.scroll.create_window((0, 0), window=self.form, anchor='nw')
        self.form.bind('<Configure>', lambda e: self.scroll.configure(scrollregion=self.scroll.bbox('all')))
        self.scroll.bind('<Configure>', lambda e: self.scroll.itemconfigure(form_id, width=e.width))
        self.transfer_holder = ttk.Frame(self.form); self.transfer_holder.pack(fill='x')
        self.transfer_controls = TransferControls(self.transfer_holder, self)
        mode_row = self.mode_row = ttk.Frame(self.form); mode_row.pack(fill='x', pady=6)
        self.mode = tk.StringVar(value='node')
        for label, value in (('MangaScreen方式', 'node'), ('階調化方式', 'posterize')):
            ttk.Radiobutton(mode_row, text=label, variable=self.mode, value=value, command=self.mode_changed).pack(side='left', padx=3)
        self.dpi_row = self.numeric(self.form, 'dpi', '原稿の解像度（dpi）', 72, 2400, 1)
        self.dpi_note = ttk.Label(self.form, text='', wraplength=400, foreground='#555555')
        self.dpi_note.pack(anchor='w', pady=(0, 4))
        self.mode_holder = ttk.Frame(self.form); self.mode_holder.pack(fill='x')
        self.node_frame = ttk.LabelFrame(self.mode_holder, text='線・白黒・トーン', padding=8)
        row = ttk.Frame(self.node_frame); row.pack(fill='x', pady=4)
        ttk.Label(row, text='出力形式').pack(side='left')
        self.format = tk.StringVar(value='dots')
        picker = ttk.Combobox(row, textvariable=self.format, values=('dots', 'gray'), state='readonly', width=10)
        picker.pack(side='left', padx=10); picker.bind('<<ComboboxSelected>>', lambda e: self.mark_dirty())
        ttk.Label(row, text='dots：網点 / gray：グレー').pack(side='left')
        for args in NODE_CONTROLS: self.numeric(self.node_frame, *args)
        ttk.Label(self.node_frame, text='確認済みのパーツ別の白・黒・濃度指定も使用します。', wraplength=400).pack(anchor='w', pady=6)
        self.posterize_frame = ttk.LabelFrame(self.mode_holder, text='線数と階調化', padding=8)
        self.numeric(self.posterize_frame, 'lpi', 'トーン線数（LPI）', 5, 200, .1)
        self.posterizer = PosterizeControl(self.posterize_frame, self.mark_dirty); self.posterizer.pack(fill='x')
        ttk.Label(self.posterize_frame, text='全パーツにこの階調を適用。出力は網点（dots）、角度は45°です。\n'
                  '暗い側ほど濃度を高く設定してください。', wraplength=400).pack(anchor='w', pady=8)
        self.transfer_grid = ttk.LabelFrame(self.form, text='網の設定（素材の濃度は上で指定）', padding=8)
        grid_modes = ttk.Frame(self.transfer_grid); grid_modes.pack(fill='x')
        for label, value in (('画像幅基準', 'node'), ('dpi・LPI基準', 'posterize')):
            ttk.Radiobutton(grid_modes, text=label, variable=self.mode, value=value, command=self.mode_changed).pack(side='left')
        for args in (('screen_lines', '画像幅あたりの網の数', 40, 600, 1), ('lpi', 'トーン線数（LPI）', 5, 200, .1)):
            self.numeric(self.transfer_grid, *args)
        ttk.Label(self.transfer_grid, text='画像幅基準では網の数、dpi・LPI基準では線数が有効です。\n線画と素材濃度は固定し、階調の再推定はしません。', wraplength=395).pack(anchor='w', pady=5)
        grid_format = ttk.Combobox(self.transfer_grid, textvariable=self.format, values=('dots', 'gray'), state='readonly', width=10)
        grid_format.pack(anchor='w'); grid_format.bind('<<ComboboxSelected>>', lambda e: self.mark_dirty())
        ttk.Label(self.transfer_grid, text='dots：網点 / gray：グレー（画像幅基準のみ）').pack(anchor='w')
        common = self.common_frame = ttk.LabelFrame(self.form, text='共通：細部・ノイズの整理', padding=8); common.pack(fill='x', pady=8)
        for args in COMMON_CONTROLS: self.numeric(common, *args)
        ttk.Label(common, text='面積は幅1024px換算。面積比0.005＝0.5%。\n線を拾う境界と白線の値は両モードで共有します。', wraplength=400).pack(anchor='w', pady=5)
        preview = ttk.Frame(body); body.add(preview, weight=1)
        toolbar = ttk.Frame(preview); toolbar.pack(fill='x', pady=5)
        ttk.Label(toolbar, text='プレビュー').pack(side='left')
        self.zoom = tk.StringVar(value='全体')
        zoom = ttk.Combobox(toolbar, textvariable=self.zoom, values=ZOOMS, state='readonly', width=9)
        zoom.pack(side='left', padx=10); zoom.bind('<<ComboboxSelected>>', lambda e: self.draw())
        ttk.Label(toolbar, text='網点の確認は100%表示で').pack(side='left')
        area = ttk.Frame(preview); area.pack(fill='both', expand=True)
        self.canvas = tk.Canvas(area, background='#30343b', highlightthickness=0)
        sx = ttk.Scrollbar(area, orient='horizontal', command=self.canvas.xview)
        sy = ttk.Scrollbar(area, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=sx.set, yscrollcommand=sy.set)
        self.canvas.grid(row=0, column=0, sticky='nsew'); sy.grid(row=0, column=1, sticky='ns'); sx.grid(row=1, column=0, sticky='ew')
        area.rowconfigure(0, weight=1); area.columnconfigure(0, weight=1)
        self.canvas.bind('<Configure>', lambda e: self.draw())
        self.canvas.bind('<ButtonPress-1>', self.canvas_click)
        self.canvas.bind('<B1-Motion>', self.canvas_drag)
        self.canvas.bind('<ButtonPress-3>', lambda e: self.canvas.scan_mark(e.x, e.y))
        self.canvas.bind('<B3-Motion>', lambda e: self.canvas.scan_dragto(e.x, e.y, gain=1))
        self.info = ttk.Label(preview, text='', padding=8); self.info.pack(fill='x')
        bottom = ttk.Frame(root, padding=10); bottom.pack(fill='x')
        self.status = tk.StringVar(value='ジョブを選択してください')
        ttk.Label(bottom, textvariable=self.status, wraplength=800).pack(side='left', fill='x', expand=True)
        self.apply_button = ttk.Button(bottom, text='反映', command=self.apply); self.apply_button.pack(side='left', padx=8)
        self.export_button = ttk.Button(bottom, text='PSD出力', command=self.export, state='disabled'); self.export_button.pack(side='left')
        self.png_button = ttk.Button(bottom, text='PNG出力', command=lambda: self.export(kind='png'), state='disabled'); self.png_button.pack(side='left', padx=8)
        self.mode_changed(); self.set_values(dict(tone.DEFAULT_SETTINGS, dpi=600))
        root.protocol('WM_DELETE_WINDOW', self.close)
        self.timer = root.after(80, self.consume)
        if job: self.open_job(job)

    def numeric(self, parent, key, label, low, high, step):
        row = ttk.Frame(parent); row.pack(fill='x', pady=3)
        ttk.Label(row, text=label).pack(anchor='w')
        line = ttk.Frame(row); line.pack(fill='x')
        var = self.vars.get(key)
        if var is None:
            var = tk.StringVar(); self.vars[key] = var
            var.trace_add('write', lambda *_: self.mark_dirty())
        ttk.Scale(line, from_=low, to=high, variable=var,
                  command=lambda value: var.set(f'{round(float(value) / step) * step:.4g}')).pack(side='left', fill='x', expand=True)
        ttk.Spinbox(line, from_=low, to=high, increment=step, width=9, textvariable=var).pack(side='left', padx=(8, 0))
        return row

    def mark_dirty(self):
        if self.loading: return
        self.revision += 1; self.dirty = True
        self.export_button.configure(state='disabled')
        self.png_button.configure(state='disabled')
        self.status.set('未反映の変更があります。「反映」でプレビューを更新します。')

    def mode_changed(self):
        self.node_frame.pack_forget(); self.posterize_frame.pack_forget()
        (self.node_frame if self.mode.get() == 'node' else self.posterize_frame).pack(fill='x')
        self.dpi_note.configure(text=(('画像幅基準ではdpiは出力解像度の記録に使用します。' if self.mode.get() == 'node'
            else 'dpi÷LPIで網間隔を決めます。素材濃度は上の指定値を使用します。') if self.transfer_active else DPI_NOTES[self.mode.get()]))
        self.mark_dirty()

    def configure_job_controls(self):
        self.transfer_holder.pack_forget()
        self.transfer_controls.pack_forget(); self.transfer_grid.pack_forget()
        self.mode_row.pack_forget(); self.mode_holder.pack_forget(); self.common_frame.pack_forget()
        if self.transfer_active:
            self.transfer_holder.pack(fill='x', before=self.dpi_row)
            self.transfer_controls.pack(fill='x', pady=6)
            self.transfer_grid.pack(fill='x', pady=6)
        else:
            self.mode_row.pack(fill='x', before=self.dpi_row)
            self.mode_holder.pack(fill='x'); self.common_frame.pack(fill='x', pady=8)

    def set_values(self, values):
        self.loading = True
        values = tone.validate_settings(values)
        for key, var in self.vars.items(): var.set(f'{values[key] if values[key] is not None else 600:g}')
        self.mode.set(values['mode']); self.format.set(values['tone_format'])
        self.posterizer.load(values['posterize_thresholds'], values['posterize_densities'])
        self.mode_changed(); self.loading = False; self.mark_dirty()

    def values(self):
        values = {key: float(var.get()) for key, var in self.vars.items()}
        for key in ('screen_lines', 'min_region', 'beta_min_area'): values[key] = round(values[key])
        stops, densities = self.posterizer.values()
        mode = self.mode.get()
        # The format choice belongs to MangaScreen mode; posterize always outputs dots.
        values.update(mode=mode, tone_format=self.format.get() if mode == 'node' else 'dots',
                      posterize_thresholds=stops, posterize_densities=densities)
        return tone.validate_settings(values)

    def choose_job(self):
        folder = filedialog.askdirectory(title='漫画jobフォルダを選択', initialdir=str(storage.ROOT / 'work'))
        if folder: self.open_job(folder)

    def open_job(self, job):
        try:
            job = Path(job).resolve()
            if not (job / 'manga_arrays.npz').exists(): raise ValueError('位置合わせ済みの漫画jobを選択してください。')
            notice = ''
            try:
                values = adjust.settings_for_job(job)
            except ValueError as exc:
                # A save from an older version should not lock the job out of the editor.
                values, notice = dict(tone.DEFAULT_SETTINGS, dpi=600), f'保存済みの設定を使えないため初期値で開きました（{exc}）'
            self.job = job; self.applied = None; self.picture = None
            self.transfer_active = (job / 'tone_transfer_job.json').exists()
            self.path_label.configure(text=('第2機能（トーン版）' if self.transfer_active else '第1機能（トーン版）') + ' — ' + str(job))
            self.set_values(values); self.configure_job_controls()
            self.scroll.yview_moveto(0)
            if self.transfer_active:
                self.transfer_controls.load(job)
                self.transfer_controls.display()
                if self.transfer_controls.reviewed.get(): self.apply()
                else: self.status.set('素材・役割を選び、ピンクの未割当領域をクリックしてください。')
            else:
                self.draw(); self.apply()
            if notice: messagebox.showwarning('設定の読み込み', notice, parent=self.root)
        except Exception as exc:
            messagebox.showerror('ジョブを開けません', str(exc), parent=self.root)

    def reset_defaults(self):
        if not messagebox.askyesno('ツール初期値にリセット', '両方式の数値をツールの初期値に戻します。表示中の方式はそのままです。',
                                   parent=self.root): return
        self.set_values(dict(tone.DEFAULT_SETTINGS, dpi=600, mode=self.mode.get()))
        self.status.set('ツール初期値に戻しました。「反映」でプレビューを更新します。')

    def save_preset(self):
        try: values = self.values()
        except Exception as exc:
            messagebox.showerror('数値を確認してください', str(exc), parent=self.root); return
        folder = adjust.presets_dir(); folder.mkdir(parents=True, exist_ok=True)
        path = filedialog.asksaveasfilename(title='プリセットとして保存', defaultextension='.json', initialdir=str(folder),
                                            filetypes=[('漫画トーン調整プリセット', '*.json')], parent=self.root)
        if not path: return
        try: data = adjust.save_preset(path, values)
        except Exception as exc:
            messagebox.showerror('プリセットの保存', str(exc), parent=self.root); return
        self.status.set(f"プリセット「{data['name']}」を保存しました（{data['saved_at']}）。")

    def load_preset(self):
        folder = adjust.presets_dir(); folder.mkdir(parents=True, exist_ok=True)
        path = filedialog.askopenfilename(title='プリセットを読み込む', initialdir=str(folder),
                                          filetypes=[('漫画トーン調整プリセット', '*.json')], parent=self.root)
        if not path: return
        try:
            data = adjust.load_preset(path)
            self.set_values(data['tone_settings'])
        except Exception as exc:
            messagebox.showerror('プリセットの読み込み', str(exc), parent=self.root); return
        self.status.set(f"プリセット「{data['name']}」（保存 {data['saved_at']}）を読み込みました。「反映」でプレビューを更新します。")

    def start_work(self, kind, fn):
        self.busy = True
        for widget in (self.apply_button, self.export_button, self.png_button, *self.top_buttons): widget.configure(state='disabled')
        self.status.set({'preview': '原寸で計算中…', 'prepare_transfer': '新線画と参照トーンを準備中…',
                         'png': '原寸PNGを保存中…'}.get(kind, 'PSDと確認画像を保存中…'))
        revision = self.revision
        def work():
            try: self.messages.put((kind, revision, fn(), None))
            except Exception as exc: self.messages.put((kind, revision, None, exc))
        threading.Thread(target=work, daemon=True, name='manga-adjust-worker').start()

    def apply(self):
        if self.busy or self.job is None: return
        try:
            values = self.values()
            draft = self.transfer_controls.draft() if self.transfer_active else None
        except Exception as exc:
            messagebox.showerror('数値を確認してください', str(exc), parent=self.root); return
        job = self.job
        self.start_work('preview', lambda: adjust.preview_manga(job, values, transfer_plan=draft))

    def export(self, output=None, kind='psd'):
        if self.busy or self.dirty or self.applied is None: return
        if output is None:
            old = Path(read_json(self.job / 'manga_job.json')['output'])
            output = filedialog.asksaveasfilename(title=f'{kind.upper()}の保存先', defaultextension=f'.{kind}',
                initialdir=str(old.parent), initialfile=old.stem + f'_adjusted.{kind}', filetypes=[(kind.upper(), f'*.{kind}')])
        if not output: return
        job, applied = self.job, copy.deepcopy({k: v for k, v in self.applied.items() if k != 'image'})
        function = adjust.export_manga_png if kind == 'png' else adjust.export_manga
        self.start_work('png' if kind == 'png' else 'export', lambda: function(job, output=output, settings=applied['settings'],
            expected_context=applied['context'], transfer_plan=applied['plan'] if self.transfer_active else None))

    def consume(self):
        if self.closed: return
        try:
            kind, revision, result, error = self.messages.get_nowait()
        except queue.Empty:
            self.timer = self.root.after(80, self.consume); return
        self.busy = False
        for widget in (self.apply_button, *self.top_buttons): widget.configure(state='normal')
        if error is not None:
            self.status.set(f'処理できませんでした：{error}')
            messagebox.showerror('漫画トーン調整', str(error), parent=self.root)
        elif kind == 'prepare_transfer':
            self.open_job(result['job'])
        elif kind == 'preview':
            self.applied = result; self.picture = result['image']; self.updates += 1
            self.dirty = revision != self.revision
            self.status.set('反映済み。設定を保存しました。' if not self.dirty else '計算中に変更された値があります。もう一度反映してください。')
            self.info.configure(text=f"{self.picture.width}×{self.picture.height}px / 網間隔 {result['pitch_px']:.2f}px / {result['pixel_layers']}描画レイヤー　{result['warning']}")
            if self.transfer_active: self.transfer_controls.assignment_view.set(False)
            self.draw()
        else:
            self.applied['context'] = adjust.context(self.job)
            self.dirty = revision != self.revision
            self.status.set(f"PNG出力済み：{result['png']}" if kind == 'png' else f"PSD出力済み：{result['psd']}（事後確認画像も生成済み）")
        state = 'normal' if self.applied is not None and not self.dirty and not self.busy else 'disabled'
        self.export_button.configure(state=state); self.png_button.configure(state=state)
        self.timer = self.root.after(80, self.consume)

    def draw(self):
        self.canvas.delete('all')
        if self.picture is None: return
        image = self.picture
        if self.zoom.get() == '全体':
            scale = min(max(1, self.canvas.winfo_width() - 16) / image.width,
                        max(1, self.canvas.winfo_height() - 16) / image.height, 1)
        else:
            scale = float(self.zoom.get().rstrip('%')) / 100
        if scale != 1:
            image = image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))), Image.Resampling.LANCZOS)
        self.display_scale = (image.width / self.picture.width, image.height / self.picture.height)
        self.photo = ImageTk.PhotoImage(image)
        self.canvas.create_image(8, 8, anchor='nw', image=self.photo)
        self.canvas.configure(scrollregion=(0, 0, image.width + 16, image.height + 16))

    def canvas_click(self, event):
        if self.transfer_active and self.transfer_controls.assignment_view.get() and self.picture is not None:
            import math
            x = math.floor((self.canvas.canvasx(event.x) - 8) / self.display_scale[0])
            y = math.floor((self.canvas.canvasy(event.y) - 8) / self.display_scale[1])
            self.transfer_controls.click(x, y)
        else:
            self.canvas.scan_mark(event.x, event.y)

    def canvas_drag(self, event):
        if not (self.transfer_active and self.transfer_controls.assignment_view.get()):
            self.canvas.scan_dragto(event.x, event.y, gain=1)

    def close(self):
        if self.busy:
            self.status.set('保存・計算が終わってから閉じてください。'); return
        self.closed = True; self.root.after_cancel(self.timer); self.root.destroy()


def main():
    parser = argparse.ArgumentParser(description='Interactive post-generation manga adjustment')
    parser.add_argument('--job')
    args = parser.parse_args()
    job = args.job
    if not job and (storage.DOCS / 'active_job.json').exists():
        active = read_json(storage.DOCS / 'active_job.json').get('job')
        if active and (Path(active) / 'manga_arrays.npz').exists(): job = active
    root = tk.Tk(); MangaEditor(root, job); root.mainloop()


if __name__ == '__main__':
    main()
