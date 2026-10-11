"""Optional screen-space normal lighting; no torch dependency in the PSD CLI."""
from pathlib import Path

import numpy as np
from PIL import Image

from .storage import digest, write_json, save_image


def validate_normals(normals, shape):
    normals = np.asarray(normals, dtype=np.float32)
    if normals.shape != (*shape, 3) or not np.isfinite(normals).all():
        raise ValueError('Normals must be finite HxWx3 matching the fixed lineart')
    lengths = np.linalg.norm(normals, axis=-1, keepdims=True)
    if np.any(lengths < .1) or np.any(np.abs(normals) > 1.01):
        raise ValueError('Expected signed [-1,1] normal vectors, not RGB visualization')
    return normals / lengths


def normal_dot(normals, light):
    """X right, Y up, Z toward viewer. Artistic diffuse ramps, no cast shadows."""
    light = np.asarray(light, dtype=np.float32)
    if light.shape != (3,) or not np.isfinite(light).all() or np.linalg.norm(light) < 1e-6:
        raise ValueError('Light must be a finite nonzero XYZ vector')
    light = light / np.linalg.norm(light)
    return np.clip(normals @ light, 0, 1)


def normal_alphas(normals, light, strength=1, curve=None):
    dot = normal_dot(normals, light)
    if curve:
        values = [curve.get(k, default) for k, default in
                  [('shadow_gain', 1), ('highlight_gain', 1), ('gamma', 1)]]
        if not all(isinstance(v, (int, float)) and np.isfinite(v) for v in values):
            raise ValueError('Normal curve values must be finite numbers')
        sg, hg, gamma = values
        if not (0 <= sg <= 4 and 0 <= hg <= 4 and .25 <= gamma <= 4):
            raise ValueError('Normal curve gains must be 0..4 and gamma .25..4')
        return (np.clip(np.maximum((.65-dot)/.65, 0)**gamma*.7*sg*strength, 0, 1),
                np.clip(np.maximum((dot-.65)/.35, 0)**gamma*.55*hg*strength, 0, 1))
    return (np.clip((.65-dot)/.65*.7*strength, 0, 1),
            np.clip((dot-.65)/.35*.55*strength, 0, 1))


TONE_KNOTS = [('shadow', 9), ('midtone', 50), ('light', 70),
              ('bright', 87.5), ('peak', 97.25)]


def normal_source_ramp(values, sample, tones, base, strength=1):
    """Map diffuse ranks to measured material RGB, without copying AI pixels.

    A minimum .08 dot interval avoids amplifying a nearly flat/noisy normal
    field. Endpoints clamp rather than extrapolate. Returns diagnostics.
    """
    bands = (tones or {}).get('bands', {})
    if len(sample) < 32 or not all(k in bands for k, _ in TONE_KNOTS):
        return None, dict(status='insufficient_tones_or_samples')
    knots = np.percentile(sample, [q for _, q in TONE_KNOTS])
    spread = float(knots[-1]-knots[0])
    if spread < .08:
        return None, dict(status='flat_normals', dot_span=spread)
    # Preserve the median while preventing collapsed intervals from turning
    # tiny normal noise into arbitrarily sharp color steps.
    x = knots.copy()
    for i in range(1, len(x)):
        x[i] = max(x[i], x[i-1]+.08)
    x += knots[1]-x[1]
    colors = np.array([bands[k]['rgb'] for k, _ in TONE_KNOTS], np.float32)/255
    target = np.stack([np.interp(values, x, colors[:, c]) for c in range(3)], -1)
    target = np.clip(base+(target-base)*strength, 0, 1).astype(np.float32)
    return target, dict(status='mapped', dot_knots=knots.tolist(),
                        curve_knots=x.tolist(), dot_span=spread)


def rgb_lighting_layers(base, target):
    """Two editable Multiply/Screen layers reach even mixed-channel colors."""
    loss = np.clip((base-target)/np.maximum(base, 1/255), 0, 1)
    sa = loss.max(-1)
    shadow = 1-loss/np.maximum(sa[..., None], 1e-8)
    after = base*(1-loss)
    gain = np.clip((target-after)/np.maximum(1-after, 1/255), 0, 1)
    ha = gain.max(-1)
    highlight = gain/np.maximum(ha[..., None], 1e-8)
    return shadow, sa, highlight, ha


def material_normal_layers(normals,light,base,tones,sample,lighting,neutral):
    """Shared by PSD export and the read-only light picker preview."""
    from .coloring import material_lighting
    strength=lighting.get('strength',1)
    if not isinstance(strength,(int,float)) or not np.isfinite(strength) or not 0<=strength<=2:
        raise ValueError('Lighting strength must be 0..2')
    color_model=lighting.get('color_model','neutral')
    if color_model not in ('neutral','source_tones'):raise ValueError('Unknown lighting color_model')
    shadow,highlight,_=material_lighting(base,tones,neutral) if color_model=='source_tones' else (*neutral,'neutral')
    sa,ha=normal_alphas(normals,light,strength,lighting.get('normal_curve'))
    diag=None
    mapping=lighting.get('tone_mapping','absolute')
    if mapping=='source_ramp':
        if color_model!='source_tones':raise ValueError('source_ramp requires source_tones')
        target,diag=normal_source_ramp(normal_dot(normals,light),sample,tones,base,strength)
        if target is not None:shadow,sa,highlight,ha=rgb_lighting_layers(base,target)
    elif mapping!='absolute':raise ValueError('Normal lighting uses absolute or source_ramp')
    return shadow,sa,highlight,ha,diag


def load_normal_lighting(lighting, shape):
    if digest(lighting['normal_path']) != lighting['normal_hash']:
        raise ValueError('Normal map changed; prepare and review lighting again')
    normals = validate_normals(np.load(lighting['normal_path'], allow_pickle=False), shape)
    normal_alphas(normals, lighting['light'])  # validate direction before any export
    return normals


def prepare_normal_lighting(job, normals, light=None, preset=None):
    from .coloring import load_job, event
    job, _, _, plan, arrays = load_job(job)
    from .light_presets import light_preset, matching_preset
    if light is not None and preset is not None:
        raise ValueError('Choose light or preset, not both')
    choice=light_preset(1 if preset is None else preset) if light is None else None
    enabled=choice['enabled'] if choice else True
    light=choice['light'] if choice and enabled else (light if light is not None else light_preset(1)['light'])
    path = Path(normals).resolve()
    normal = validate_normals(np.load(path, allow_pickle=False), arrays['ink'].shape)
    normal_alphas(normal, light)
    if not (job/'flat_preview.png').exists():
        raise ValueError('Run paint-flats first')
    # Same pixel grid is mandatory; never warp vectors or the input artwork.
    vis = Image.fromarray(np.uint8(np.round((normal+1)*127.5)))
    save_image(vis, job/'lighting_aligned.png')
    save_image(vis, job/'lighting_before.png')
    plan['lighting'] = dict(method='normals', reviewed=False, notes='',
        normal_path=str(path), normal_hash=digest(path), light=list(light),
        guide_path=str(path), guide_hash=digest(path),
        aligned_hash=digest(job/'lighting_aligned.png'),
        flat_hash=digest(job/'flat_preview.png'), strength=1,
        enabled=enabled,preset=choice['id'] if choice else matching_preset(light),
        color_model='source_tones', temperature='neutral', tone_mapping='absolute')
    write_json(job/'semantic_plan.json', plan)
    write_json(job/'lighting_alignment.json', dict(method='normals', size=list(vis.size),
        resampling='none; inference output must already match input grid',
        convention='X right, Y up, Z viewer', cast_shadows=False))
    event(job, 'coloring_lighting_review', '法線と光方向を準備。固定線画・素材内で照明を生成',
          next_action='法線画像を確認しlighting.reviewedとnotesを記入')
    return dict(job=str(job), normals=str(path), light=list(light), reviewed=False)
