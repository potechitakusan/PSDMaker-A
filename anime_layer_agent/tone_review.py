"""Compare material appearance, independently of Base preservation/readback QA."""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from psd_tools import PSDImage
from scipy.ndimage import binary_erosion
from skimage.color import rgb2lab

from .coloring_reference import layer_path
from .composition import render_psd
from .storage import digest, read_json, write_json, save_image


def summarize(rgb, mask):
    """Interior quantile bands reduce outline/shadow and specular contamination.

    These are statistical bands, not a claim that illumination is understood.
    """
    mask = binary_erosion(mask, iterations=3)
    pixels = np.asarray(rgb)[mask]
    if len(pixels) < 32:
        return {'status': 'insufficient_interior', 'pixels': len(pixels)}
    lab = rgb2lab(pixels.astype(float) / 255)
    bands = {}
    for name, low, high in [('midtone', 40, 60), ('light', 60, 80), ('bright', 80, 95)]:
        lo, hi = np.percentile(lab[:, 0], [low, high])
        selected = (lab[:, 0] >= lo) & (lab[:, 0] <= hi)
        sample = lab[selected]
        bands[name] = dict(rgb=pixels[selected].mean(0).round(3).tolist(),
            L=round(float(sample[:, 0].mean()), 3),
            C=round(float(np.linalg.norm(sample[:, 1:], axis=1).mean()), 3))
    return dict(status='measured', pixels=len(pixels), bands=bands)


def material_samples(psd, parts, rgb):
    pixels = {tuple(layer_path(p)): p for p in psd.descendants() if not p.is_group()}
    masks = {}
    for part in parts:
        if part['semantic_id'] == 'background':
            continue
        key = part.get('palette_id', part['semantic_id'])
        if 'base_layer_path' in part:
            layer = pixels.get(tuple(part['base_layer_path']))
        else:
            name = part.get('display_name', part['semantic_id']) + '_Base'
            matches = [p for p in pixels.values() if p.name == name]
            layer = matches[0] if len(matches) == 1 else None
        if layer is None:
            raise ValueError('Tone review requires an unambiguous Base layer')
        mask = masks.setdefault(key, np.zeros((psd.height, psd.width), bool))
        rgba = np.asarray(layer.topil(apply_icc=False).convert('RGBA'))
        x0, y0, x1, y1 = layer.bbox
        # Only the intersection of the layer and canvas is visible.
        left, top, right, bottom = max(0,x0), max(0,y0), min(psd.width,x1), min(psd.height,y1)
        if right > left and bottom > top:
            mask[top:bottom,left:right] |= rgba[top-y0:bottom-y0,left-x0:right-x0,3] > 250
    return {key: summarize(rgb, mask) for key,mask in masks.items()}


def source_appearance(palette, folder):
    """Works with both source-job and portable PSD+JSON, using actual PSD pixels."""
    folder = Path(folder)
    if 'parts' not in palette:
        # Older source-job palettes predate the portable semantic hints.
        from .coloring import source_palette
        palette = source_palette(palette['source_job'])
    if digest(palette['psd_path']) != palette['psd_hash']:
        raise ValueError('Source PSD changed')
    psd = PSDImage.open(palette['psd_path'])
    image = render_psd(palette['psd_path'], generated=True)
    samples = material_samples(psd, palette['parts'], np.asarray(image))
    save_image(image, folder/'source_appearance.png')
    write_json(folder/'source_appearance.json', dict(psd_hash=palette['psd_hash'], materials=samples))
    return image, samples


def review_coloring_tones(job):
    from .coloring import load_job
    from .post_review import checked_manifest, sheet
    job, _, palette, plan, _ = load_job(job)
    manifest = checked_manifest(job)
    if manifest['pipeline'] != 'coloring':
        raise ValueError('Tone review requires a coloring job')
    folder = job/'post_review'
    folder.mkdir(exist_ok=True)
    source, before = source_appearance(palette, folder)
    current = render_psd(manifest['psd_path'], generated=True)
    after = material_samples(PSDImage.open(manifest['psd_path']), plan['parts'], np.asarray(current))
    materials = {}
    for key, sample in after.items():
        reference = before.get(key, {'status': 'missing_source'})
        result = dict(source=reference, current=sample, warnings=[])
        if reference['status'] == sample['status'] == 'measured':
            result['delta'] = {}
            for band in sample['bands']:
                old, new = reference['bands'][band], sample['bands'][band]
                delta = {name: round(new[name]-old[name],3) for name in ('L','C')}
                result['delta'][band] = delta
                if abs(delta['L']) >= 3 or abs(delta['C']) >= 3:
                    result['warnings'].append(band)
        materials[key] = result
    report = dict(source_psd_hash=palette['psd_hash'], psd_hash=manifest['psd_hash'],
        semantic_plan_hash=manifest['semantic_plan_hash'], materials=materials,
        interpretation='sRGB/D65 assumption; quantile bands, not matching poses or physical lighting. Differences require visual assessment, not automatic failure.')
    write_json(folder/'tones.json', report)
    sheet([('Source finished PSD',source),('Colored finished PSD',current)], folder/'tones_overview.png',columns=2)
    swatches = Image.new('RGB',(900,max(1,len(materials))*130+40),'#808080')
    draw = ImageDraw.Draw(swatches)
    draw.text((10,10),'Material | Source -> colored: middle / light / bright (L*, C*)',fill='white')
    for row,(key,data) in enumerate(materials.items()):
        y=40+row*130
        draw.text((10,y),key,fill='white')
        for i,band in enumerate(('midtone','light','bright')):
            for j,side in enumerate(('source','current')):
                sample=data[side]
                if sample['status'] != 'measured':
                    continue
                value=sample['bands'][band];x=210+i*225+j*108
                draw.rectangle((x,y,x+100,y+75),fill=tuple(round(c) for c in value['rgb']))
                draw.text((x,y+80),f"{value['L']:.1f}, {value['C']:.1f}",fill='white')
        if data['warnings']:
            draw.text((10,y+20),'Inspect tone difference',fill='white')
    save_image(swatches,folder/'tones_swatches.png')
    return dict(job=str(job), report=str(folder/'tones.json'),
        overview=str(folder/'tones_overview.png'),swatches=str(folder/'tones_swatches.png'),
        review_required=[key for key,v in materials.items() if v['warnings']])
