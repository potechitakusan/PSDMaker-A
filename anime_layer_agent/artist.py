"""Artist-oriented palettes, lighting, layer budgets and inspection artifacts."""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage as ndi
from skimage.color import rgb2lab

from .storage import read_json, write_json, save_image
from .regions import bbox_of

LIGHTING = {
    'neutral': ([.3, .3, .3], [.7, .7, .7]),
    'cool': ([.27, .31, .40], [.62, .70, .80]),
    'warm': ([.40, .31, .24], [.80, .70, .58]),
}


def configure_editing(job, layer_range='auto', color_tolerance='auto', lighting='neutral', lineart='monochrome', detail_mode='relative'):
    job = Path(job).resolve()
    plan = read_json(job / 'semantic_plan.json')
    requested = 'auto'
    if layer_range != 'auto':
        try:
            requested = [int(value) for value in layer_range.split('-')]
        except (ValueError, AttributeError):
            raise ValueError('layer-range must be auto or MIN-MAX') from None
        if len(requested) != 2 or not 1 <= requested[0] <= requested[1] <= 2000:
            raise ValueError('layer-range must satisfy 1 <= MIN <= MAX <= 2000')
    tolerance = 'auto' if color_tolerance == 'auto' else float(color_tolerance)
    if tolerance != 'auto' and (not np.isfinite(tolerance) or not 0 <= tolerance <= 100):
        raise ValueError('color-tolerance must be auto or 0..100')
    if lighting not in LIGHTING or lineart not in ('monochrome', 'source'):
        raise ValueError('Unknown lighting or lineart mode')
    if detail_mode not in ('relative', 'pigment'):
        raise ValueError('detail_mode must be relative or pigment')
    plan['editing'] = dict(profile='artist', layer_range=requested, color_tolerance=tolerance,
                           lighting=lighting, lineart=lineart, detail_mode=detail_mode, line_cleanup=True)
    # Artist budget supersedes the legacy total-with-folders upper bound.
    write_json(job / 'semantic_plan.json', plan)
    return {'job': str(job), 'editing': plan['editing'],
            'next_action': 'Assign group_path and palette_id to reviewed parts, then build-compact'}


def dominant_color(target, mask, alpha):
    sample = target[mask & (alpha < .12)]
    if not len(sample):
        sample = target[mask]
    if not len(sample):
        raise ValueError('Cannot estimate color of an empty semantic part')
    sample = sample[::max(1, len(sample) // 20000)]
    bins = np.round(rgb2lab(sample.reshape(-1, 1, 3)).reshape(-1, 3) / 8).astype(int)
    _, inverse, counts = np.unique(bins, axis=0, return_inverse=True, return_counts=True)
    return np.median(sample[inverse == counts.argmax()], axis=0)


def resolve_settings(plan, labels, target, alpha):
    editing = plan['editing']
    if editing.get('profile') != 'artist' or editing.get('lighting', 'neutral') not in LIGHTING:
        raise ValueError('Invalid artist profile or lighting')
    if editing.get('lineart', 'monochrome') not in ('monochrome', 'source'):
        raise ValueError('Invalid lineart mode')
    parts = plan['parts']
    colors = {p['semantic_id']: dominant_color(target, labels == i, alpha)
              for i, p in enumerate(parts, 1) if p['semantic_id'] != 'background'}
    palette_groups = {}
    for p in parts:
        if p['semantic_id'] == 'background':
            continue
        key = p.get('palette_id', p['semantic_id'])
        if not isinstance(key, str) or not key:
            raise ValueError('palette_id must be a nonempty string')
        palette_groups.setdefault(key, []).append(p['semantic_id'])
    shared, palette_report, differences = {}, [], []
    for key, members in palette_groups.items():
        # Equal weight per semantic part prevents the large sleeve dominating eyes etc.
        palette = np.clip(np.median([colors[name] for name in members], axis=0), 1/255, 254/255)
        for name in members:
            shared[name] = palette
        original_lab = rgb2lab(np.array([colors[name] for name in members]).reshape(-1, 1, 3))[:, 0]
        drift = np.linalg.norm(original_lab - rgb2lab(palette.reshape(1, 1, 3))[0, 0], axis=1)
        if len(members) > 1:
            differences.extend(drift.tolist())
        palette_report.append({'palette_id': key, 'parts': members,
                               'shared_rgb': np.round(palette * 255).astype(int).tolist(),
                               'original_rgb': {name: np.round(colors[name] * 255).astype(int).tolist() for name in members},
                               'max_base_delta_e76': float(drift.max())})
    tolerance = editing.get('color_tolerance', 'auto')
    if tolerance == 'auto':
        # Conservative 2..8 Delta E76 allowance, informed by reviewed shared materials.
        tolerance = float(np.clip(np.median(differences) * 5 if differences else 15, 10, 40))
    if not isinstance(tolerance, (int, float)) or not np.isfinite(tolerance) or not 0 <= tolerance <= 100:
        raise ValueError('color_tolerance must be auto or a number in 0..100')
    requested = editing.get('layer_range', 'auto')
    if requested == 'auto':
        # Three editable paint roles plus optional color/detail, background, and ink.
        n = len(colors)
        extra = sum(p.get('detail_mode',editing.get('detail_mode','pigment')) == 'relative'
                    for p in parts if p['semantic_id'] != 'background')
        budget = [max(1, 2 * n + 2), max(6, 4 * n + extra + 2)]
    else:
        if not (isinstance(requested, list) and len(requested) == 2 and
                all(isinstance(v, int) and not isinstance(v, bool) for v in requested) and
                1 <= requested[0] <= requested[1] <= 2000):
            raise ValueError('Invalid editing.layer_range')
        budget = requested
    return shared, dict(requested=editing, resolved_layer_range=budget,
                        layer_count_unit='pixel_layers_excluding_folders',
                        layer_range_reason='Reviewed material count; Base/Shadow/Highlight plus relative color correction or intentional pigment',
                        resolved_color_tolerance=round(tolerance, 3), max_paint_delta_e76=tolerance / 5,
                        tolerance_reason='User value or conservative allowance from shared-material base drift',
                        palettes=palette_report)


def decompose_artist(target, base_color, tolerance, lighting='neutral'):
    """Fixed lighting RGB, spatial alpha; bounded change, with explicit color detail.

    The tolerance caps paint RGB changes in CIE76 (0..20). Line monochroming is
    separate. Residual normal color is solved per part, never a flattened cover.
    """
    base = np.broadcast_to(np.clip(base_color, 1/255, 254/255), target.shape).copy()
    shadow, light = [np.broadcast_to(np.array(v, dtype=np.float32), target.shape) for v in LIGHTING[lighting]]
    dark_vector = base * (shadow - 1)
    light_vector = (1 - base) * light
    diff = target - base
    sa = np.clip(np.sum(diff * dark_vector, axis=-1) / np.maximum(np.sum(dark_vector**2, axis=-1), 1e-8), 0, 1)
    ha = np.clip(np.sum(diff * light_vector, axis=-1) / np.maximum(np.sum(light_vector**2, axis=-1), 1e-8), 0, 1)
    dark_fit = base + dark_vector * sa[..., None]
    light_fit = base + light_vector * ha[..., None]
    use_dark = np.sum((target-dark_fit)**2, axis=-1) <= np.sum((target-light_fit)**2, axis=-1)
    sa = np.where(use_dark, sa, 0)
    ha = np.where(use_dark, 0, ha)
    fitted = np.where(use_dark[..., None], dark_fit, light_fit)
    # Bisection caps the change, including gamut/nonlinear Lab effects.
    original_lab = rgb2lab(target)
    low, high = np.zeros(target.shape[:-1]), np.ones(target.shape[:-1])
    cap = tolerance / 5
    if cap == 0:
        changed = target.copy()
    else:
        for _ in range(12):
            mid = (low + high) / 2
            candidate = target + (fitted-target) * mid[..., None]
            accepted = np.linalg.norm(rgb2lab(candidate) - original_lab, axis=-1) <= cap
            low = np.where(accepted, mid, low)
            high = np.where(accepted, high, mid)
        fraction = np.where(np.linalg.norm(rgb2lab(fitted)-original_lab, axis=-1) <= cap, 1, low)
        changed = target + (fitted-target) * fraction[..., None]
    darker = np.maximum(fitted - changed, 0) / np.maximum(fitted, 1e-8)
    lighter = np.maximum(changed - fitted, 0) / np.maximum(1 - fitted, 1e-8)
    detail_alpha = np.clip(np.maximum(darker, lighter).max(axis=-1) * 1.01, 0, 1)
    detail = np.clip((changed - fitted*(1-detail_alpha[..., None])) /
                     np.maximum(detail_alpha[..., None], 1e-8), 0, 1)
    return base, shadow, sa, light, ha, detail, detail_alpha, changed


def relative_color_layers(fitted, target):
    """Correct chroma with base-responsive Multiply/Screen, never opaque Normal.

    Intentional printed motifs/pigments belong to separate reviewed materials.
    No decomposition can guarantee arbitrary recolors preserve original lighting;
    the post-review render is mandatory, including high-alpha correction warnings.
    """
    ratio = np.minimum(target / np.maximum(fitted, 1e-8), 1)
    shadow_alpha = np.clip((1-ratio).max(axis=-1)/.7, 0, 1)
    shadow = 1-(1-ratio)/np.maximum(shadow_alpha[...,None],1e-8)
    shaded = fitted*ratio
    amount = np.clip((target-shaded)/np.maximum(1-shaded,1e-8),0,1)
    light_alpha = np.clip(amount.max(axis=-1)/.7,0,1)
    light = amount/np.maximum(light_alpha[...,None],1e-8)
    return np.clip(shadow,0,1),shadow_alpha,np.clip(light,0,1),light_alpha


def clean_linework(arrays):
    """Reject unsupported reference texture while preserving source recomposition."""
    alpha = arrays['alpha']
    warped = arrays['warped']
    guide_distance = ndi.distance_transform_edt(warped < .12)
    supported = (guide_distance <= 2) & (alpha >= .10)
    components, count = ndi.label(supported)
    sizes = np.bincount(components.ravel(), minlength=count+1)
    keep = sizes >= 6
    keep[0] = False
    # A tiny strong mark on the guide may be an eyelash, punctuation-sized detail.
    supported &= keep[components] | ((warped > .65) & (alpha > .6))
    cleaned = np.where(supported,alpha,0)
    original = arrays['underlying']*(1-alpha[...,None])+arrays['ink']*alpha[...,None]
    underlying = np.where((cleaned == alpha)[...,None], arrays['underlying'], original)
    stats = {'method':'guide_supported_reference_ink', 'removed_pixels':int(np.count_nonzero((alpha>0)&~supported)),
             'remaining_pixels':int(np.count_nonzero(cleaned)),
             'recomposition_max_error':float(np.abs(underlying*(1-cleaned[...,None])+arrays['ink']*cleaned[...,None]-original).max())}
    return underlying, cleaned, stats


def part_group_path(part):
    path = part.get('group_path', ['キャラクター'])
    if not isinstance(path, list) or not path or len(path) > 6 or any(not isinstance(s, str) or not s.strip() for s in path):
        raise ValueError('group_path must be a list of 1..6 nonempty folder names')
    return [*path, f"{part.get('display_name', part['semantic_id'])} [{part['semantic_id']}]"]


def count_groups(layers):
    return len({tuple(layer['group_path'][:depth]) for layer in layers
                for depth in range(1, len(layer['group_path']) + 1)})


def background_audit(rgb, labels, parts, job):
    """Review candidates only: background shadows/props can be legitimate."""
    index = next(i for i, p in enumerate(parts, 1) if p['semantic_id'] == 'background')
    mask = labels == index
    border = np.zeros(mask.shape, bool)
    border[[0, -1], :] = True
    border[:, [0, -1]] = True
    sample = rgb[mask & border]
    if not len(sample):
        sample = rgb[mask]
    lab = rgb2lab(rgb)
    center = rgb2lab(np.median(sample, axis=0).reshape(1, 1, 3))[0, 0]
    suspicious = mask & (np.linalg.norm(lab-center, axis=2) > 18)
    components, n = ndi.label(suspicious)
    records = []
    sizes = np.bincount(components.ravel())
    for number in sorted(range(1, n+1), key=lambda k: -sizes[k])[:24]:
        if sizes[number] < 6:
            continue
        component = components == number
        seed = ndi.maximum_position(ndi.distance_transform_edt(component))
        neighbors = ndi.binary_dilation(component, iterations=3) & ~mask
        counts = np.bincount(labels[neighbors], minlength=len(parts)+1)
        candidates = [parts[k-1]['semantic_id'] for k in np.argsort(-counts) if k and counts[k]][:3]
        records.append(dict(component=number, pixels=int(sizes[number]), bbox=bbox_of(component),
                            seed_xy=[int(seed[1]), int(seed[0])], nearby_parts=candidates))
    checker = np.indices(mask.shape).sum(axis=0) // 12 % 2
    backing = np.where(checker[..., None], .72, .87) * np.ones_like(rgb)
    isolated = np.where(mask[..., None], rgb, backing)
    save_image(Image.fromarray(np.uint8(np.round(isolated*255))), job / 'background_only.png')
    preview = isolated.copy()
    preview[suspicious] = .45 * preview[suspicious] + .55 * np.array([1, .25, 0])
    save_image(Image.fromarray(np.uint8(np.round(preview*255))), job / 'background_review.png')
    result = dict(suspicious_pixels=int(suspicious.sum()), candidates=records,
                  verdict='visual_review_required', note='Color contrast is a proposal, not proof of foreground contamination')
    write_json(job / 'background_review.json', result)
    return result


def write_editing_report(job, settings, layers, audit, paint_delta):
    count = len(layers)
    minimum, maximum = settings['resolved_layer_range']
    settings.update(pixel_layers=count, folders=count_groups(layers), total_items=count+count_groups(layers),
                    layer_range_met=minimum <= count <= maximum,
                    paint_delta_e76_max=float(paint_delta), background_review=audit,
                    limitations=['Hidden anatomy is not reconstructed', 'Background candidates require visual review',
                                 'Color/detail layer may retain motifs and residual chroma',
                                 'Monochrome line changes are measured separately from paint tolerance'])
    write_json(job / 'editing_report.json', settings)
    sheet = Image.new('RGB', (900, max(60, len(settings['palettes'])*46+32)), 'white')
    draw = ImageDraw.Draw(sheet)
    for row, palette in enumerate(settings['palettes']):
        y = 24 + row*46
        draw.rectangle((8, y, 66, y+30), fill=tuple(palette['shared_rgb']))
        draw.text((78, y+8), f"{palette['palette_id']}: {', '.join(palette['parts'])}", fill='black')
    save_image(sheet, job / 'palette_review.png')
    return settings


def review_editing(job):
    """Inspect the current masks and background before authorizing PSD build."""
    from .compact import semantic_masks, region_sheets
    job = Path(job).resolve()
    plan = read_json(job/'semantic_plan.json')
    with np.load(job/'compact_arrays.npz') as arrays:
        labels = semantic_masks(arrays, plan)
        region_sheets(arrays['rgb'], labels, job, 'semantic_review',
                      {i:p['semantic_id'] for i,p in enumerate(plan['parts'],1)})
        audit = background_audit(arrays['rgb'], labels, plan['parts'], job)
        _, settings = resolve_settings(plan, labels, arrays['underlying'], arrays['alpha'])
    write_json(job/'editing_proposal.json', settings)
    return {'job':str(job), 'preferences':settings, 'background_review':audit,
            'next_action':'Inspect semantic_review and background_only; repair assignments before astra_reviewed/build-compact'}
