"""Feature 1 (tone): a color illustration to an editable black-and-white manga PSD.

Codex judges the semantic parts on the color illustration. An image edit model
(Codex imagegen or Qwen-Image 2.1 through ComfyUI) only redraws values/lines;
Python maps that image back to the original grid, plans the tones per part and
writes Lineart / Beta / White / per-part Base-Shadow-Highlight tone layers.
No existing decomposition or coloring command calls this module.
"""
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from psd_tools import PSDImage
from scipy import ndimage as ndi
from skimage.color import hsv2rgb

from . import storage
from .storage import read_json, write_json, digest, save_image, atomic_write, progress, monitor
from .compact import image_u8, region_sheets
from .regions import geometry_labels, bbox_of
from .composition import export_psd, render_generated_psd
from .artist import part_group_path, count_groups
from . import manga_tone as tone

MANGA_VERSION = 1
ENGINES = ('imagegen', 'qwen', 'none')
LINE_ENGINES = ('imagegen', 'qwen', 'provided', 'redraw')
FITS = ('auto', 'stretch', 'contain', 'cover')
# Edit instruction from the QWEN-Manga workflow of ComfyUI-MangaScreen, Copyright (c) 2026 ashukae,
# MIT License (THIRD_PARTY_NOTICES.md). <image1> is the input image.
REDRAW_PROMPT = ('Redraw <image1> as a printed black-and-white manga page panel, as if inked by the original artist for '
                 'serialization. Keep the exact composition, pose, character design, face, expression and drawing style of '
                 '<image1>. Use confident ink lines with varied line weight, solid black for dark hair and deep shadows, white '
                 'paper for skin and light surfaces, ink hatching lines for shadows on cloth, and regular screentone only where '
                 'a mid tone is needed. No color and no smooth gradients. Do not add anything that is not in <image1>.')
LINEART_PROMPT = ('Convert <image1> into clean black ink line art on pure white paper. Keep the exact canvas, composition, '
                  'pose, proportions, face and every contour of <image1> at the same position. Draw outlines, hair strands, '
                  'folds and part boundaries only. No shading, no screentone, no solid fills, no gray, no color, no frame, '
                  'and do not add or remove anything.')
TONE_WORDS = {'white': 0, 'light': 1, 'dark': 2, 'black': 3}
ROLE_ORDER = ('Paper', 'Base', 'Shadows', 'Highlights', 'Beta', 'Lineart', 'White')
CHECKS = {
    'input_alignment': '生成画像の倍率換算（縮小入力→元サイズ）・局所位置合わせ・残る再描画差',
    'part_masks': 'カラー原画基準のパーツ分割、飛び地・別パーツへの混入',
    'tone_values': 'パーツごとの白/トーン/ベタの濃度が意味に合うか',
    'shadow_highlight': '影・ハイライトの分離とOFF時の見え方',
    'beta_white': 'ベタの範囲と白抜き・ホワイトの量と位置',
    'linework': '線画単独、欠け・ノイズ・二重線・トーンへの線混入',
    'screen': '網点の線数・角度統一、モアレ・ドット潰れ・網の継ぎ目',
    'hierarchy_budget': 'レイヤー階層・日本語名・描画枚数とフォルダ数',
    'readback': '保存PSDの読み戻しとPython目標の一致',
    'delivery': '出力実在・入力不変・未達/実アプリ未確認/AI再描画差の明記',
}


def event(job, phase, message, **extra):
    job = Path(job)
    now = datetime.now(timezone.utc)
    path = job / 'timing.json'
    timing = read_json(path) if path.exists() else dict(
        started_at=now.isoformat(), origin='First manga CLI work timestamp; request receipt unavailable', phases=[])
    elapsed = (now - datetime.fromisoformat(timing['started_at'])).total_seconds()
    timing['phases'].append(dict(phase=phase, at=now.isoformat(), elapsed_seconds=round(elapsed, 3)))
    timing.update(elapsed_seconds=round(elapsed, 3), elapsed_human=f'{int(elapsed) // 60}m {int(elapsed) % 60}s')
    if phase in ('complete', 'complete_with_limitations'):
        timing['completed_at'] = now.isoformat()
    write_json(path, timing)
    state = progress(job, phase, message, **extra)
    if phase in ('complete', 'complete_with_limitations'):
        md = ['# 漫画トーン化作業時間', '', f'開始: {timing["started_at"]}', f'完了: {now.isoformat()}',
              f'経過: {elapsed:.3f}秒 / {int(elapsed) // 60}分{int(elapsed) % 60}秒', f'起点: {timing["origin"]}', '']
        md += [f'- {p["phase"]}: {p["at"]} / {p["elapsed_seconds"]}秒' for p in timing['phases']]
        atomic_write(job / 'time_log.md', '\n'.join(md) + '\n')
        if state.get('psd') and Path(state['psd']).parent != job:
            atomic_write(Path(state['psd']).parent / 'time_log.md', (job / 'time_log.md').read_bytes())
    return state


def load_rgb(path):
    with Image.open(path) as source:
        rgba = np.asarray(source.convert('RGBA'), np.float32) / 255
    had_alpha = bool(np.any(rgba[..., 3] < 1))
    rgb = rgba[..., :3] * rgba[..., 3:] + (1 - rgba[..., 3:])
    return rgb.astype(np.float32), had_alpha


def manga_job(job):
    job = Path(job).resolve()
    path = job / 'manga_job.json'
    if not path.exists():
        raise ValueError('Not a manga job; run prepare-manga first')
    return job, read_json(path)


def prepare_manga(reference, job, redraw_engine='imagegen', line_engine=None, max_side=1536, source_job=None, output=None):
    """Create the job, the downscaled generation input and the scale record."""
    if redraw_engine not in ENGINES:
        raise ValueError(f'redraw_engine must be one of {ENGINES}')
    line_engine = line_engine or ('redraw' if redraw_engine == 'none' else redraw_engine)
    if line_engine not in LINE_ENGINES:
        raise ValueError(f'line_engine must be one of {LINE_ENGINES}')
    if not isinstance(max_side, int) or not 256 <= max_side <= 4096:
        raise ValueError('max_side must be 256..4096')
    job = Path(job).resolve()
    if (job / 'tone_transfer_job.json').exists():
        raise ValueError('Use a fresh job; this is a second-feature tone transfer job')
    config = dict(reference_hash=digest(reference), manga_version=MANGA_VERSION)
    previous = read_json(job / 'manga_job.json') if (job / 'manga_job.json').exists() else None
    if previous and previous['config'] != config:
        raise ValueError('Use a fresh job for a different reference')
    if (job / 'compact_job.json').exists() or (job / 'coloring_job.json').exists():
        raise ValueError('This job belongs to another feature; use a fresh manga job folder')
    job.mkdir(parents=True, exist_ok=True)
    rgb, had_alpha = load_rgb(reference)
    h, w = rgb.shape[:2]
    if min(h, w) < 32:
        raise ValueError('Reference must be at least 32 x 32 pixels')
    save_image(image_u8(rgb), job / 'reference.png')
    scale = min(1.0, max_side / max(w, h))
    gw, gh = max(1, round(w * scale)), max(1, round(h * scale))
    picture = image_u8(rgb)
    if (gw, gh) != (w, h):
        picture = picture.resize((gw, gh), Image.Resampling.LANCZOS)
    save_image(picture, job / 'generation_input.png')
    imported = None
    if source_job:
        imported = import_source_parts(source_job, job / 'reference.png')
    prompts = dict(redraw=REDRAW_PROMPT, lineart=LINEART_PROMPT,
                   imagegen_note='Codex imagegen: pass generation_input.png and replace <image1> with "the input image". '
                                 'Save the result as a separate file; keep the full canvas without border or crop.')
    write_json(job / 'prompts.json', prompts)
    output = Path(output).resolve() if output else job / 'output.psd'
    record = dict(config=config, reference_source=str(Path(reference).resolve()), reference_had_alpha=had_alpha,
                  size=[w, h], generation_input=dict(path='generation_input.png', size=[gw, gh],
                                                     scale_to_input=[gw / w, gh / h], scale_to_original=[w / gw, h / gh]),
                  engines=dict(redraw=redraw_engine, lineart=line_engine), source_job=str(Path(source_job).resolve()) if source_job else None,
                  generated=previous['generated'] if previous else {}, output=str(output))
    write_json(job / 'manga_job.json', record)
    if imported:
        np.save(job / 'source_labels.npy', imported['labels'])
        write_json(job / 'source_parts.json', imported['parts'])
    monitor(output, job)
    steps = {'imagegen': 'Codex imagegenでgeneration_input.pngとprompts.jsonから再描画/線画を別ファイルに生成',
             'qwen': 'ComfyUIを起動しrun-comfy-manga --task redraw / lineartを実行',
             'none': '再描画なし（Manga Levels）。必要なら線画を用意'}
    event(job, 'manga_generation', '元サイズと生成用縮小サイズを記録。再描画・線画の生成待ち', psd=str(output),
          next_action=steps[redraw_engine] + '→register-manga')
    return dict(job=str(job), size=[w, h], generation_input=str(job / 'generation_input.png'), generation_size=[gw, gh],
                engines=record['engines'], prompts=str(job / 'prompts.json'), imported_parts=len(imported['parts']) if imported else 0,
                next_action=steps[redraw_engine])


def import_source_parts(source_job, reference_png):
    """Reuse reviewed first-feature masks for the same illustration (opt-in)."""
    source = Path(source_job).resolve()
    if not (source / 'compact_job.json').exists() or not (source / 'semantic_labels.npy').exists():
        raise ValueError('source_job must be a built first-feature compact job (semantic_labels.npy)')
    plan = read_json(source / 'semantic_plan.json')
    labels = np.load(source / 'semantic_labels.npy').astype(np.int32)
    with Image.open(source / 'reference.png') as a, Image.open(reference_png) as b:
        if a.size != b.size or np.any(np.asarray(a.convert('RGB')) != np.asarray(b.convert('RGB'))):
            raise ValueError('source_job reference differs from this illustration')
    parts = [dict(semantic_id=p['semantic_id'], display_name=p.get('display_name', p['semantic_id']),
                  group_path=['背景'] if p['semantic_id'] == 'background' else p.get('group_path', ['キャラクター']),
                  regions=[i], coloring_notes=p.get('coloring_notes', '')) for i, p in enumerate(plan['parts'], 1)]
    return dict(labels=labels, parts=parts)


def run_comfy_manga(job, task='redraw', url='http://127.0.0.1:8188', unet=None, clip=None, vae=None, seed=1,
                    megapixels=1.0, cache_device='cpu', wait_seconds=900, prompt=None):
    """Queue the Qwen-Image 2.1 redraw or the Manga Lineart extractor on a running ComfyUI."""
    from .comfy_client import ComfyClient, REDRAW_NODES, MANGA_LINE_NODES, redraw_graph, manga_line_graph, choose_model
    job, record = manga_job(job)
    if task not in ('redraw', 'lineart', 'qwen_lineart'):
        raise ValueError('task must be redraw, lineart (Manga Line Extraction) or qwen_lineart')
    if not 0.25 <= megapixels <= 4:
        raise ValueError('megapixels must be 0.25..4')
    client = ComfyClient(url)
    prompts = read_json(job / 'prompts.json')
    if task == 'lineart':
        client.require_nodes(MANGA_LINE_NODES)
        width = record['size'][0]
        resolution = max(64, round(width / 64) * 64)
        graph = manga_line_graph(client.upload(job / 'reference.png'), resolution, f'PsdMaker/{job.name}_lineart')
        details = dict(node='Manga2Anime_LineArt_Preprocessor', resolution=resolution, input='reference.png')
    else:
        info = client.require_nodes(REDRAW_NODES)
        unet = choose_model(info, 'UNETLoader', 'unet_name', [('qwen_image_2.1', 'turbo'), ('qwen-image-2.1', 'turbo')], unet)
        clip = choose_model(info, 'CLIPLoader', 'clip_name', [('qwen3', 'vl'), ('qwen3',)], clip)
        vae = choose_model(info, 'VAELoader', 'vae_name', [('qwen_image_2.1', 'vae'), ('qwen-image-2.1', 'vae')], vae)
        text = prompt or prompts['redraw' if task == 'redraw' else 'lineart']
        graph = redraw_graph(client.upload(job / 'generation_input.png'), text, unet, clip, vae, seed, megapixels,
                             cache_device=cache_device, prefix=f'PsdMaker/{job.name}_{task}')
        details = dict(unet=unet, clip=clip, vae=vae, seed=int(seed), megapixels=megapixels, prompt=text,
                       sigmas='turbo 8-step', input='generation_input.png')
    try:
        stats = client.get_json('/system_stats').get('system', {})
    except RuntimeError:
        stats = {}
    event(job, 'manga_generation', f'ComfyUIで{task}を実行中', next_action='完了待ち')
    prompt_id, images = client.run(graph, wait_seconds)
    path = client.download(images[0], job / 'generated' / f'{task}_qwen.png')
    with Image.open(path) as picture:
        size = list(picture.size)
    record['generated'][task] = dict(engine='qwen' if task != 'lineart' else 'qwen_manga_line', path=str(path), hash=digest(path),
                                     size=size, prompt_id=prompt_id, comfyui_version=stats.get('comfyui_version'),
                                     at=datetime.now(timezone.utc).isoformat(), **details)
    write_json(job / 'manga_job.json', record)
    event(job, 'manga_generation', f'{task}を保存: {path.name}', next_action='生成画像を開いて確認し、register-mangaで位置合わせ')
    return dict(job=str(job), task=task, image=str(path), size=size, details=details)


def line_feature(gray):
    """Thin dark lines of the color original as 0..1; boundaries between flat colors stay out."""
    g = np.uint8(np.clip(gray, 0, 1) * 255)
    closed = cv2.morphologyEx(g, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)))
    return np.clip((closed.astype(np.float32) - g) / 255 * 6, 0, 1)


def stroke_feature(gray):
    """Strokes only (no screentone, paper grain or solid masses), as 0/1 float.

    Redraws carry dots and texture that a plain edge filter would count as lines.
    """
    k = gray.shape[1] / 1024
    ink, solid = tone.ink_layer(np.asarray(gray, np.float32), k)
    return (ink & ~tone.solid_mass(solid, k)).astype(np.float32)


def fit_candidates(gen_size, input_size):
    """Rectangle of the generated image that shows the whole generation input, per fit rule."""
    gw, gh = gen_size
    iw, ih = input_size
    out = {'stretch': (0.0, 0.0, float(gw), float(gh))}
    for name, s in (('contain', min(gw / iw, gh / ih)), ('cover', max(gw / iw, gh / ih))):
        cw, ch = iw * s, ih * s
        box = ((gw - cw) / 2, (gh - ch) / 2, (gw + cw) / 2, (gh + ch) / 2)
        if max(abs(a - b) for a, b in zip(box, out['stretch'])) > .5:
            out[name] = box
    return out


def map_to_original(image, box, size):
    """Warp a generated image to the original grid; box is the original canvas in generated pixels."""
    w, h = size
    x0, y0, x1, y1 = box
    sx, sy = (x1 - x0) / w, (y1 - y0) / h
    # Pixel centers: original (x + .5) lands on generated x0 + (x + .5) * sx.
    matrix = np.float32([[sx, 0, x0 + .5 * sx - .5], [0, sy, y0 + .5 * sy - .5]])
    flags = cv2.INTER_AREA if sx > 1 else cv2.INTER_CUBIC
    warped = cv2.warpAffine(image, matrix, (w, h), flags=flags | cv2.WARP_INVERSE_MAP, borderMode=cv2.BORDER_CONSTANT, borderValue=1.0)
    gh, gw = image.shape[:2]
    cx = x0 + (np.arange(w) + .5) * sx
    cy = y0 + (np.arange(h) + .5) * sy
    valid = ((cy >= 0) & (cy <= gh))[:, None] & ((cx >= 0) & (cx <= gw))[None, :]
    return np.clip(warped, 0, 1).astype(np.float32), valid


def chamfer(a, b, valid, cap=10.0):
    """Symmetric mean distance between two line features (px), each distance capped so strokes
    that only one image has cannot dominate, while sub-pixel shifts still change the score."""
    ea, eb = (a > .25) & valid, (b > .25) & valid
    if not ea.any() or not eb.any():
        return None
    da, db = ndi.distance_transform_edt(~ea), ndi.distance_transform_edt(~eb)
    return float((np.minimum(db[ea], cap).mean() + np.minimum(da[eb], cap).mean()) / 2)


def within(a, b, valid, radius=2.0):
    """Fraction of b's line pixels that lie within radius px of a's lines."""
    ea, eb = (a > .25) & valid, (b > .25) & valid
    if not ea.any() or not eb.any():
        return None
    return float((ndi.distance_transform_edt(~ea)[eb] <= radius).mean())


def register_image(path, original_gray, input_size, fit='auto', max_shift=12, kind='redraw'):
    with Image.open(path) as source:
        gen_size = source.size
        rgba = np.asarray(source.convert('RGBA'), np.float32) / 255
    foreground = kind == 'foreground'
    if foreground and (not (rgba[..., 3] < .5).any() or not (rgba[..., 3] >= .5).any()):
        raise ValueError('Foreground image needs actual transparent background and opaque subject pixels')
    g = tone.gray(rgba[..., :3] * rgba[..., 3:] + (1 - rgba[..., 3:]))
    inverted = False
    if kind == 'lineart' and g.mean() < .5:  # Manga Line Extraction: white lines on black
        g, inverted = 1 - g, True
    h, w = original_gray.shape
    reference_feature = line_feature(original_gray)
    candidates = fit_candidates(gen_size, input_size)
    if fit != 'auto':
        if fit not in candidates:
            raise ValueError(f'fit {fit} is identical to stretch for this aspect; use auto or stretch')
        candidates = {fit: candidates[fit]}
    scores = {}
    mapped = {}
    alpha_maps = {}
    for name, box in candidates.items():
        image, valid = map_to_original(g, box, (w, h))
        mapped[name] = (image, valid, box)
        score_valid = valid
        if foreground:
            alpha, _ = map_to_original(1 - rgba[..., 3], box, (w, h))
            alpha_maps[name] = 1 - alpha
            score_valid = valid & ndi.binary_erosion(alpha_maps[name] >= .5, iterations=3)
        scores[name] = chamfer(reference_feature, stroke_feature(image), score_valid)
    usable = {k: v for k, v in scores.items() if v is not None}
    chosen = min(usable, key=usable.get) if usable else next(iter(candidates))
    image, valid, box = mapped[chosen]
    before = scores.get(chosen)
    yy, xx = np.mgrid[:h, :w].astype(np.float32)
    aligned, valid_aligned, after, flow, flow_used = image, valid, before, np.zeros((h, w, 2), np.float32), False
    if max_shift:
        feature_mask = ndi.binary_erosion(alpha_maps[chosen] >= .5, iterations=3) if foreground else 1
        target = np.uint8(cv2.GaussianBlur(reference_feature * feature_mask, (0, 0), 1.2) * 255)
        source = np.uint8(cv2.GaussianBlur(stroke_feature(image) * feature_mask, (0, 0), 1.2) * 255)
        candidate = np.clip(cv2.calcOpticalFlowFarneback(target, source, None, .5, 5, 31, 5, 7, 1.5, 0), -max_shift, max_shift)
        # A smooth field follows redraw drift without bending shapes toward strokes the original lacks.
        candidate = cv2.GaussianBlur(candidate, (0, 0), max(4.0, 16 * w / 1024))
        warped = cv2.remap(image, xx + candidate[..., 0], yy + candidate[..., 1], cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        warped_valid = cv2.remap(valid.astype(np.float32), xx + candidate[..., 0], yy + candidate[..., 1], cv2.INTER_NEAREST,
                                 borderMode=cv2.BORDER_CONSTANT, borderValue=0) > .5
        score = chamfer(reference_feature, stroke_feature(warped), warped_valid & feature_mask if foreground else warped_valid)
        if score is not None and (before is None or score < before - .05):
            aligned, valid_aligned, after, flow, flow_used = warped, warped_valid, score, candidate, True
    magnitude = np.linalg.norm(flow, axis=2)
    sx, sy = (box[2] - box[0]) / w, (box[3] - box[1]) / h
    stats = dict(path=str(Path(path).resolve()), hash=digest(path), generated_size=list(gen_size), generation_input_size=list(input_size),
                 original_size=[w, h], fit=chosen, fit_scores=scores, inverted_polarity=inverted,
                 original_px_per_generated_px=[1 / sx, 1 / sy], generated_box_of_original=[round(v, 2) for v in box],
                 edge_distance_before_flow_px=before, edge_distance_after_flow_px=after, flow_used=flow_used, max_shift=max_shift,
                 median_flow_px=float(np.median(magnitude)), max_flow_px=float(magnitude.max()),
                 generated_lines_within_2px=within(reference_feature, stroke_feature(aligned), valid_aligned),
                 invalid_fraction=float(1 - valid_aligned.mean()))
    stats['needs_attention'] = bool(stats['invalid_fraction'] > .002 or after is None or after > 2.0)
    if foreground:
        alpha = alpha_maps[chosen]
        if flow_used:
            alpha = cv2.remap(alpha, xx + flow[..., 0], yy + flow[..., 1], cv2.INTER_LINEAR,
                              borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        aligned = np.where(valid_aligned, alpha, 0).astype(np.float32)
        stats['alpha_threshold'] = .5
        stats['foreground_fraction'] = float((aligned >= .5).mean())
        stats['score_scope'] = 'subject interior; background removal is not an alignment error'
        g = rgba[..., 3]
    return aligned, valid_aligned, stats, g


def overlay(base_gray, feature, color=(230, 30, 30)):
    picture = np.repeat(np.uint8(np.clip(base_gray, 0, 1) * 255)[..., None], 3, axis=2)
    picture[feature > .25] = color
    return Image.fromarray(picture)


def sheet(panels, path, columns=3):
    from .post_review import sheet as post_sheet
    return post_sheet(panels, path, columns)


def region_overview(rgb, labels, path, names=None):
    original = image_u8(rgb)
    rng = np.random.default_rng(7)
    palette = rng.integers(30, 240, (int(labels.max()) + 1, 3), dtype=np.uint8)
    picture = Image.blend(original, Image.fromarray(palette[labels]), .45)
    scale = min(1.0, 1400 / max(picture.size))
    if scale < 1:
        picture = picture.resize((round(picture.width * scale), round(picture.height * scale)), Image.Resampling.LANCZOS)
    draw = ImageDraw.Draw(picture)
    font = ImageFont.load_default(size=14)
    for n, box in enumerate(ndi.find_objects(labels), 1):
        if box is None:
            continue
        # Deepest interior point, so the label sits inside its own region.
        inside = ndi.distance_transform_edt(np.pad(labels[box] == n, 1))[1:-1, 1:-1]
        y, x = np.unravel_index(int(inside.argmax()), inside.shape)
        y, x = y + box[0].start, x + box[1].start
        text = names.get(int(n), str(n)) if names else str(n)
        draw.text((x * scale, y * scale), text, font=font, fill='white', stroke_width=2, stroke_fill='black', anchor='mm')
    draw.text((6, 6), f'original {rgb.shape[1]}x{rgb.shape[0]} px, shown x{scale:.3f}', font=font, fill='yellow',
              stroke_width=2, stroke_fill='black')
    save_image(picture, path)


def register_manga(job, redraw=None, lineart=None, fit='auto', max_shift=12, min_area=None, region_source='auto', foreground=None):
    """Map generated images to the original grid, then build the part candidates on the color illustration."""
    job, record = manga_job(job)
    if (job / 'tone_transfer_job.json').exists():
        raise ValueError('Tone transfer preserves the supplied lineart; prepare a fresh job to change inputs')
    if fit not in FITS:
        raise ValueError(f'fit must be one of {FITS}')
    if not isinstance(max_shift, int) or not 0 <= max_shift <= 48:
        raise ValueError('max_shift must be 0..48')
    if region_source not in ('auto', 'lineart', 'redraw', 'source_job'):
        raise ValueError('region_source must be auto, lineart, redraw or source_job')
    redraw = redraw or record['generated'].get('redraw', {}).get('path')
    lineart = lineart or (record['generated'].get('lineart') or record['generated'].get('qwen_lineart') or {}).get('path')
    foreground = foreground or record['generated'].get('foreground', {}).get('path')
    rgb, _ = load_rgb(job / 'reference.png')
    h, w = rgb.shape[:2]
    original_gray = tone.gray(rgb)
    input_size = tuple(record['generation_input']['size'])
    stats = {}
    value_source = 'levels'
    L = tone.manga_levels(rgb, **tone.DEFAULT_LEVELS)
    redraw_valid = np.ones((h, w), bool)
    panels = [('Color original', image_u8(rgb))]
    if redraw:
        aligned, redraw_valid, stats['redraw'], raw = register_image(redraw, original_gray, input_size, fit, max_shift, 'redraw')
        stats['redraw']['engine'] = record['engines']['redraw']
        # Outside the generated canvas (cover crop) fall back to Manga Levels of the original.
        L = np.where(redraw_valid, aligned, L).astype(np.float32)
        value_source = 'redraw'
        save_image(image_u8(aligned), job / 'aligned_redraw.png')
        panels += [('Redraw as generated', image_u8(raw)), ('Redraw on original grid', image_u8(aligned)),
                   ('Redraw lines (red) over original', overlay(original_gray, stroke_feature(aligned)))]
    pen = None
    if lineart:
        line_aligned, line_valid, stats['lineart'], raw = register_image(lineart, original_gray, input_size, fit, max_shift, 'lineart')
        stats['lineart']['engine'] = record['engines']['lineart']
        pen = np.where(line_valid, 1 - line_aligned, 0).astype(np.float32)
        save_image(image_u8(1 - pen), job / 'aligned_lineart.png')
        panels += [('Lineart as generated', image_u8(raw)), ('Lineart on original grid', image_u8(1 - pen)),
                   ('Lineart (red) over original', overlay(original_gray, pen))]
    k = w / 1024
    if region_source == 'auto':
        region_source = 'source_job' if (job / 'source_labels.npy').exists() else 'lineart' if pen is not None else 'redraw'
    if region_source == 'source_job':
        if not (job / 'source_labels.npy').exists():
            raise ValueError('No imported source_job parts; pass --source-job to prepare-manga')
        regions = np.load(job / 'source_labels.npy').astype(np.int32)
    else:
        if region_source == 'lineart':
            if pen is None:
                raise ValueError('region_source lineart needs a lineart image')
            guide = pen
        else:
            ink, solid = tone.ink_layer(L, k)
            guide = (ink & ~tone.solid_mass(solid, k)).astype(np.float32)
        min_area = int(min_area if min_area is not None else max(12, round(30 * k * k)))
        if not 1 <= min_area <= 20000:
            raise ValueError('min_area must be 1..20000')
        regions = geometry_labels(guide, min_area)
        if regions.max() > 2500:
            raise ValueError(f'{int(regions.max())} regions; raise --min-area or provide a cleaner lineart')
        stats['region_min_area'] = min_area
    foreground_arrays = {}
    if foreground:
        alpha, _, stats['foreground'], _ = register_image(foreground, original_gray, input_size, fit, max_shift, 'foreground')
        subject = alpha >= .5
        if not subject.any() or subject.all():
            raise ValueError('Aligned foreground must leave both subject and background pixels')
        background_region = int(regions.max()) + 1
        regions = np.where(subject, regions, background_region).astype(np.int32)
        foreground_arrays = dict(foreground_alpha=alpha, foreground_mask=subject)
        stats['foreground']['background_region'] = background_region
        record['generated']['foreground'] = dict(engine='imagegen', path=str(Path(foreground).resolve()), hash=digest(foreground))
        write_json(job / 'manga_job.json', record)
        save_image(Image.fromarray(np.uint8(subject) * 255), job / 'foreground_mask.png')
        front = np.where(subject[..., None], rgb, .85)
        back = np.where(subject[..., None], .85, rgb)
        with Image.open(foreground) as cutout:
            shown = Image.alpha_composite(Image.new('RGBA', cutout.size, '#b8c4cf'), cutout.convert('RGBA')).convert('RGB')
        sheet([('Original', image_u8(rgb)), ('Imagegen cutout as generated', shown),
               ('Aligned subject from ORIGINAL pixels', image_u8(front)), ('Background from ORIGINAL pixels', image_u8(back))],
              job / 'foreground_alignment.png', columns=2)
    np.savez_compressed(job / 'manga_arrays.npz', rgb=rgb, L=L, pen=pen if pen is not None else np.zeros((h, w), np.float32),
                        has_pen=np.bool_(pen is not None), redraw_valid=redraw_valid, regions=regions, **foreground_arrays)
    regions_hash = digest(job / 'manga_arrays.npz')
    for old in job.glob('manga_regions_*.png'):
        old.unlink()
    records = region_sheets(rgb, regions, job, 'manga_regions', {int(n): str(int(n)) for n in np.unique(regions)})
    region_overview(rgb, regions, job / 'manga_regions_overview.png')
    write_json(job / 'manga_regions.json', dict(regions=[dict(r, id=r['number']) for r in records], source=region_source,
                                                coordinate_space='original reference pixels'))
    sheet(panels, job / 'manga_alignment.png')
    stats.update(value_source=value_source, region_source=region_source, regions=int(len(records)), arrays_hash=regions_hash)
    write_json(job / 'manga_alignment.json', stats)
    plan_path = job / 'semantic_plan.json'
    labels_hash = digest_array(regions)
    if plan_path.exists():
        plan = read_json(plan_path)
        if plan.get('regions_hash') != labels_hash:
            atomic_write(job / 'history' / f'semantic_plan_{digest(plan_path)[:12]}.json', plan_path.read_bytes())
            plan.update(regions_hash=labels_hash, classification_source='partial_review', region_source=region_source)
            if region_source != 'source_job':
                plan['parts'] = [dict(p, regions=[]) for p in plan['parts']]
            write_json(plan_path, plan)
    else:
        parts = read_json(job / 'source_parts.json') if region_source == 'source_job' else []
        write_json(plan_path, dict(schema='manga_plan_v1', classification_source='heuristic', reference_hash=record['config']['reference_hash'],
                                   regions_hash=labels_hash, region_source=region_source, tone_settings=dict(tone.DEFAULT_SETTINGS),
                                   parts=[dict(p, tone=dict(base='auto', shadow='auto', highlight='white', beta=True)) for p in parts]))
    attention = [k_ for k_, v in stats.items() if isinstance(v, dict) and v.get('needs_attention')]
    event(job, 'manga_semantic_review', f'生成画像を元サイズへ換算・位置合わせ。{len(records)}領域の意味割当待ち',
          next_action='位置合わせと全領域を目視し、partsの意味・領域を割当。仕上げ設定は既定を維持（推奨依頼時のみ調整）')
    return dict(job=str(job), value_source=value_source, region_source=region_source, regions=len(records),
                alignment={k_: {key: v[key] for key in ('fit', 'original_px_per_generated_px', 'edge_distance_before_flow_px',
                                                        'edge_distance_after_flow_px', 'needs_attention')} for k_, v in stats.items() if isinstance(v, dict)},
                needs_attention=attention, sheets=sorted(p.name for p in job.glob('manga_regions_*.png')))


def digest_array(array):
    import hashlib
    array = np.ascontiguousarray(array)
    return hashlib.sha256(str((array.shape, array.dtype.str)).encode() + array.tobytes()).hexdigest()


def tone_spec(part, settings):
    spec = {'base': 'auto', 'shadow': 'auto', 'highlight': 'white', 'beta': True, **(part.get('tone') or {})}
    covers = tone.level_covers(settings)
    def coverage(value, key, allow):
        if value in allow:
            return value
        if value in TONE_WORDS:
            return float(covers[TONE_WORDS[value]])
        if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 1:
            return float(value)
        raise ValueError(f'{part["semantic_id"]}.tone.{key} must be one of {sorted(allow | set(TONE_WORDS))} or 0..1')
    unknown = set(spec) - {'base', 'shadow', 'highlight', 'beta', 'notes'}
    if unknown:
        raise ValueError(f'{part["semantic_id"]}.tone has unknown keys {sorted(unknown)}')
    spec['base'] = coverage(spec['base'], 'base', {'auto'})
    spec['shadow'] = coverage(spec['shadow'], 'shadow', {'auto', 'none'})
    if spec['highlight'] not in ('white', 'keep', 'none'):
        raise ValueError(f'{part["semantic_id"]}.tone.highlight must be white, keep or none')
    if spec['beta'] not in (True, False, 'force'):
        raise ValueError(f'{part["semantic_id"]}.tone.beta must be true, false or "force"')
    return spec


def filter_beta_noise(beta, inside, settings, k):
    """Drop black specks: small components, then the whole part when its black is negligible.

    Returns the kept beta and counts of removed pixels (specks, negligible remainder).
    """
    min_area = settings['beta_min_area'] * k * k
    n, labels, stats, _ = cv2.connectedComponentsWithStats(beta.astype(np.uint8), connectivity=8)
    small = stats[:, cv2.CC_STAT_AREA] < min_area
    small[0] = False
    specks = small[labels]
    kept = beta & ~specks
    negligible = 0
    if kept.sum() < settings['beta_min_ratio'] * inside.sum():
        negligible = int(kept.sum())
        kept = np.zeros_like(kept)
    return kept, int(specks.sum()), negligible


def part_labels(plan, regions):
    parts = plan['parts']
    if not parts:
        raise ValueError('semantic_plan.parts is empty')
    ids = [p['semantic_id'] for p in parts]
    names = [p.get('display_name', p['semantic_id']) for p in parts]
    if len(set(ids)) != len(ids) or len(set(names)) != len(names):
        raise ValueError('semantic_id and display_name must be unique')
    labels = np.zeros(regions.shape, np.int32)
    seen = set()
    available = set(int(v) for v in np.unique(regions)) - {0}
    for index, part in enumerate(parts, 1):
        ids_ = [int(r) for r in part.get('regions', [])]
        if seen.intersection(ids_):
            raise ValueError(f'Duplicate region assignment in {part["semantic_id"]}')
        if set(ids_) - available:
            raise ValueError(f'Unknown regions in {part["semantic_id"]}: {sorted(set(ids_) - available)}')
        seen.update(ids_)
        labels[np.isin(regions, ids_)] = index
    if seen != available:
        raise ValueError(f'Unassigned regions: {sorted(available - seen)[:50]}')
    return labels


def part_target(mask, part, page, settings):
    """Target coverage t of one part (meaningful inside mask only) and its beta pixels.

    The dominant tone level of the part becomes its flat base; darker levels are
    shadows (gradation kept), lighter levels highlights. Codex may override each.
    """
    if settings['mode'] == 'posterize':
        return posterized_part_target(mask, page, settings)
    covers = tone.level_covers(settings)
    spec = tone_spec(part, settings)
    level, cover, solid, box = tone.part_tones(page['L'], page['ink'], mask, settings, page['k'])
    inside = mask[box]
    counts = np.bincount(level[inside], minlength=4)
    auto_level = int(counts.argmax())
    base = float(covers[auto_level]) if spec['base'] == 'auto' else spec['base']
    local = np.full(inside.shape, base, np.float32)
    darker = inside & (level > auto_level)
    lighter = inside & (level < auto_level)
    if spec['shadow'] == 'auto':
        local[darker] = np.maximum(cover[darker], base)
    elif spec['shadow'] != 'none':
        local[darker] = max(spec['shadow'], base)
    if spec['highlight'] == 'white':
        local[lighter] = 0
    elif spec['highlight'] == 'keep':
        local[lighter] = np.minimum(cover[lighter], base)
    local_beta = np.zeros(inside.shape, bool)
    specks = negligible = 0
    if spec['beta']:
        local_beta = inside & ((local >= .999) | (level == 3) | solid)
        # A black base is Codex's explicit decision; detected black elsewhere may be redraw noise.
        if spec['beta'] is True and base < .999:
            kept, specks, negligible = filter_beta_noise(local_beta, inside, settings, page['k'])
            dropped = local_beta & ~kept
            local_beta = kept
            if dropped.any():
                # Noise takes the tone of the nearest remaining pixel of the same part.
                source = inside & ~local_beta & ~dropped
                if source.any():
                    nearest = ndi.distance_transform_edt(~source, return_distances=False, return_indices=True)
                    local[dropped] = local[tuple(nearest[:, dropped])]
                local[dropped & (local >= .999)] = settings['dark_tone']
    else:
        if base >= .999:
            base = float(settings['dark_tone'])
        local[inside & (local >= .999)] = settings['dark_tone']
    t = np.full(mask.shape, base, np.float32)
    t[box] = local
    part_beta = np.zeros(mask.shape, bool)
    part_beta[box] = local_beta
    # The tone a removed beta reveals: the part's shadow, or the base when shadows are off.
    if spec['shadow'] == 'none':
        fill = base
    else:
        fill = max(base, settings['dark_tone'] if spec['shadow'] == 'auto' else spec['shadow'])
    if fill >= .999:
        fill = float(settings['dark_tone'])
    return dict(spec=spec, t=t, base=base, auto_level=auto_level, level_counts=counts.tolist(), part_beta=part_beta,
                beta_fill=float(fill), beta_specks_removed=specks, beta_dropped_as_negligible=negligible)


def posterized_part_target(mask, page, settings):
    """Apply the shared brightness stops within each part; semantic tone overrides are not used."""
    k = page['k']
    x0, y0, x1, y1 = bbox_of(mask)
    box = (slice(y0, y1), slice(x0, x1))
    inside = mask[box]
    m = (inside & ~page['strokes'][box]).astype(np.float32)
    if not m.any():
        m = inside.astype(np.float32)
    sigma = max(.5, 2.5 * k)
    weight = cv2.GaussianBlur(m, (0, 0), sigma)
    values = cv2.GaussianBlur(page['L'][box] * m, (0, 0), sigma) / np.maximum(weight, 1e-6)
    # Extend only from samples of this part. Do not pick up an adjacent material.
    valid = inside & (weight > 1e-6)
    if valid.any():
        nearest = ndi.distance_transform_edt(~valid, return_distances=False, return_indices=True)
        values[~valid] = values[tuple(nearest[:, ~valid])]
    cov = tone.posterize_cover(values, settings).astype(np.float32)
    bins = np.round(cov[inside] * 255).astype(np.uint8)
    base = float(np.bincount(bins, minlength=256).argmax() / 255)
    base = min(settings['posterize_densities'], key=lambda v: abs(v - base))
    # Remove small tone islands using the same resolution-relative area setting.
    for value in set(settings['posterize_densities']):
        n, lab, st, _ = cv2.connectedComponentsWithStats((inside & (cov == value)).astype(np.uint8), connectivity=4)
        small = st[:, cv2.CC_STAT_AREA] < settings['min_region'] * k * k
        small[0] = False
        cov[small[lab]] = base
    local_beta = inside & (cov >= .999)
    specks = negligible = 0
    if base < .999:
        kept, specks, negligible = filter_beta_noise(local_beta, inside, settings, k)
        dropped = local_beta & ~kept
        local_beta = kept
        remaining = inside & ~dropped & ~local_beta
        if dropped.any() and remaining.any():
            nearest = ndi.distance_transform_edt(~remaining, return_distances=False, return_indices=True)
            cov[dropped] = cov[tuple(nearest[:, dropped])]
    t = np.full(mask.shape, base, np.float32)
    t[box] = cov
    beta = np.zeros(mask.shape, bool)
    beta[box] = local_beta
    coarse = np.digitize(cov[inside], [.01, .30, .999])
    # Under the beta lies the darkest screen step (the tone a removed beta reveals).
    darkest = max(v for v in settings['posterize_densities'] if v < .999)
    return dict(spec=dict(mode='posterize', base=base, shadow='curve', highlight='curve', beta=True),
                t=t, base=base, auto_level=int(np.argmin(abs(tone.level_covers(settings) - base))),
                level_counts=np.bincount(coarse, minlength=4).tolist(), part_beta=beta,
                beta_fill=float(darkest if base >= .999 else max(base, darkest)),
                beta_specks_removed=specks, beta_dropped_as_negligible=negligible)


def load_manga_inputs(job):
    if (Path(job) / 'tone_transfer_job.json').exists():
        from .tone_transfer import load_inputs
        return load_inputs(job)
    job, record = manga_job(job)
    plan = read_json(job / 'semantic_plan.json')
    if plan.get('classification_source') != 'astra_reviewed':
        raise ValueError('build-manga requires semantic_plan.classification_source = astra_reviewed after visual review')
    if plan.get('reference_hash') != record['config']['reference_hash']:
        raise ValueError('Semantic plan belongs to another reference')
    arrays = dict(np.load(job / 'manga_arrays.npz'))
    if digest_array(arrays['regions']) != plan.get('regions_hash'):
        raise ValueError('Regions changed since the plan was made; review the plan again')
    alignment = read_json(job / 'manga_alignment.json')
    for kind in ('redraw', 'lineart', 'foreground'):
        if kind in alignment and digest(alignment[kind]['path']) != alignment[kind]['hash']:
            raise ValueError(f'{kind} image changed; run register-manga again')
    return job, record, plan, arrays


def render_manga(arrays, plan, layer_sink=None):
    """One renderer for both the Apply preview and the exported PSD. No filesystem writes."""
    settings = tone.validate_settings(plan.get('tone_settings'))
    labels = part_labels(plan, arrays['regions'])
    L = arrays['L']
    h, w = L.shape
    pen = arrays['pen'] if bool(arrays['has_pen']) else None
    page = tone.analyze_page(L, settings, pen)
    transfer = plan.get('pipeline') == 'tone_transfer'
    if transfer:
        # Input ink is fixed. Thresholds/white cuts must not redraw or erase it.
        page['strokes'] = arrays['fixed_ink'].copy()
        page['white_candidates'] = np.zeros((h, w), bool)
        page['transfer_regions'] = arrays['regions']
    k = page['k']
    thresholds = tone.screen_thresholds(h, w, page['pitch'])
    gray_mode = settings['tone_format'] == 'gray'
    strokes = page['strokes']
    beta = np.zeros((h, w), bool)
    layers, solvers = [], []
    canvas = np.ones((h, w), np.float32)  # Python target: 1 paper, 0 ink

    def add(name, role, path, value, alpha):
        """value: 0..1 gray of the layer; alpha: bool/float mask. Composited into the target immediately."""
        alpha = np.asarray(alpha, np.float32)
        if not (alpha > 0).any():
            return
        x0, y0, x1, y1 = bbox_of(alpha > 0)
        value = np.broadcast_to(np.asarray(value, np.float32), (h, w))
        rgba = np.dstack([np.repeat(value[y0:y1, x0:x1, None], 3, axis=2), alpha[y0:y1, x0:x1]])
        file = f'manga_layers/{len(layers):03d}.png'
        if layer_sink is not None:
            layer_sink(file, image_u8(rgba))
        q_value = np.round(value * 255) / 255
        q_alpha = np.round(alpha * 255) / 255
        canvas[:] = canvas * (1 - q_alpha) + q_value * q_alpha
        layers.append(dict(name=name, group=role, group_path=path, image_path=file, bbox=[x0, y0, x1, y1], blend_mode='normal', opacity=255))

    def tone_layer(cov, where):
        """Black dots (or flat gray) for coverage cov over the boolean mask where."""
        if gray_mode:
            return 1 - np.asarray(cov, np.float32), where
        return np.zeros((h, w), np.float32), where & tone.dots(np.broadcast_to(cov, (h, w)), thresholds)

    add('紙', 'Paper', [], 1.0, np.ones((h, w), np.float32))
    white_highlights = []
    for index, part in enumerate(plan['parts'], 1):
        mask = labels == index
        from .tone_transfer import part_target as transfer_target, profile_from_target
        item = transfer_target(mask, part, page, settings) if transfer else part_target(mask, part, page, settings)
        t, base, part_beta, fill = item['t'], item['base'], item['part_beta'], item['beta_fill']
        beta |= part_beta
        title = part.get('display_name', part['semantic_id'])
        default_path = ['背景'] if part['semantic_id'] == 'background' else ['キャラクター']
        # Beta and tones of a part share its folder. The tones continue under the beta,
        # so erasing beta in the PSD reveals the part's tone instead of paper.
        path = ['ベタ・トーン', *part_group_path(dict(part, group_path=part.get('group_path', default_path)))]
        free = mask & ~part_beta
        shadow = free & (t > base + 1e-6)
        light = free & (t < base - 1e-6)
        # An explicitly requested editable white layer exposes paper-white areas
        # of a white-base part without changing its resolved tone or composite.
        if part.get('separate_white_highlights') and base == 0:
            white_area = free & (t == 0) & ~strokes
            if transfer:
                white_area &= np.isin(arrays['regions'], part.get('role_regions', {}).get('highlight', []))
            add(f'{title}_ハイライト', 'Highlights', path, 1.0, white_area)
        editable_beta = part_beta
        if base >= .999:
            # Black base: the flat area is beta; lighter areas become white-out above the beta.
            white_highlights.append((title, light & ~strokes, np.where(light, t, 0).astype(np.float32)))
            if transfer:
                # Transferred white-outs must reveal the material's black base when hidden.
                # Keeping ink/tone beneath the white layer does not change the visible result.
                editable_beta = mask
            if fill > 0:
                add(f'{title}_ベース', 'Base', path, *tone_layer(fill, editable_beta))
        else:
            if base > 0:
                add(f'{title}_ベース', 'Base', path, *tone_layer(base, mask))
            if fill > base + 1e-6:
                shadow = shadow | part_beta
            add(f'{title}_影', 'Shadows', path, *tone_layer(np.where(part_beta, fill, t), shadow))
            if base > 0 and light.any():
                value, alpha = tone_layer(t, light)
                if gray_mode:
                    add(f'{title}_ハイライト', 'Highlights', path, value, alpha)
                else:
                    add(f'{title}_ハイライト', 'Highlights', path, np.where(alpha, 0.0, 1.0), light)
        # One beta layer per part; parts with no black (white only) or only noise get none.
        # Source-dark contours can also be classified as part beta. They are already drawn
        # by Lineart; omit that overlap from the editable fill.
        add(f'{title}_ベタ', 'Beta', path, 0.0, editable_beta & ~strokes)
        solvers.append(dict(semantic_id=part['semantic_id'], display_name=title, base_coverage=round(base, 4),
                            transfer_tone=profile_from_target(item, mask, settings),
                            auto_level=tone.LEVEL_NAMES[item['auto_level']], level_pixels=dict(zip(tone.LEVEL_NAMES, item['level_counts'])),
                            spec={k_: v for k_, v in item['spec'].items() if k_ != 'notes'}, pixels=int(mask.sum()),
                            beta_pixels=int(editable_beta.sum()), beta_fill_coverage=round(fill, 4),
                            beta_specks_removed=item['beta_specks_removed'],
                            beta_dropped_as_negligible=item['beta_dropped_as_negligible'],
                            shadow_pixels=int(shadow.sum()), highlight_pixels=int(light.sum())))
    add('線画', 'Lineart', ['線画'], 0.0, strokes)
    if settings['white_lines']:
        # Black on black is invisible: inside beta the main strokes are cut out in white.
        add('白抜き', 'White', ['ホワイト'], 1.0, tone.beta_interior(beta, k) & tone.white_cut(page['white_candidates'], k))
    for title, where, t in white_highlights:
        if gray_mode:
            add(f'{title}_ハイライト', 'White', ['ホワイト'], 1 - t, where)
        else:
            add(f'{title}_ハイライト', 'White', ['ホワイト'], np.where(tone.dots(t, thresholds), 0.0, 1.0), where)
    groups = count_groups(layers)
    target = np.repeat(canvas[..., None], 3, axis=2)
    manifest = dict(size=[w, h], layers=layers, solvers=solvers, pipeline='manga', hierarchy='parts',
                    pixel_layer_count=len(layers), group_count=groups, total_layer_count=len(layers) + groups,
                    settings=settings, dpi=settings['dpi'])
    return dict(target=target, labels=labels, page=page, manifest=manifest)


def build_manga(job, output=None):
    job, record, plan, arrays = load_manga_inputs(job)
    output = Path(output or record['output']).resolve()
    if output.suffix.lower() != '.psd':
        raise ValueError('Output must have .psd extension')
    if (job / 'tone_transfer_job.json').exists():
        from .tone_transfer import protect_output
        protect_output(job, output)
    record['output'] = str(output)
    write_json(job / 'manga_job.json', record)
    monitor(output, job)
    event(job, 'manga_build', 'パーツ別トーン計画とレイヤー生成中', psd=str(output))
    rendered = render_manga(arrays, plan, lambda file, image: save_image(image, job / file))
    target, labels, page, manifest = (rendered[key] for key in ('target', 'labels', 'page', 'manifest'))
    settings = manifest['settings']
    np.save(job / 'semantic_labels.npy', labels)
    save_image(image_u8(target), job / 'manga_target.png')
    strokes = page['strokes']
    save_image(image_u8(np.repeat((~strokes)[..., None], 3, axis=2).astype(np.float32)), job / 'editing_lineart_white.png')
    thresholds = tone.screen_thresholds(*labels.shape, page['pitch'])
    plain = tone.plain_page(page, thresholds)
    save_image(image_u8(np.repeat((~plain)[..., None], 3, axis=2).astype(np.float32)), job / 'plain_finish.png')
    # semantic_id is ASCII; the bundled default font has no Japanese glyphs for display_name.
    names = {i: p['semantic_id'] for i, p in enumerate(plan['parts'], 1)}
    region_overview(arrays['rgb'], labels, job / 'manga_parts_overview.png', names)
    manifest.update(semantic_plan_hash=digest(job / 'semantic_plan.json'),
                    arrays_hash=digest(job / 'manga_arrays.npz'), target='manga_target.png', target_hash=digest(job / 'manga_target.png'),
                    settings=settings)
    layers, groups = manifest['layers'], manifest['group_count']
    write_json(job / 'layers.json', manifest)
    event(job, 'psd_composition', f'{len(layers)}描画レイヤー（フォルダ{groups}）を保存中')
    export_psd(job, manifest, output)
    manifest.update(psd_hash=digest(output), psd_path=str(output))
    write_json(job / 'layers.json', manifest)
    result = evaluate_manga(job, manifest, page, plan)
    from .tone_transfer import export_tone_reference
    reference_result = export_tone_reference(job, manifest, plan)
    if output.parent != job:
        for name in ('manga_target.png', 'plain_finish.png', 'manga_parts_overview.png', 'manga_alignment.png', 'semantic_plan.json',
                     'analysis.json', 'diff.png', 'editing_lineart_white.png'):
            atomic_write(output.parent / name, (job / name).read_bytes())
    event(job, 'post_review_required' if result['passed'] else 'needs_repair', 'PSD保存・読み戻し済み。事後レビューを実施',
          pixel_layers=len(layers), total_layers=len(layers) + groups,
          next_action='post-review --job で比較画像を生成し、全画像を開いてfinish-review')
    return dict(job=str(job), psd=str(output), tone_reference=reference_result['reference'], passed=result['passed'], readback=result['readback'], warnings=result['warnings'], pixel_layers=len(layers),
                groups=groups, parts=len(plan['parts']), analysis=str(job / 'analysis.json'))


def evaluate_manga(job, manifest, page, plan):
    psd = PSDImage.open(manifest['psd_path'])
    rendered = render_generated_psd(psd)
    backing = Image.new('RGBA', rendered.size, 'white')
    actual = np.asarray(Image.alpha_composite(backing, rendered).convert('RGB'), np.float32)
    save_image(Image.fromarray(actual.astype(np.uint8)), job / 'reconstructed.png')
    target = np.asarray(Image.open(job / 'manga_target.png').convert('RGB'), np.float32)
    error = np.abs(actual - target)
    save_image(Image.fromarray(np.uint8(np.clip(error * 4, 0, 255))), job / 'diff.png')
    readback = dict(mae=float(error.mean()), max_channel_error=float(error.max()))
    readback['passed'] = readback['mae'] <= .5 and readback['max_channel_error'] <= 3
    ink = target[..., 0] < 128
    coverage = {name: float((page['level'] == i).mean()) for i, name in enumerate(tone.LEVEL_NAMES)}
    warnings = []
    if manifest['settings']['tone_format'] == 'dots' and page['pitch'] < 5:
        advice = (f"Use lpi <= {manifest['settings']['dpi'] / 5:g} or increase dpi."
                  if manifest['settings']['mode'] == 'posterize' else
                  f"Use tone_settings.screen_lines <= {int(manifest['size'][0] / 5)} or a larger image.")
        warnings.append(f"Dot pitch {page['pitch']:.2f}px is under 5px; light tones may vanish. {advice}")
    result = dict(evaluated=True, pipeline='manga', passed=bool(readback['passed']), readback=readback, psd_hash=manifest['psd_hash'],
                  semantic_plan_hash=manifest['semantic_plan_hash'], classification_source=plan['classification_source'],
                  ink_fraction=float(ink.mean()), level_fraction=coverage, parts=manifest['solvers'],
                  layer_counts=dict(pixel=manifest['pixel_layer_count'], groups=manifest['group_count'], total=manifest['total_layer_count']),
                  screen=dict(screen_lines=manifest['size'][0] / page['pitch'], pitch_px=page['pitch'], angle_deg=tone.ANGLE,
                              mode=manifest['settings']['mode'], dpi=manifest['settings']['dpi'],
                              lpi=(manifest['settings']['dpi'] / page['pitch'] if manifest['settings']['dpi'] else None)),
                  warnings=warnings,
                  alignment=read_json(job / 'manga_alignment.json'), semantic_quality='requires_visual_review')
    write_json(job / 'analysis.json', result)
    return result


def checked_manifest(job):
    if (Path(job) / 'tone_transfer_job.json').exists():
        from .tone_transfer import load_inputs
        load_inputs(job)
    manifest = read_json(job / 'layers.json')
    if manifest.get('pipeline') != 'manga':
        raise ValueError('Not a manga build')
    if digest(manifest['psd_path']) != manifest['psd_hash'] or digest(job / 'semantic_plan.json') != manifest['semantic_plan_hash']:
        raise ValueError('PSD or semantic plan changed; run build-manga again')
    return manifest


def post_review(job):
    from contextlib import ExitStack
    from unittest.mock import patch
    job = Path(job).resolve()
    manifest = checked_manifest(job)
    psd = PSDImage.open(manifest['psd_path'])
    items = {item['name']: item for item in manifest['layers']}
    pixels = [layer for layer in psd.descendants() if not layer.is_group()]
    if len(items) != len(pixels) or any(layer.name not in items for layer in pixels):
        raise ValueError('Post-review requires unique layer names matching the saved PSD')
    folder = job / 'post_review'
    folder.mkdir(exist_ok=True)
    files = []
    part_colors = {}
    for index, solver in enumerate(manifest['solvers'], 1):
        part_colors[solver['display_name']] = np.uint8(np.round(hsv2rgb(np.array([[[index * .61803398875 % 1, .85, .9]]]))[0, 0] * 255))

    def tinted(layer, color):
        rgba = np.array(layer.topil().convert('RGBA'))
        dark = rgba[..., :3].mean(axis=2) < 128
        rgba[dark, :3] = color
        return Image.fromarray(rgba)

    def render(name, only=None, hide=(), tint=False, background='white', owners=None):
        with ExitStack() as stack:
            for layer in pixels:
                role = items[layer.name]['group']
                layer.visible = role not in hide and (only is None or role in only)
                if owners is not None:
                    layer.visible = layer.visible and layer.name.rsplit('_', 1)[0] in owners
                if tint and role in ('Base', 'Shadows', 'Highlights', 'Beta'):
                    owner = layer.name.rsplit('_', 1)[0]
                    stack.enter_context(patch.object(layer, 'topil', return_value=tinted(layer, part_colors.get(owner, (255, 0, 0)))))
            image = render_generated_psd(psd)
        picture = Image.alpha_composite(Image.new('RGBA', image.size, background), image).convert('RGB')
        path = folder / f'{name}.png'
        save_image(picture, path)
        files.append(path)
        return picture

    reference = Image.open(job / 'reference.png').convert('RGB')
    transfer = (job / 'tone_transfer_job.json').exists()
    panels = [('Input line art' if transfer else 'Color original', reference)]
    if transfer and (job / 'source_appearance.png').exists():
        panels.append(('Source tone PSD (different geometry)', Image.open(job / 'source_appearance.png').convert('RGB')))
        files.append(job / 'source_appearance.png')
    if (job / 'aligned_redraw.png').exists():
        panels.append(('Redraw on original grid', Image.open(job / 'aligned_redraw.png').convert('RGB')))
    panels += [('Plain Manga Finish (no parts)', Image.open(job / 'plain_finish.png').convert('RGB')),
               ('Saved PSD', render('current')),
               ('Lineart alone', render('lineart', only=('Lineart',))),
               ('Beta alone', render('beta', only=('Beta',))),
               ('Beta by part (tinted)', render('beta_by_part', only=('Beta',), tint=True)),
               ('White alone (on gray)', render('white', only=('White',), background='#7a8290')),
               ('Tones alone', render('tones', only=('Paper', 'Base', 'Shadows', 'Highlights'))),
               ('Tones by part (tinted)', render('tones_by_part', only=('Paper', 'Base', 'Shadows', 'Highlights'), tint=True)),
               ('Shadows OFF', render('no_shadow', hide=('Shadows',))),
               ('Highlights/White OFF', render('no_highlight', hide=('Highlights', 'White'))),
               ('Base tones OFF', render('no_base', hide=('Base',)))]
    plan = read_json(job / 'semantic_plan.json')
    background_names = {p.get('display_name', p['semantic_id']) for p in plan['parts'] if p['semantic_id'] == 'background'}
    if background_names:
        foreground_names = {p.get('display_name', p['semantic_id']) for p in plan['parts']} - background_names
        panels += [('Background layers alone', render('background_only', owners=background_names, background='#c5c5c5')),
                   ('Subject layers alone', render('foreground_only', owners=foreground_names, background='#c5c5c5'))]
    sheet(panels, folder / 'overview.png')
    files.append(folder / 'overview.png')
    # Full-size crop to judge dots: the central quarter of the page at 1:1.
    current = Image.open(folder / 'current.png')
    cw, ch = current.size
    crop = current.crop((cw // 4, ch // 4, cw // 4 + min(cw // 2, 600), ch // 4 + min(ch // 2, 600)))
    save_image(crop, folder / 'screen_crop_1to1.png')
    files.append(folder / 'screen_crop_1to1.png')
    files += [job / name for name in ('manga_alignment.png', 'manga_parts_overview.png', 'diff.png',
                                      'foreground_alignment.png', 'foreground_mask.png') if (job / name).exists()]
    context = dict(psd_hash=manifest['psd_hash'], semantic_plan_hash=manifest['semantic_plan_hash'],
                   evidence_hashes={path.relative_to(job).as_posix(): digest(path) for path in files})
    write_json(job / 'post_review_context.json', context)
    assessment = job / 'post_review_assessment.json'
    if assessment.exists():
        atomic_write(job / 'history' / f'post_review_{digest(assessment)[:12]}.json', assessment.read_bytes())
    write_json(assessment, dict(context=context, checks={key: dict(status='pending', notes='', evidence=[]) for key in CHECKS}))
    md = ['# Astra事後チェック（漫画トーン化・未確認）', '', f'- PSD: `{manifest["psd_path"]}`',
          '- 手順: docs/MANGA_WORKFLOW.md。画像を実際に開き、各項目へ根拠と結果を記録する。', '']
    md += [f'- [ ] {key}: {label}' for key, label in CHECKS.items()]
    md += ['', f'記入先: `{assessment}`', f'比較画像: `{folder}`', '']
    atomic_write(storage.DOCS / 'jobs' / f'{job.name}-post-review.md', '\n'.join(md))
    event(job, 'post_review_required', '線画/ベタ/ホワイト/トーン単独とパーツ別表示を生成。Astraの確認待ち',
          next_action='MANGA_WORKFLOW.mdに従い全画像を開きassessment記入後finish-review')
    return dict(job=str(job), assessment=str(assessment), overview=str(folder / 'overview.png'), reviewed=False)


def finish_review(job, assessment=None):
    job = Path(job).resolve()
    manifest = checked_manifest(job)
    context = read_json(job / 'post_review_context.json')
    record = read_json(assessment or job / 'post_review_assessment.json')
    if record.get('context') != context or any(context[key] != manifest[key] for key in ('psd_hash', 'semantic_plan_hash')):
        raise ValueError('Review context is stale; run post-review again')
    for path, expected in context['evidence_hashes'].items():
        if digest(job / path) != expected:
            raise ValueError('Review evidence changed; run post-review again')
    checks = record.get('checks', {})
    if set(checks) != set(CHECKS):
        raise ValueError('Every checklist item is required')
    for key, check in checks.items():
        if check.get('status') not in ('pass', 'limitation', 'fail') or not str(check.get('notes', '')).strip():
            raise ValueError(f'Unreviewed checklist item: {key}')
        evidence = check.get('evidence', [])
        if not evidence or any(path not in context['evidence_hashes'] for path in evidence):
            raise ValueError(f'Valid visual evidence required: {key}')
    analysis = read_json(job / 'analysis.json')
    if analysis.get('psd_hash') != manifest['psd_hash']:
        raise ValueError('Evaluation does not match saved PSD')
    failed = [key for key, v in checks.items() if v['status'] == 'fail']
    limits = [key for key, v in checks.items() if v['status'] == 'limitation']
    result = dict(reviewed=True, passed=bool(analysis['passed'] and not failed), failed_checks=failed, limitations=limits,
                  psd_hash=manifest['psd_hash'], assessment=record)
    write_json(job / 'post_review_result.json', result)
    phase = 'complete_with_limitations' if result['passed'] and limits else 'complete' if result['passed'] else 'needs_repair'
    md = ['# Astra事後チェック結果（漫画トーン化）', '', f'判定: {phase}', f'PSD: `{manifest["psd_path"]}`', '']
    for key, check in checks.items():
        md += [f'- [{"x" if check["status"] != "fail" else " "}] {CHECKS[key]} — {check["status"]}',
               f'  - {check["notes"]}', f'  - 根拠: {", ".join(check["evidence"])}']
    atomic_write(storage.DOCS / 'jobs' / f'{job.name}-post-review.md', '\n'.join(md) + '\n')
    destination = Path(manifest['psd_path']).parent
    if destination != job:
        atomic_write(destination / 'post_review_result.json', (job / 'post_review_result.json').read_bytes())
        atomic_write(destination / 'post_review.md', '\n'.join(md) + '\n')
    event(job, phase, 'Astra事後レビューを記録', next_action='意味・領域の誤りは局所修正して再確認。仕上げ設定は明示指定・推奨依頼がある場合だけ変更',
          post_review_passed=result['passed'], post_review_limitations=limits)
    return dict(job=str(job), passed=result['passed'], limitations=limits, failed_checks=failed)
