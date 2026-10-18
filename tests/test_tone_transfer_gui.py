"""Tk regression for draft/apply/export gates and switching between both tone features."""
import time
import tkinter as tk
from tkinter import messagebox

import numpy as np
import pytest

from anime_layer_agent.manga_editor import MangaEditor
from anime_layer_agent.storage import digest
from test_tone_transfer import source, inputs, prepare_target


def test_transfer_editor_draft_gates_and_switching(tmp_path, source, monkeypatch):
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip('Tk display is unavailable')
    root.withdraw()
    errors = []
    monkeypatch.setattr(messagebox, 'showerror', lambda *a, **kw: errors.append(a))
    monkeypatch.setattr(messagebox, 'showwarning', lambda *a, **kw: errors.append(a))
    job, plan, arrays = prepare_target(tmp_path, source)
    editor = None
    def settle():
        deadline = time.monotonic() + 15
        while editor.busy and time.monotonic() < deadline:
            root.update(); time.sleep(.02)
        root.update()
        assert not editor.busy and not errors
    try:
        editor = MangaEditor(root, job); settle()
        assert editor.transfer_active
        assert editor.transfer_holder.winfo_manager() == 'pack'
        before = digest(job / 'semantic_plan.json')
        original = np.asarray(editor.picture).copy()
        controls = editor.transfer_controls
        controls.material.set(controls.labels[controls.keys.index('dress')]); controls.select_material()
        controls.profile_vars['base'].set('25'); controls.profile_vars['shadow'].set('65')
        assert editor.dirty
        assert str(editor.export_button['state']) == str(editor.png_button['state']) == 'disabled'
        editor.apply(); settle()
        assert not np.array_equal(original, np.asarray(editor.picture))
        assert digest(job / 'semantic_plan.json') == before
        editor.open_job(source); settle()
        assert not editor.transfer_active
        assert editor.transfer_holder.winfo_manager() == ''  # An empty frame must not retain its old height.
        assert editor.mode_row.winfo_manager() == editor.mode_holder.winfo_manager() == 'pack'
        editor.open_job(job); settle()
        assert editor.transfer_active and not editor.dirty
        assert str(editor.png_button['state']) == 'normal'
        # Invalid manual input must invalidate both outputs.
        controls.profile_vars['base'].set('bad')
        assert str(editor.export_button['state']) == str(editor.png_button['state']) == 'disabled'
    finally:
        if editor:
            while editor.busy:
                root.update(); time.sleep(.02)
            editor.closed = True
        root.destroy()
