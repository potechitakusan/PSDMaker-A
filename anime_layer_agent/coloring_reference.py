"""Portable, PSD-verified palette and semantic hints for a new drawing."""
from pathlib import Path

import numpy as np
from psd_tools import PSDImage

from .storage import digest, read_json, write_json


def layer_path(layer):
    names = [layer.name]
    parent = layer.parent
    while parent is not None and parent.parent is not None:
        names.insert(0, parent.name)
        parent = parent.parent
    return names


def solid_rgb(layer):
    rgba = np.asarray(layer.topil().convert('RGBA'))
    colors = np.unique(rgba[rgba[..., 3] > 0, :3], axis=0)
    if len(colors) != 1:
        raise ValueError('Source Base must have one solid color; use the artist source profile')
    return colors[0].tolist()


def export_coloring_reference(job):
    """Export only reusable metadata; never copy masks, local paths or region IDs."""
    job = Path(job).resolve()
    manifest = read_json(job / 'layers.json')
    plan = read_json(job / 'semantic_plan.json')
    psd_path = Path(manifest['psd_path']).resolve()
    if digest(psd_path) != manifest['psd_hash'] or digest(job / 'semantic_plan.json') != manifest['semantic_plan_hash']:
        raise ValueError('Source PSD/plan changed; rebuild the source job first')
    psd = PSDImage.open(psd_path)
    pixels = {}
    for layer in psd.descendants():
        if not layer.is_group():
            pixels.setdefault(layer.name, []).append(layer)
    palettes, parts = {}, []
    for part in plan['parts']:
        sid = part['semantic_id']
        if sid == 'background':
            continue
        name = part.get('display_name', sid) + '_Base'
        candidates = pixels.get(name, [])
        if len(candidates) != 1:
            raise ValueError(f'Source Base layer missing or ambiguous: {name}')
        layer = candidates[0]
        rgb = solid_rgb(layer)
        key = part.get('palette_id', sid)
        palette = palettes.setdefault(key, dict(rgb=rgb, source_parts=[]))
        if palette['rgb'] != rgb:
            raise ValueError(f'Source palette contains different Base colors: {key}')
        palette['source_parts'].append(sid)
        parts.append(dict(semantic_id=sid, display_name=part.get('display_name', sid),
            palette_id=key, group_path=part.get('group_path', []),
            coloring_notes=part.get('coloring_notes', ''), base_layer_path=layer_path(layer),
            source_bbox=list(layer.bbox)))
    if not parts:
        raise ValueError('Source PSD has no foreground Base layers')
    reference = dict(schema=1, kind='coloring_reference', psd_file=psd_path.name,
        psd_hash=manifest['psd_hash'], semantic_plan_hash=manifest['semantic_plan_hash'],
        size=list(psd.size), bbox_convention='source pixels; left, top, right, bottom; exclusive end',
        classification_source=plan.get('classification_source', 'unknown'),
        palettes=palettes, parts=parts)
    # The adjacent copy is portable; the job copy is a record of the same export.
    path = psd_path.parent / 'coloring_reference.json'
    write_json(path, reference)
    if path != job / 'coloring_reference.json':
        write_json(job / 'coloring_reference.json', reference)
    return dict(reference=str(path), psd=str(psd_path), palettes=len(palettes), parts=len(parts))


def load_coloring_reference(path):
    path = Path(path).resolve()
    reference = read_json(path)
    if reference.get('schema') != 1 or reference.get('kind') != 'coloring_reference':
        raise ValueError('Unsupported coloring reference schema')
    filename = reference['psd_file']
    if not isinstance(filename, str) or not filename or any(c in filename for c in '/\\:') or filename in ('.', '..'):
        raise ValueError('Reference PSD must be a filename beside the JSON')
    psd_path = path.parent / filename
    if digest(psd_path) != reference['psd_hash']:
        raise ValueError('Source PSD changed; export a new coloring reference')
    psd = PSDImage.open(psd_path)
    if list(psd.size) != reference['size']:
        raise ValueError('Source PSD size mismatch')
    pixels = {tuple(layer_path(p)): p for p in psd.descendants() if not p.is_group()}
    seen = set()
    for part in reference['parts']:
        sid = part['semantic_id']
        if sid in seen:
            raise ValueError('Duplicate source semantic ID')
        seen.add(sid)
        layer = pixels.get(tuple(part['base_layer_path']))
        palette = reference['palettes'][part['palette_id']]
        if layer is None or solid_rgb(layer) != palette['rgb'] or sid not in palette['source_parts']:
            raise ValueError('Reference palette does not match the actual PSD Base')
    expected = {key: sorted(p['semantic_id'] for p in reference['parts'] if p['palette_id'] == key)
                for key in reference['palettes']}
    if not seen or any(not ids or ids != sorted(reference['palettes'][key]['source_parts']) for key, ids in expected.items()):
        raise ValueError('Reference palette part mapping mismatch')
    return dict(psd_path=str(psd_path), psd_hash=reference['psd_hash'],
        semantic_plan_hash=reference['semantic_plan_hash'], palettes=reference['palettes'],
        parts=reference['parts'], reference_path=str(path), reference_hash=digest(path))
