"""Widgets for creating tone-transfer jobs and assigning new lineart regions."""
import copy
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from . import storage, tone_transfer as transfer
from .storage import read_json

ROLE_LABELS = {'ベース': 'base', '影': 'shadow', 'ハイライト': 'highlight', 'ベタ': 'beta'}


def create_dialog(editor):
    window = tk.Toplevel(editor.root)
    window.title('第2機能（トーン版）— 別線画へトーン')
    window.transient(editor.root); window.grab_set()
    form = ttk.Frame(window, padding=16); form.pack(fill='both', expand=True)
    ttk.Label(form, text='参照トーンと新しい線画を選び、別の作業フォルダを作成します。').grid(row=0, column=0, columnspan=3, sticky='w', pady=6)
    kind = tk.StringVar(value='job')
    choice = ttk.Frame(form); choice.grid(row=1, column=0, columnspan=3, sticky='w')
    ttk.Radiobutton(choice, text='参照job', variable=kind, value='job').pack(side='left')
    ttk.Radiobutton(choice, text='PSDに隣接したtone_reference.json', variable=kind, value='reference').pack(side='left', padx=12)
    values = {k: tk.StringVar() for k in ('source', 'lineart', 'job', 'threshold', 'gap_close', 'min_area')}
    if editor.job and (editor.job / 'layers.json').exists(): values['source'].set(str(editor.job))
    def browse(key):
        if key == 'source' and kind.get() == 'job':
            result = filedialog.askdirectory(title='参照job', parent=window, initialdir=str(storage.ROOT / 'work'))
        elif key == 'job':
            result = filedialog.askdirectory(title='空の作業フォルダ（新規作成も可）', parent=window, initialdir=str(storage.ROOT / 'work'))
        else:
            types = [('トーン情報', '*.json')] if key == 'source' else [('線画', '*.png *.jpg *.jpeg *.webp *.bmp'), ('全ファイル', '*.*')]
            result = filedialog.askopenfilename(parent=window, filetypes=types)
        if result:
            values[key].set(result)
            if key == 'lineart' and not values['job'].get():
                values['job'].set(str(storage.ROOT / 'work' / (Path(result).stem + '_tones')))
    for row, (key, label) in enumerate((('source', '参照元'), ('lineart', '新しい線画'), ('job', '新規作業フォルダ')), 2):
        ttk.Label(form, text=label).grid(row=row, column=0, sticky='w', pady=6)
        ttk.Entry(form, textvariable=values[key], width=65).grid(row=row, column=1, padx=8)
        ttk.Button(form, text='選択', command=lambda k=key: browse(k)).grid(row=row, column=2)
    for row, (key, label, default, low, high) in enumerate((('threshold', '線の二値化閾値', 192, 1, 254),
            ('gap_close', '隙間補助（px）', 3, 0, 64), ('min_area', '最小領域（px）', 12, 1, 1000)), 5):
        values[key].set(str(default))
        ttk.Label(form, text=label).grid(row=row, column=0, sticky='w', pady=4)
        ttk.Spinbox(form, textvariable=values[key], from_=low, to=high, width=8).grid(row=row, column=1, sticky='w', padx=8)
    ttk.Label(form, text='準備後に新線画の領域を割り当てます。影・光は新線画の閉領域に指定できます。', wraplength=650).grid(row=8, column=0, columnspan=3, sticky='w', pady=12)
    def create():
        try:
            if any(not values[k].get().strip() for k in ('source', 'lineart', 'job')):
                raise ValueError('参照元・線画・新規作業フォルダを指定してください。')
            args = {k: values[k].get() for k in ('lineart', 'job')}
            args.update({k: int(values[k].get()) for k in ('threshold', 'gap_close', 'min_area')})
            args['source_job' if kind.get() == 'job' else 'source_reference'] = values['source'].get()
        except ValueError as exc:
            messagebox.showerror('入力を確認してください', str(exc), parent=window); return
        window.destroy()
        editor.start_work('prepare_transfer', lambda: transfer.prepare_tone_transfer(**args))
    ttk.Button(form, text='準備する', command=create).grid(row=9, column=2, pady=8)


class TransferControls(ttk.LabelFrame):
    def __init__(self, parent, editor):
        super().__init__(parent, text='第2機能（トーン版）：素材と領域', padding=8)
        self.editor = editor
        self.plan = None; self.loading = False; self.selected = None; self.undo_stack = []
        self.reference = None; self.arrays = None; self.overrides = {}
        self.material = tk.StringVar()
        self.material_picker = ttk.Combobox(self, textvariable=self.material, state='readonly')
        ttk.Label(self, text='参照素材').pack(anchor='w')
        ttk.Button(self, text='参照画像を見る', command=self.show_source).pack(anchor='w', pady=4)
        self.material_picker.pack(fill='x', pady=4)
        self.material_picker.bind('<<ComboboxSelected>>', self.select_material)
        self.role = tk.StringVar(value='ベース')
        row = ttk.Frame(self); row.pack(fill='x', pady=4)
        ttk.Label(row, text='貼る役割').pack(side='left')
        ttk.Combobox(row, textvariable=self.role, values=list(ROLE_LABELS), state='readonly', width=14).pack(side='left', padx=8)
        self.assignment_view = tk.BooleanVar(value=True)
        ttk.Checkbutton(self, text='領域の割当表示（クリックで選択素材を貼る）', variable=self.assignment_view,
                        command=self.display).pack(anchor='w', pady=5)
        ttk.Label(self, text='ピンクは未割当。仮色は素材の区別です。\n右ドラッグで移動。貼り直すと旧割当を移します。', wraplength=395).pack(anchor='w')
        self.count = ttk.Label(self, text=''); self.count.pack(anchor='w', pady=4)
        self.undo_button = ttk.Button(self, text='割当を1つ戻す', command=self.undo); self.undo_button.pack(anchor='w', pady=4)
        self.profile_vars = {}
        for key, label in (('base', 'ベース濃度 %'), ('shadow', '影濃度 %'), ('highlight', 'ハイライト濃度 %'), ('beta_fill', 'ベタ下の濃度 %')):
            row = ttk.Frame(self); row.pack(fill='x', pady=3)
            ttk.Label(row, text=label, width=22).pack(side='left')
            var = tk.StringVar(); self.profile_vars[key] = var
            ttk.Spinbox(row, from_=0, to=99.9 if key == 'beta_fill' else 100, increment=1, width=8, textvariable=var).pack(side='left')
            var.trace_add('write', self.profile_changed)
        self.beta = tk.BooleanVar(value=True)
        ttk.Checkbutton(self, text='ベタを使う', variable=self.beta, command=self.profile_changed).pack(anchor='w', pady=4)
        self.notes = ttk.Label(self, text='', wraplength=390); self.notes.pack(anchor='w', pady=4)
        self.reviewed = tk.BooleanVar(value=False)
        ttk.Checkbutton(self, text='全領域の素材・役割を目視確認した', variable=self.reviewed,
                        command=editor.mark_dirty).pack(anchor='w', pady=6)
        ttk.Label(self, text='濃度は参照素材から引継ぎ。下の網設定で調整できます。\n線画は固定。明暗の自動推定や元ポーズの移植は行いません。', wraplength=395).pack(anchor='w')

    def load(self, job):
        _, _, _, self.arrays = transfer.load_inputs(job, require_review=False)
        self.reference = read_json(job / 'source_tones.json')
        self.plan = copy.deepcopy(transfer.draft_for_job(job))
        transfer.validate_plan(self.plan, self.arrays, self.reference, complete=False)
        self.materials = {p['semantic_id']: copy.deepcopy(p) for p in self.reference['parts']}
        self.materials.setdefault('background', dict(semantic_id='background', display_name='背景', group_path=['背景'],
            tone=dict(base=0., shadow=0., highlight=0., beta=False, beta_fill=0.)))
        self.labels = [f'{p["display_name"]} [{k}]' for k, p in self.materials.items()]
        self.keys = list(self.materials)
        self.material_picker.configure(values=self.labels)
        self.overrides = {p['source_material']: copy.deepcopy(p['transfer_tone']) for p in self.plan['parts']}
        self.undo_stack = []; self.selected = None
        self.material.set(self.labels[0]); self.select_material()
        reviewed = self.plan.get('classification_source') in ('astra_reviewed', 'user_reviewed')
        self.reviewed.set(reviewed); self.assignment_view.set(not reviewed)
        self.update_count()

    def profile(self):
        return transfer.validate_profile(dict({key: float(var.get()) / 100 for key, var in self.profile_vars.items()}, beta=self.beta.get()))

    def show_source(self):
        from PIL import Image, ImageTk
        path = self.editor.job / 'source_appearance.png'
        if not path.exists():
            messagebox.showinfo('参照画像', 'このjobには参照プレビューがありません。参照PSDを確認してください。', parent=self.editor.root)
            return
        window = tk.Toplevel(self.editor.root); window.title('参照トーンPSD')
        picture = Image.open(path).convert('RGB'); picture.thumbnail((850, 800), Image.Resampling.LANCZOS)
        window.photo = ImageTk.PhotoImage(picture)
        ttk.Label(window, image=window.photo).pack(padx=8, pady=8)

    def store_profile(self):
        if self.selected is None: return
        values = self.profile()
        self.overrides[self.selected] = values
        for part in self.plan['parts']:
            if part['source_material'] == self.selected: part['transfer_tone'] = copy.deepcopy(values)

    def select_material(self, _event=None):
        try:
            self.store_profile()
        except ValueError as exc:
            self.material.set(self.labels[self.keys.index(self.selected)])
            messagebox.showerror('濃度を確認してください', str(exc), parent=self.editor.root); return
        self.selected = self.keys[self.labels.index(self.material.get())]
        profile = self.overrides.get(self.selected, self.materials[self.selected]['tone'])
        self.loading = True
        for key, var in self.profile_vars.items(): var.set(f'{profile[key] * 100:.6g}')
        self.beta.set(profile['beta']); self.loading = False
        self.notes.configure(text=self.materials[self.selected].get('tone_notes', ''))

    def profile_changed(self, *_):
        if not self.loading: self.editor.mark_dirty()

    def update_count(self):
        import numpy as np
        total = len(np.unique(self.arrays['regions']))
        used = sum(len(p['regions']) for p in self.plan['parts'])
        self.count.configure(text=f'割当済み {used} / {total} 領域（未割当 {total - used}）')

    def display(self):
        if self.plan is None: return
        if self.assignment_view.get(): self.editor.picture = transfer.assignment_image(self.arrays, self.plan)
        else: self.editor.picture = self.editor.applied['image'] if self.editor.applied else None
        self.editor.draw()

    def click(self, x, y):
        if self.editor.busy or not self.assignment_view.get(): return
        try:
            self.store_profile()
            region = transfer.seed_region(self.arrays, x, y)
            self.undo_stack.append(copy.deepcopy(self.plan))
            self.plan = transfer.assign_region(self.plan, self.reference, region, self.selected, ROLE_LABELS[self.role.get()])
            for part in self.plan['parts']:
                if part['source_material'] in self.overrides:
                    part['transfer_tone'] = copy.deepcopy(self.overrides[part['source_material']])
            self.reviewed.set(False); self.editor.mark_dirty(); self.update_count(); self.display()
            self.editor.status.set(f'領域 {region} → {self.material.get()} / {self.role.get()}。全割当後に確認して「反映」。')
        except ValueError as exc:
            self.editor.status.set(str(exc))

    def undo(self):
        if self.editor.busy or not self.undo_stack: return
        self.plan = self.undo_stack.pop(); self.reviewed.set(False)
        self.editor.mark_dirty(); self.update_count(); self.display()

    def draft(self):
        self.store_profile()
        plan = copy.deepcopy(self.plan)
        plan['classification_source'] = 'user_reviewed' if self.reviewed.get() else 'partial_review'
        transfer.validate_plan(plan, self.arrays, self.reference)
        return plan
