"""Feature 2 (tone): reuse material tones on independently classified line art."""
import copy
import math
from pathlib import Path

import numpy as np
from PIL import Image

from . import manga, manga_tone as tone
from .coloring import binary_ink, enclosed_regions, seed_region
from .compact import image_u8, region_sheets
from .storage import read_json, write_json, digest, save_image, monitor

ROLES = ('base', 'shadow', 'highlight', 'beta')
DENSITIES = ('base', 'shadow', 'highlight', 'beta_fill')


def validate_profile(profile):
    values = copy.deepcopy(profile)
    if set(values) != {*DENSITIES, 'beta'}:
        raise ValueError('トーン素材には base/shadow/highlight/beta_fill/beta が必要です。')
    for key in DENSITIES:
        v = values[key]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 1:
            raise ValueError(f'{key} の濃度は0〜1で指定してください。')
    if not values['highlight'] <= values['base'] <= values['shadow']:
        raise ValueError('濃度はハイライト ≤ ベース ≤ 影にしてください。')
    if values['beta_fill'] >= 1 or not isinstance(values['beta'], bool):
        raise ValueError('ベタ下の濃度は1未満、betaはtrue/falseで指定してください。')
    return values


def profile_from_target(item, mask, settings):
    """Resolve auto from the actual pre-screen target, never from dotted black pixels."""
    if 'transfer_profile' in item:
        return validate_profile(item['transfer_profile'])
    base, t, spec = item['base'], item['t'], item['spec']
    dark = t[mask & (t > base + 1e-6) & ~item['part_beta']]
    light = t[mask & (t < base - 1e-6)]
    shadow = spec.get('shadow', 'auto')
    fallback = base if shadow == 'none' else max(base, settings['dark_tone'] if shadow in ('auto', 'curve') else float(shadow))
    return validate_profile(dict(base=float(base), shadow=float(np.median(dark)) if dark.size else fallback,
                                 highlight=float(np.median(light)) if light.size else
                                 (base if spec.get('highlight') == 'none' else 0.0),
                                 beta=bool(spec.get('beta', True)), beta_fill=float(item['beta_fill'])))


def reference_from_job(job):
    job, record, plan, arrays = manga.load_manga_inputs(job)
    manifest = manga.checked_manifest(job)
    if manifest.get('arrays_hash') != digest(job / 'manga_arrays.npz'):
        raise ValueError('参照の処理配列が変わっています。参照PSDを再構築してください。')
    solvers = manifest['solvers']
    if any('transfer_tone' not in s for s in solvers):
        # Old jobs remain readable without changing their plan, PSD or review.
        solvers = manga.render_manga(arrays, plan)['manifest']['solvers']
    return reference_data(dict(manifest, solvers=solvers), plan)


def reference_data(manifest, plan):
    by_id = {s['semantic_id']: s for s in manifest['solvers']}
    parts = [dict(semantic_id=p['semantic_id'], display_name=p.get('display_name', p['semantic_id']),
                  group_path=p.get('group_path') or (['背景'] if p['semantic_id'] == 'background' else ['キャラクター']),
                  tone=by_id[p['semantic_id']]['transfer_tone'],
                  tone_notes=p.get('tone_notes', p.get('coloring_notes', (p.get('tone') or {}).get('notes', ''))))
             for p in plan['parts']]
    return dict(schema='tone_reference_v1', psd_file=Path(manifest['psd_path']).name,
                psd_hash=manifest['psd_hash'], size=manifest['size'],
                tone_settings=manifest['settings'], parts=parts,
                density_method='Resolved flat base and median shadow/highlight before screening; no spatial data',
                review_note='Meaning and visual quality require review; this JSON is not a quality certificate.')


def export_tone_reference(job, manifest=None, plan=None):
    job = Path(job).resolve()
    data = reference_data(manifest, plan) if manifest is not None else reference_from_job(job)
    output = Path(read_json(job / 'layers.json')['psd_path']).parent / 'tone_reference.json'
    write_json(output, data)
    return dict(reference=str(output), parts=len(data['parts']))


def load_reference(path):
    path = Path(path).resolve()
    data = read_json(path)
    validate_reference(data)
    name = data['psd_file']
    if not isinstance(name, str) or Path(name).name != name or '/' in name or '\\' in name:
        raise ValueError('参照PSDはJSONと同じフォルダのファイル名で指定してください。')
    psd = path.parent / name
    if digest(psd) != data['psd_hash']:
        raise ValueError('参照PSDのハッシュが一致しません。')
    from psd_tools import PSDImage
    if list(PSDImage.open(psd).size) != data['size']:
        raise ValueError('参照PSDのサイズが一致しません。')
    return data, psd


def validate_reference(data):
    if data.get('schema') != 'tone_reference_v1' or not data.get('parts'):
        raise ValueError('tone_reference_v1の素材情報が必要です。')
    tone.validate_settings(data['tone_settings'])
    ids, names = set(), set()
    for part in data['parts']:
        if not part.get('semantic_id') or part['semantic_id'] in ids or not part.get('display_name') or part['display_name'] in names:
            raise ValueError('参照素材のID・表示名は空でない一意の値にしてください。')
        ids.add(part['semantic_id']); names.add(part['display_name'])
        validate_profile(part['tone'])
        if not isinstance(part.get('group_path'), list) or not 1 <= len(part['group_path']) <= 6 or any(not isinstance(v, str) or not v.strip() for v in part['group_path']):
            raise ValueError('group_pathは階層名の配列にしてください。')


def prepare_tone_transfer(lineart, job, source_job=None, source_reference=None, output=None,
                          threshold=192, gap_close=3, min_area=12):
    if (source_job is None) == (source_reference is None):
        raise ValueError('参照jobまたはtone_reference.jsonのどちらか一方を指定してください。')
    job, lineart = Path(job).resolve(), Path(lineart).resolve()
    if job.exists() and any(job.iterdir()):
        raise ValueError('既存jobは上書きしません。空の別jobを指定してください。')
    if lineart.is_relative_to(job):
        raise ValueError('入力線画は新規jobの外に置いてください。')
    source_files = {}
    if source_job:
        source = Path(source_job).resolve()
        data = reference_from_job(source)
        psd = Path(read_json(source / 'layers.json')['psd_path']).resolve()
        source_files[str(source / 'semantic_plan.json')] = digest(source / 'semantic_plan.json')
    else:
        data, psd = load_reference(source_reference)
        source_files[str(Path(source_reference).resolve())] = digest(source_reference)
    source_files[str(psd)] = digest(psd)
    validate_reference(data)
    target = Path(output).resolve() if output else job / 'output.psd'
    if target.suffix.lower() != '.psd' or target.parent == psd.parent or target == lineart:
        raise ValueError('出力は参照PSDと入力線画とは別の.psdにしてください。')
    ink = binary_ink(lineart, threshold)
    if min(ink.shape) < 32:
        raise ValueError('線画は32×32px以上が必要です。')
    labels, barriers = enclosed_regions(ink, gap_close, min_area)
    job.mkdir(parents=True, exist_ok=True)
    manga.event(job, 'tone_transfer_prepare', '第2機能（トーン版）：新線画の閉領域と参照素材を準備', psd=str(target))
    rgb = np.repeat((~ink)[..., None], 3, axis=2).astype(np.float32)
    save_image(image_u8(rgb), job / 'reference.png')
    save_image(image_u8(rgb), job / 'lineart_binary.png')
    save_image(Image.fromarray(np.uint8(~barriers) * 255), job / 'fill_barriers.png')
    np.savez_compressed(job / 'manga_arrays.npz', rgb=rgb, L=(~ink).astype(np.float32),
                        pen=ink.astype(np.float32), has_pen=True, regions=labels, fixed_ink=ink, barriers=barriers)
    write_json(job / 'source_tones.json', data)
    from psd_tools import PSDImage
    save_image(manga.render_generated_psd(PSDImage.open(psd)).convert('RGB'), job / 'source_appearance.png')
    config = dict(schema='tone_transfer_job_v1', lineart_source=str(lineart), lineart_hash=digest(lineart),
                  arrays_hash=digest(job / 'manga_arrays.npz'), reference_hash=digest(job / 'reference.png'),
                  source_hash=digest(job / 'source_tones.json'), source_files=source_files,
                  threshold=threshold, gap_close=gap_close, min_area=min_area)
    write_json(job / 'tone_transfer_job.json', config)
    write_json(job / 'manga_job.json', dict(config=dict(reference_hash=config['reference_hash']),
               feature='tone_transfer', reference_source=str(lineart), size=[ink.shape[1], ink.shape[0]],
               output=str(target), engines=dict(redraw='none', lineart='provided')))
    write_json(job / 'manga_alignment.json', dict(lineart=dict(path=str(lineart), hash=digest(lineart)),
               method='fixed_input_grid', needs_attention=False, note='No source pose, masks or coordinates transferred.'))
    original_rgb, _ = manga.load_rgb(lineart)
    manga.sheet([('Input line art (fixed)', image_u8(original_rgb)),
                 ('Binary ink', image_u8(rgb)), ('Fill barriers only', Image.open(job / 'fill_barriers.png'))],
                job / 'manga_alignment.png')
    records = region_sheets(rgb, labels, job, 'manga_regions')
    manga.region_overview(rgb, labels, job / 'manga_regions_overview.png')
    write_json(job / 'manga_regions.json', dict(regions=[dict(r, id=r['number']) for r in records], source='new_lineart'))
    # Reference settings keep their documented units (screen_lines is width-based).
    write_json(job / 'semantic_plan.json', dict(schema='manga_plan_v1', pipeline='tone_transfer',
               classification_source='unreviewed', reference_hash=config['reference_hash'],
               regions_hash=manga.digest_array(labels), tone_settings=data['tone_settings'], parts=[]))
    monitor(target, job)
    manga.event(job, 'tone_transfer_semantic_review', f'{len(records)}領域の素材・役割割当待ち',
                next_action='start_manga_adjust.batで素材と役割を選び領域を割当、確認後に反映')
    return dict(job=str(job), regions=len(records), materials=[p['semantic_id'] for p in data['parts']], psd=str(target))


def load_inputs(job, require_review=True):
    job, record = manga.manga_job(job)
    config = read_json(job / 'tone_transfer_job.json')
    files = {config['lineart_source']: config['lineart_hash'], **config['source_files'],
             str(job / 'source_tones.json'): config['source_hash'],
             str(job / 'manga_arrays.npz'): config['arrays_hash'],
             str(job / 'reference.png'): config['reference_hash']}
    for file, expected in files.items():
        if digest(file) != expected:
            raise ValueError(f'トーン引継ぎの入力が変わりました。新しいjobを準備してください: {file}')
    plan = read_json(job / 'semantic_plan.json')
    if plan.get('pipeline') != 'tone_transfer' or plan.get('reference_hash') != config['reference_hash']:
        raise ValueError('別の線画の計画です。')
    with np.load(job / 'manga_arrays.npz') as a:
        arrays = {k: a[k] for k in a.files}
    if plan.get('regions_hash') != manga.digest_array(arrays['regions']):
        raise ValueError('領域情報が計画と一致しません。')
    if require_review:
        validate_plan(plan, arrays, read_json(job / 'source_tones.json'))
    return job, record, plan, arrays


def validate_plan(plan, arrays, reference, complete=True):
    materials = {p['semantic_id'] for p in reference['parts']} | {'background'}
    available = set(int(v) for v in np.unique(arrays['regions']))
    seen = set()
    ids, names = set(), set()
    for part in plan['parts']:
        if part.get('source_material') not in materials:
            raise ValueError('未知の参照素材です。')
        if not part.get('semantic_id') or not part.get('display_name') or part['semantic_id'] in ids or part['display_name'] in names:
            raise ValueError('パーツID・表示名は空でない一意の値にしてください。')
        ids.add(part['semantic_id']); names.add(part['display_name'])
        validate_profile(part['transfer_tone'])
        roles = part.get('role_regions', {})
        if set(roles) - set(ROLES):
            raise ValueError('未知のトーン役割です。')
        assigned = [n for values in roles.values() for n in values]
        if not assigned or any(type(n) is not int for n in assigned) or len(assigned) != len(set(assigned)):
            raise ValueError('空・重複・無効な領域割当です。')
        if seen.intersection(assigned) or set(assigned) - available or sorted(assigned) != sorted(part['regions']):
            raise ValueError('重複・未知の領域、またはパーツと役割の不一致です。')
        seen.update(assigned)
    if complete:
        if available != seen:
            raise ValueError(f'未割当の領域があります: {sorted(available - seen)[:30]}')
        if plan.get('classification_source') not in ('astra_reviewed', 'user_reviewed'):
            raise ValueError('全領域の素材・役割を目視確認してください。')


def assign_region(plan, reference, region, material, role='base'):
    """Explicit move: remove an old assignment before assigning a new material/role."""
    if role not in ROLES:
        raise ValueError('未知のトーン役割です。')
    sources = {p['semantic_id']: p for p in reference['parts']}
    sources.setdefault('background', dict(semantic_id='background', display_name='背景', group_path=['背景'],
                       tone=dict(base=0., shadow=0., highlight=0., beta=False, beta_fill=0.)))
    if material not in sources:
        raise ValueError('未知の参照素材です。')
    source = sources[material]
    result = copy.deepcopy(plan)
    part = next((p for p in result['parts'] if p['semantic_id'] == material), None)
    if part is None:
        part = dict(semantic_id=material, display_name=source['display_name'], group_path=source['group_path'],
                    source_material=material, transfer_tone=copy.deepcopy(source['tone']), role_regions={}, regions=[],
                    tone_notes=source.get('tone_notes', ''))
        result['parts'].append(part)
    for p in result['parts']:
        for values in p['role_regions'].values():
            if region in values: values.remove(region)
    part['role_regions'].setdefault(role, []).append(int(region))
    for p in result['parts']:
        p['regions'] = sorted(n for values in p['role_regions'].values() for n in values)
    result['parts'] = [p for p in result['parts'] if p['regions']]
    result['classification_source'] = 'partial_review'
    return result


def assign_tone_region(job, region, material, role='base'):
    job, _, plan, arrays = load_inputs(job, require_review=False)
    if type(region) is not int or region not in arrays['regions']:
        raise ValueError('未知の領域IDです。')
    plan = assign_region(plan, read_json(job / 'source_tones.json'), region, material, role)
    write_json(job / 'semantic_plan.json', plan)
    # A CLI edit supersedes any older GUI draft.
    (job / 'tone_transfer_adjustments.json').unlink(missing_ok=True)
    manga.event(job, 'tone_transfer_semantic_review', f'領域{region}を{material}/{role}へ割当')
    return dict(job=str(job), region=region, material=material, role=role)


def draft_for_job(job):
    job = Path(job)
    path = job / 'tone_transfer_adjustments.json'
    if path.exists():
        data = read_json(path)
        if data['base_plan_hash'] == digest(job / 'semantic_plan.json'):
            return data['plan']
    return read_json(job / 'semantic_plan.json')


def save_draft(job, plan):
    write_json(Path(job) / 'tone_transfer_adjustments.json',
               dict(base_plan_hash=digest(Path(job) / 'semantic_plan.json'), plan=plan))


def protect_output(job, output):
    job, output = Path(job).resolve(), Path(output).resolve()
    config = read_json(job / 'tone_transfer_job.json')
    protected = {Path(p).resolve() for p in config['source_files']} | {Path(config['lineart_source']).resolve()}
    if output in protected or any(output.parent == p.parent for p in protected if p.suffix.lower() == '.psd'):
        raise ValueError('参照PSDのフォルダと入力線画には出力できません。別の出力先を指定してください。')
    if output.is_relative_to(job) and output.parent != job:
        raise ValueError('job内の出力はjob直下へ保存してください。')
    reserved = {'reference.png', 'lineart_binary.png', 'fill_barriers.png', 'manga_target.png',
                'editing_lineart_white.png', 'plain_finish.png', 'reconstructed.png', 'diff.png', 'aligned_redraw.png'}
    if output.parent == job and (output.name in reserved or output.name.startswith(('manga_', 'source_'))):
        raise ValueError('jobの入力・計画・確認画像を上書きできません。')


def part_target(mask, part, page, settings):
    values = validate_profile(part['transfer_tone'])
    regions = page['transfer_regions']
    base = values['base']
    target = np.full(mask.shape, base, np.float32)
    for role in ('shadow', 'highlight'):
        target[np.isin(regions, part['role_regions'].get(role, []))] = values[role]
    explicit_beta = np.isin(regions, part['role_regions'].get('beta', []))
    target[explicit_beta] = 1 if values['beta'] else values['beta_fill']
    beta = mask & (target >= .999) if values['beta'] else np.zeros(mask.shape, bool)
    if not values['beta']:
        target[mask & (target >= .999)] = values['beta_fill']
        if base >= .999: base = values['beta_fill']
    return dict(t=target, base=base, part_beta=beta, beta_fill=values['beta_fill'],
                spec=dict(base=base, shadow=values['shadow'], highlight='keep', beta=values['beta']),
                auto_level=int(np.argmin(np.abs(tone.level_covers(settings) - base))),
                level_counts=[int((mask & (target == v)).sum()) for v in tone.level_covers(settings)],
                beta_specks_removed=0, beta_dropped_as_negligible=0, transfer_profile=values)


def assignment_image(arrays, plan):
    """Unassigned = magenta. Assigned materials have stable distinct colors."""
    labels = arrays['regions']
    rgb = np.full((*labels.shape, 3), (255, 130, 230), np.uint8)
    import colorsys
    for i, p in enumerate(plan['parts']):
        color = np.array(colorsys.hsv_to_rgb((i * .618) % 1, .4, .95)) * 255
        rgb[np.isin(labels, p['regions'])] = color
    rgb[arrays['fixed_ink']] = 0
    return Image.fromarray(rgb)
