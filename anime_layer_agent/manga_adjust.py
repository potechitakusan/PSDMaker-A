"""Persist applied manga controls and share the renderer with interactive/headless export."""
import copy
from io import BytesIO
from datetime import datetime
from pathlib import Path

from . import manga, manga_tone as tone, storage
from .compact import image_u8
from .storage import read_json, write_json, digest, atomic_write


def last_settings_path():
    return storage.DOCS / 'manga_last_settings.json'


def presets_dir():
    return storage.ROOT / 'presets' / 'manga'


def save_preset(path, settings):
    """A named, dated copy of the controls that any job can load."""
    path = Path(path)
    data = dict(schema='manga_preset_v1', name=path.stem, saved_at=datetime.now().astimezone().isoformat(timespec='seconds'),
                tone_settings=tone.validate_settings(settings))
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, data)
    return data


def load_preset(path):
    data = read_json(path)
    if data.get('schema') != 'manga_preset_v1':
        raise ValueError('漫画トーン調整のプリセットではありません。')
    return dict(data, tone_settings=tone.validate_settings(data['tone_settings']))


def context(job):
    job = Path(job)
    names = ['semantic_plan.json', 'manga_arrays.npz', 'manga_alignment.json']
    if (job / 'tone_transfer_job.json').exists():
        names += ['tone_transfer_job.json', 'source_tones.json', 'tone_transfer_adjustments.json']
    result = {name: digest(job / name) if (job / name).exists() else None for name in names}
    if (job / 'tone_transfer_job.json').exists():
        config = read_json(job / 'tone_transfer_job.json')
        for path in [config['lineart_source'], *config['source_files']]:
            result['input:' + path] = digest(path)
    return result


def settings_for_job(job, reuse_last=False, settings_file=None):
    job = Path(job)
    if settings_file:
        data = read_json(settings_file)
    elif reuse_last:
        path = job / 'manga_adjustments.json'
        if not path.exists():
            path = last_settings_path()
        if not path.exists():
            raise ValueError('保存済みの調整値がありません。画面で反映するか --settings を指定してください。')
        data = read_json(path)
    elif (job / 'manga_adjustments.json').exists():
        data = read_json(job / 'manga_adjustments.json')
    else:
        data = read_json(job / 'semantic_plan.json')['tone_settings']
    return tone.validate_settings(data.get('tone_settings', data))


def save_settings(job, settings):
    data = dict(schema='manga_adjustments_v1', tone_settings=tone.validate_settings(settings))
    write_json(Path(job) / 'manga_adjustments.json', data)
    write_json(last_settings_path(), data)


def adjustment_inputs(job, transfer_plan=None):
    if (Path(job) / 'tone_transfer_job.json').exists():
        from . import tone_transfer as transfer
        job, record, plan, arrays = transfer.load_inputs(job, require_review=False)
        candidate = copy.deepcopy(transfer_plan if transfer_plan is not None else transfer.draft_for_job(job))
        if any(candidate.get(k) != plan.get(k) for k in ('pipeline', 'reference_hash', 'regions_hash')):
            raise ValueError('調整計画が現在の線画と一致しません。')
        transfer.validate_plan(candidate, arrays, read_json(job / 'source_tones.json'))
        return job, record, candidate, arrays
    return manga.load_manga_inputs(job)


def preview_manga(job, settings, persist=True, transfer_plan=None):
    before = context(job)
    job, record, plan, arrays = adjustment_inputs(job, transfer_plan)
    plan = copy.deepcopy(plan)
    plan['tone_settings'] = tone.validate_settings(settings)
    result = manga.render_manga(arrays, plan)
    if context(job) != before:
        raise ValueError('計算中に入力または計画が変わりました。もう一度反映してください。')
    picture = image_u8(result['target'])
    if persist:
        save_settings(job, settings)
        if plan.get('pipeline') == 'tone_transfer':
            from .tone_transfer import save_draft
            save_draft(job, plan)
    pitch = result['page']['pitch']
    return dict(image=picture, context=context(job), settings=plan['tone_settings'], plan=plan,
                pitch_px=pitch, pixel_layers=result['manifest']['pixel_layer_count'],
                warning=('網の間隔が5px未満です。線数を下げるか解像度を上げてください。'
                         if pitch < 5 and settings['tone_format'] == 'dots' else ''))


def export_manga(job, output=None, reuse_last=False, settings=None, expected_context=None, review=True, transfer_plan=None):
    """CLI settings is a JSON path; GUI may pass a validated dict."""
    job, record, plan, arrays = adjustment_inputs(job, transfer_plan)
    del arrays
    if expected_context is not None and context(job) != expected_context:
        raise ValueError('プレビュー後に入力または計画が変わりました。もう一度反映してください。')
    values = (tone.validate_settings(settings) if isinstance(settings, dict) else
              settings_for_job(job, reuse_last=reuse_last, settings_file=settings))
    target = Path(output or record['output']).resolve()
    if target.suffix.lower() != '.psd':
        raise ValueError('出力先は .psd を指定してください。')
    if plan.get('pipeline') == 'tone_transfer':
        from .tone_transfer import protect_output
        protect_output(job, target)
    # Keep the previous reviewed plan available, and never report a preview as a review.
    old_bytes = (job / 'semantic_plan.json').read_bytes()
    old_plan = read_json(job / 'semantic_plan.json')
    plan['tone_settings'] = values
    if old_plan != plan:
        atomic_write(job / 'history' / f'pre_adjust_{digest(job / "semantic_plan.json")[:12]}.json', old_bytes)
        write_json(job / 'semantic_plan.json', plan)
    result = manga.build_manga(job, target)
    save_settings(job, values)
    if plan.get('pipeline') == 'tone_transfer':
        from .tone_transfer import save_draft
        save_draft(job, plan)
    if review:
        result['review'] = manga.post_review(job)
    result['review_status'] = 'post_review_required'
    result['settings'] = values
    return result


def export_manga_png(job, output, reuse_last=False, settings=None, expected_context=None, transfer_plan=None):
    """Export the same renderer at full resolution, with an atomic PNG round-trip."""
    job, target = Path(job).resolve(), Path(output).resolve()
    if target.suffix.lower() != '.png':
        raise ValueError('出力先は .png を指定してください。')
    if (job / 'tone_transfer_job.json').exists():
        from .tone_transfer import protect_output
        protect_output(job, target)
    else:
        record = read_json(job / 'manga_job.json')
        protected = {Path(record['reference_source']).resolve()}
        alignment = read_json(job / 'manga_alignment.json')
        protected.update(Path(alignment[k]['path']).resolve() for k in ('redraw', 'lineart') if k in alignment)
        protected.update(Path(v['path']).resolve() for v in record.get('generated', {}).values() if 'path' in v)
        reserved = {'reference.png', 'generation_input.png', 'editing_lineart_white.png', 'plain_finish.png', 'reconstructed.png', 'diff.png'}
        if target in protected or (target.parent == job and (target.name in reserved or target.name.startswith(('manga_', 'aligned_', 'source_')))):
            raise ValueError('入力・jobの確認画像とは別のPNGへ保存してください。')
    if expected_context is not None and context(job) != expected_context:
        raise ValueError('プレビュー後に入力または計画が変わりました。もう一度反映してください。')
    values = (tone.validate_settings(settings) if isinstance(settings, dict) else
              settings_for_job(job, reuse_last=reuse_last, settings_file=settings))
    result = preview_manga(job, values, persist=False, transfer_plan=transfer_plan)
    buffer = BytesIO()
    kwargs = dict(dpi=(values['dpi'], values['dpi'])) if values['dpi'] else {}
    result['image'].save(buffer, format='PNG', **kwargs)
    from PIL import Image
    import numpy as np
    with Image.open(BytesIO(buffer.getvalue())) as back:
        if not np.array_equal(np.asarray(back), np.asarray(result['image'])):
            raise ValueError('PNG読み戻しがプレビューと一致しません。')
    atomic_write(target, buffer.getvalue())
    save_settings(job, values)
    if result['plan'].get('pipeline') == 'tone_transfer':
        from .tone_transfer import save_draft
        save_draft(job, result['plan'])
    manga.event(job, 'tone_png_exported', f'原寸PNGを保存: {target}', next_action='PNGの見た目を確認。PSDは別途出力・事後確認')
    return dict(png=str(target), passed=True, size=list(result['image'].size), settings=values,
                review_status='visual_review_required')
