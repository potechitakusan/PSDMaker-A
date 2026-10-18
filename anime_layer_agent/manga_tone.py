"""Manga tone primitives: line/solid separation, descreened values, flat tone plan, page screen.

Portions of this module are adapted from ComfyUI-MangaScreen
(https://github.com/ashukae/ComfyUI-MangaScreen), Copyright (c) 2026 ashukae,
released under the MIT License. The full notice is in THIRD_PARTY_NOTICES.md.

Changes from the original: no torch/ComfyUI node wrappers; the halftone is rendered
from a per-pixel page threshold so every layer shares one 45-degree screen; the
coverage of each pixel is constant inside that pixel (nearest instead of linear
supersampling), which makes a higher coverage always a superset of a lower one.
"""
import cv2
import copy
import math
import numpy as np
from scipy import ndimage as ndi

ANGLE = 45.0
LEVEL_NAMES = ('white', 'light', 'dark', 'black')
DEFAULT_SETTINGS = dict(screen_lines=200, white_point=.80, black_point=.30, light_tone=.18, dark_tone=.42,
                        gradation=1.0, pen_threshold=.30, white_lines=.4, min_region=600, tone_format='dots',
                        beta_min_area=40, beta_min_ratio=.005, mode='node', dpi=None, lpi=60.0,
                        posterize_thresholds=[50, 100, 150, 200],
                        posterize_densities=[1.0, .50, .30, .15, 0.0])
SETTING_RANGES = dict(screen_lines=(40, 600), white_point=(.3, 1.0), black_point=(0.0, .7), light_tone=(.02, .9),
                      dark_tone=(.02, .95), gradation=(0.0, 1.0), pen_threshold=(.05, .95), white_lines=(0.0, 1.0),
                      min_region=(0, 20000), beta_min_area=(0, 5000), beta_min_ratio=(0.0, .5))
DEFAULT_LEVELS = dict(black=.20, white=.70, gamma=1.3)


def validate_settings(values):
    values = copy.deepcopy(values or {})
    values.pop('posterize_enabled', None)  # Older saves; posterize mode now always steps.
    settings = dict(copy.deepcopy(DEFAULT_SETTINGS), **values)
    unknown = set(settings) - set(DEFAULT_SETTINGS)
    if unknown:
        raise ValueError(f'Unknown tone_settings: {sorted(unknown)}')
    for key, (low, high) in SETTING_RANGES.items():
        if not isinstance(settings[key], (int, float)) or isinstance(settings[key], bool) or not math.isfinite(settings[key]) or not low <= settings[key] <= high:
            raise ValueError(f'tone_settings.{key} must be {low}..{high}')
    if settings['black_point'] >= settings['white_point']:
        raise ValueError('tone_settings.black_point must be below white_point')
    if settings['light_tone'] >= settings['dark_tone']:
        raise ValueError('tone_settings.light_tone must be below dark_tone')
    if settings['tone_format'] not in ('dots', 'gray'):
        raise ValueError('tone_settings.tone_format must be dots or gray')
    if settings['mode'] not in ('node', 'posterize'):
        raise ValueError('mode must be node or posterize')
    for key, low, high in (('dpi', 72, 2400), ('lpi', 5, 200)):
        value = settings[key]
        if key == 'dpi' and value is None and settings['mode'] == 'node':
            continue  # Preserve old jobs that did not declare print resolution.
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f'{key} must be {low}..{high}')
    stops, densities = settings['posterize_thresholds'], settings['posterize_densities']
    if not isinstance(stops, list) or not 1 <= len(stops) <= 7:
        raise ValueError('posterize_thresholds needs 1..7 thresholds')
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 < v < 255 for v in stops):
        raise ValueError('posterize thresholds must be inside 0..255')
    if any(a >= b for a, b in zip(stops, stops[1:])):
        raise ValueError('posterize thresholds must be strictly increasing')
    if not isinstance(densities, list) or len(densities) != len(stops) + 1:
        raise ValueError('posterize_densities must have one value per interval')
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 1 for v in densities):
        raise ValueError('posterize densities must be 0..1')
    if any(a < b for a, b in zip(densities, densities[1:])):
        raise ValueError('posterize densities must decrease from dark to light')
    if settings['mode'] == 'posterize':
        # The darkest step is beta and the lightest is paper; only the steps between are tones.
        if densities[0] != 1 or densities[-1] != 0:
            raise ValueError('posterize densities must start at 1 (beta) and end at 0 (white)')
        if settings['tone_format'] != 'dots':
            raise ValueError('posterize mode outputs dots only')
    return settings


def posterize_cover(values, settings):
    """Brightness 0..1 -> density; equality belongs to the brighter interval."""
    bins = np.searchsorted(np.asarray(settings['posterize_thresholds']) / 255,
                           np.clip(values, 0, 1), side='right')
    return np.asarray(settings['posterize_densities'], np.float32)[bins]


def level_covers(settings):
    return np.float32((0.0, settings['light_tone'], settings['dark_tone'], 1.0))


def gray(rgb):
    """BT.601 luma, matching cv2.COLOR_RGB2GRAY used by the reference node."""
    return (np.asarray(rgb, np.float32)[..., :3] @ np.float32((.299, .587, .114))).astype(np.float32)


def manga_levels(rgb, black=.20, white=.70, gamma=1.3):
    """Grayscale + levels (Manga Levels): the light path without an edit model."""
    g = np.clip((gray(rgb) - black) / max(white - black, 1e-3), 0, 1)
    return (g ** (1 / gamma)).astype(np.float32)


def _median(image, size):
    size = int(size) | 1
    return cv2.medianBlur(image, size) if size >= 3 else image


def screen_thresholds(h, w, pitch, supersample=3):
    """Per-pixel threshold of one page-aligned 45-degree AM screen.

    The reference draws dots 3x supersampled and keeps a pixel when at least 5 of
    its 9 subsamples are inside the dot. That equals coverage > the 5th smallest
    spot value of the pixel, so one threshold map serves every layer.
    """
    s = supersample
    a = np.deg2rad(ANGLE)
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    samples = []
    for dy in range(s):
        for dx in range(s):
            x, y = xs * s + dx, ys * s + dy
            u = (x * np.cos(a) + y * np.sin(a)) / (pitch * s)
            v = (-x * np.sin(a) + y * np.cos(a)) / (pitch * s)
            samples.append((1 - (np.cos(2 * np.pi * u) + np.cos(2 * np.pi * v) + 2) / 4).astype(np.float32))
    stack = np.stack(samples, axis=-1)
    keep = s * s // 2  # 5th smallest of 9: majority of the area average
    return np.partition(stack, keep, axis=-1)[..., keep]


def dots(cover, thresholds):
    return np.asarray(cover, np.float32) > thresholds


def ink_layer(L, k):
    """Lines and solid blacks as a crisp mask. k is the image scale relative to 1024 px width."""
    solid = cv2.GaussianBlur(L, (0, 0), 1.5 * k) < 0.22
    Lb = cv2.GaussianBlur(L, (0, 0), 0.7 * k)
    line = (Lb < cv2.blur(Lb, (int(15 * k) | 1,) * 2) - 0.07) & (Lb < 0.65)
    _, lab, st, _ = cv2.connectedComponentsWithStats(line.astype(np.uint8), connectivity=8)
    w, h, area = st[:, cv2.CC_STAT_WIDTH], st[:, cv2.CC_STAT_HEIGHT], st[:, cv2.CC_STAT_AREA]
    # a stroke is long and thin; a dot, or a clump of generated dots, is compact
    keep = (np.maximum(w, h) >= 10 * k) & (area >= 12 * k * k) & (area < 0.6 * w * h + 40 * k * k)
    keep |= area >= 400 * k * k
    keep[0] = False
    ink = solid | keep[lab]
    _, lab, st, _ = cv2.connectedComponentsWithStats(ink.astype(np.uint8), connectivity=8)
    big = st[:, cv2.CC_STAT_AREA] >= 6 * k * k
    big[0] = False
    return big[lab], solid


def solid_mass(solid, k):
    """Solid black areas wider than a stroke. Thick lines also pass the solid test of
    the page, but in a layered file they belong to the lineart, not the beta."""
    size = max(3, round(7 * k)) | 1
    return cv2.morphologyEx(solid.astype(np.uint8), cv2.MORPH_OPEN,
                            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))) > 0


def value_map(L, ink, k):
    """Descreened value with the ink masked out (normalized convolution), then edge-preserving flattening."""
    m = (~ink).astype(np.float32)
    s = 2.5 * k
    V = cv2.GaussianBlur(L * m, (0, 0), s) / np.maximum(cv2.GaussianBlur(m, (0, 0), s), 1e-3)
    for _ in range(2):
        V = cv2.bilateralFilter(V.astype(np.float32), int(9 * k) | 1, 0.10, 9 * k)
    return V.astype(np.float32)


def tone_plan(V, k, white, split, black, min_region=600):
    """0 white, 1 light tone, 2 dark tone, 3 black; small regions melt into their surroundings."""
    level = _median(np.digitize(-V, [-white, -split, -black]).astype(np.uint8), 11 * k)
    for lv in (1, 2, 3, 0):
        _, lab, st, _ = cv2.connectedComponentsWithStats((level == lv).astype(np.uint8), connectivity=4)
        small = st[:, cv2.CC_STAT_AREA] < min_region * k * k
        small[0] = False
        hole = small[lab]
        if hole.any():
            fill = _median(np.where(hole, 255, level).astype(np.uint8), 21 * k)
            level = np.where(hole & (fill != 255), fill, level).astype(np.uint8)
    return level


def region_spread(level, V):
    """Standard deviation of V inside each flat region: a region hiding a gradient spreads wide."""
    out = np.zeros_like(V)
    for lv in range(4):
        n, lab = cv2.connectedComponents((level == lv).astype(np.uint8), connectivity=4)
        cnt = np.maximum(np.bincount(lab.ravel(), minlength=n), 1)
        mean = np.bincount(lab.ravel(), V.ravel(), minlength=n) / cnt
        std = np.sqrt(np.maximum(np.bincount(lab.ravel(), (V * V).ravel(), minlength=n) / cnt - mean * mean, 0))
        out = np.where(level == lv, std[lab], out)
    return out.astype(np.float32)


def white_cut(candidates, k):
    """Long strokes only, thinned: the folds and seams worth keeping white inside beta."""
    _, lab, st, _ = cv2.connectedComponentsWithStats(candidates.astype(np.uint8), connectivity=8)
    keep = np.maximum(st[:, cv2.CC_STAT_WIDTH], st[:, cv2.CC_STAT_HEIGHT]) >= 40 * k
    keep[0] = False
    return cv2.erode(keep[lab].astype(np.uint8), np.ones((2 if k < 1.6 else 3,) * 2, np.uint8)) > 0


def analyze_page(L, settings, pen=None):
    """Page-wide intermediates shared by the plain page and the per-part layers.

    L: 0..1 luminance on the reference grid. pen: optional 0..1 line strength.
    """
    settings = validate_settings(settings)
    L = np.asarray(L, np.float32)
    k = L.shape[1] / 1024
    white, black = settings['white_point'], settings['black_point']
    ink, solid = ink_layer(L, k)
    mass = solid_mass(solid, k)
    strokes = ink & ~mass
    if pen is not None:
        strokes |= pen > settings['pen_threshold']
    V = value_map(L, ink, k)
    level = tone_plan(V, k, white, (white + black) / 2, black, settings['min_region'])
    cover = np.take(level_covers(settings), level)
    if settings['gradation']:
        Vs = cv2.GaussianBlur(V, (0, 0), 3 * k)
        a = cv2.GaussianBlur(np.clip((region_spread(level, Vs) - 0.03) / 0.05, 0, 1) * settings['gradation'], (0, 0), 4 * k)
        cover = cover * (1 - a) + np.clip((white - Vs) / (white - black), 0, 1) * a
    candidates = None
    if settings['white_lines']:
        candidates = strokes if pen is None else pen > 1 - settings['white_lines']
    return dict(k=k, L=L, ink=ink, solid=solid, mass=mass, strokes=strokes, V=V, level=level, cover=cover.astype(np.float32),
                white_candidates=candidates,
                pitch=(settings['dpi'] / settings['lpi'] if settings['mode'] == 'posterize'
                       else L.shape[1] / settings['screen_lines']), settings=settings)


def part_tones(L, ink, mask, settings, k):
    """Level and coverage of one semantic part from its own pixels only.

    Same steps as the page (masked descreen, bilateral flattening, four levels,
    small-region merge, gradation), but the normalized convolution is limited to
    the part so a black neighbor cannot leave a dark halo inside it.
    Returns (level, cover, box) for the crop box of the part.
    """
    s = settings
    pad = int(np.ceil(12 * k)) + 4
    ys, xs = np.nonzero(mask)
    y0, y1 = max(0, ys.min() - pad), min(mask.shape[0], ys.max() + pad + 1)
    x0, x1 = max(0, xs.min() - pad), min(mask.shape[1], xs.max() + pad + 1)
    box = (slice(y0, y1), slice(x0, x1))
    inside = mask[box]
    # Part edges come from the lineart, fills from the redraw; they can disagree by a
    # pixel or two. Read values in the core and extend them across that edge band.
    band = max(2, round(3 * k))
    core = cv2.erode(inside.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * band + 1,) * 2)) > 0
    if core.sum() < 16:
        core = inside
    m = (core & ~ink[box]).astype(np.float32)
    if not m.any():
        m = core.astype(np.float32)
    lum = L[box]
    sigma = 2.5 * k
    V = cv2.GaussianBlur(lum * m, (0, 0), sigma) / np.maximum(cv2.GaussianBlur(m, (0, 0), sigma), 1e-3)
    V = np.where(cv2.GaussianBlur(m, (0, 0), sigma) > 1e-3, V, np.median(lum[inside])).astype(np.float32)
    for _ in range(2):
        V = cv2.bilateralFilter(V, int(9 * k) | 1, 0.10, 9 * k)
    white, black = s['white_point'], s['black_point']
    level = _median(np.digitize(-V, [-white, -(white + black) / 2, -black]).astype(np.uint8), 11 * k)
    counts = np.bincount(level[core], minlength=4)
    dominant = int(counts.argmax())
    for lv in range(4):
        n, lab, st, _ = cv2.connectedComponentsWithStats((inside & (level == lv)).astype(np.uint8), connectivity=4)
        small = st[:, cv2.CC_STAT_AREA] < s['min_region'] * k * k
        small[0] = False
        level[small[lab]] = dominant
    # Slivers no wider than the edge band are misregistration along outlines, not shading.
    for lv in range(4):
        if lv == dominant:
            continue
        region = inside & (level == lv)
        if not region.any():
            continue
        lab, n = ndi.label(region)
        width = ndi.maximum(ndi.distance_transform_edt(region), lab, np.arange(1, n + 1))
        sliver = np.zeros(n + 1, bool)
        sliver[1:] = np.asarray(width) <= band + 1
        level[sliver[lab]] = dominant
    level = np.where(inside, level, dominant).astype(np.uint8)
    cover = np.take(level_covers(s), level)
    if s['gradation']:
        Vs = cv2.GaussianBlur(V, (0, 0), 3 * k)
        a = cv2.GaussianBlur(np.clip((region_spread(level, Vs) - 0.03) / 0.05, 0, 1) * s['gradation'], (0, 0), 4 * k)
        cover = cover * (1 - a) + np.clip((white - Vs) / (white - black), 0, 1) * a
    edge = inside & ~core
    if edge.any():
        nearest = ndi.distance_transform_edt(~core, return_distances=False, return_indices=True)
        source = tuple(nearest[:, edge])
        level[edge], cover[edge] = level[source], cover[source]
    # Solid black masses, measured inside the part as well.
    full = inside.astype(np.float32)
    solid = inside & (cv2.GaussianBlur(lum * full, (0, 0), 1.5 * k) / np.maximum(cv2.GaussianBlur(full, (0, 0), 1.5 * k), 1e-3) < 0.22)
    return level, cover.astype(np.float32), solid_mass(solid, k), box


def beta_interior(beta, k):
    return cv2.erode(beta.astype(np.uint8), np.ones((int(3 * k) | 1,) * 2, np.uint8)) > 0


def plain_page(page, thresholds):
    """The reference Manga Finish page (no semantic parts): True = ink."""
    s, k = page['settings'], page['k']
    if s['mode'] == 'posterize':
        return dots(posterize_cover(page['V'], s), thresholds) | page['strokes']
    out = dots(page['cover'], thresholds) | page['ink'] | page['strokes']
    if s['white_lines']:
        beta = beta_interior(page['level'] == 3, k)
        out = (out | beta) & ~(beta & white_cut(page['white_candidates'], k))
    return out
