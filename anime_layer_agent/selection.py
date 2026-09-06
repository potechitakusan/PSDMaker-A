"""Seeded paint-style selections, with reproducible semantic reassignment."""
from pathlib import Path
import re

import numpy as np
from PIL import Image
from scipy import ndimage as ndi
from scipy.spatial import cKDTree
from skimage.color import rgb2lab, rgb2hsv, deltaE_ciede2000

from .storage import read_json, write_json, save_image, digest
from .regions import bbox_of

HUES = {'red': (345, 15), 'orange': (15, 45), 'yellow': (45, 75),
        'green': (75, 165), 'cyan': (165, 195), 'blue': (195, 255),
        'purple': (255, 285), 'pink': (285, 345)}


def recover_background_boundary(rgb, labels, parts, config):
    """Opt-in repair for a visually confirmed light, low-chroma background.

    Only a narrow foreground border is eligible; compare both foreground and
    actual background colors. Dark/scenic backgrounds require seed selections.
    """
    if not config:
        return labels
    radius = config.get('max_distance', 20)
    margin = config.get('color_margin', 3)
    if not isinstance(radius, (int, float)) or not 1 <= radius <= 32 or not 0 <= margin <= 20:
        raise ValueError('Background boundary radius must be 1..32 and margin 0..20')
    if config.get('mode') != 'reviewed_light_background':
        raise ValueError('Boundary recovery requires reviewed_light_background mode')
    bg = next(i for i,p in enumerate(parts,1) if p['semantic_id']=='background')
    background = labels == bg
    lab = rgb2lab(rgb)
    border = np.zeros(labels.shape, bool)
    border[[0,-1],:] = True; border[:,[0,-1]] = True
    samples = lab[background & border & (lab[...,0] >= 88) & (np.linalg.norm(lab[...,1:],axis=2) < 20)]
    if not len(samples):
        raise ValueError('No light neutral background samples; use explicit color selections')
    background_tree = cKDTree(samples[::max(1,len(samples)//2000)])
    distance = ndi.distance_transform_edt(background)
    candidate = background & (distance <= radius)
    foreground = ~background
    points = np.column_stack(np.nonzero(foreground))
    if not len(points) or not candidate.any():
        return labels
    # Spatial neighbors retain local ownership, even with similarly colored distant parts.
    points = points[::3]
    query = np.column_stack(np.nonzero(candidate))
    distances, indices = cKDTree(points).query(query, k=min(8,len(points)))
    if distances.ndim == 1:
        distances, indices = distances[:,None], indices[:,None]
    neighbors = points[indices]
    candidate_lab = lab[candidate]
    color_distance = np.linalg.norm(lab[neighbors[...,0],neighbors[...,1]]-candidate_lab[:,None,:],axis=2)
    color_distance[distances > radius] = np.inf
    choice = color_distance.argmin(axis=1)
    best = color_distance[np.arange(len(query)),choice]
    bg_distance, _ = background_tree.query(candidate_lab)
    accepted = (best + margin < bg_distance) & (bg_distance > 8)
    owners = neighbors[np.arange(len(query)),choice]
    result = labels.copy()
    result[tuple(query[accepted].T)] = labels[tuple(owners[accepted].T)]
    return result


def color_mask(rgb, x, y, tolerance=15, metric='delta-e-2000', contiguous=True,
               connectivity=4, families=None, hue_range=None, scope=None):
    """Tolerance uses Delta E units, or Euclidean RGB distance in 0..255."""
    if not (isinstance(x, int) and isinstance(y, int) and 0 <= x < rgb.shape[1] and 0 <= y < rgb.shape[0]):
        raise ValueError('Seed must be an integer pixel inside the image')
    if not np.isfinite(tolerance) or not 0 <= tolerance <= (442 if metric == 'rgb' else 100):
        raise ValueError('Invalid color selection tolerance')
    if connectivity not in (4, 8):
        raise ValueError('connectivity must be 4 or 8')
    if metric == 'rgb':
        distance = np.linalg.norm((rgb - rgb[y, x]) * 255, axis=2)
    elif metric in ('delta-e-76', 'delta-e-2000'):
        lab = rgb2lab(rgb)
        distance = (np.linalg.norm(lab - lab[y, x], axis=2) if metric == 'delta-e-76'
                    else deltaE_ciede2000(lab, lab[y, x].reshape(1, 1, 3)))
    else:
        raise ValueError('Unknown selection metric')
    mask = distance <= tolerance
    if families or hue_range is not None:
        hsv = rgb2hsv(rgb)
        hue, sat = hsv[..., 0] * 360, hsv[..., 1]
        def interval(bounds):
            lo, hi = bounds
            if not (0 <= lo <= 360 and 0 <= hi <= 360):
                raise ValueError('Hue bounds must be in 0..360 degrees')
            return ((hue >= lo) & (hue <= hi) if lo <= hi else (hue >= lo) | (hue <= hi))
        if families:
            allowed = np.zeros(mask.shape, bool)
            for family in families:
                if family == 'neutral':
                    allowed |= sat < .12
                elif family in HUES:
                    allowed |= interval(HUES[family]) & (sat >= .12)
                else:
                    raise ValueError(f'Unknown color family: {family}')
            mask &= allowed
        if hue_range is not None:
            mask &= interval(hue_range) & (sat >= .12)
    if scope is not None:
        if scope.shape != mask.shape:
            raise ValueError('Selection scope size mismatch')
        mask &= scope
    if not mask[y, x]:
        raise ValueError('Seed excluded by scope or color-family constraints')
    if contiguous:
        components, _ = ndi.label(mask, ndi.generate_binary_structure(2, 1 if connectivity == 4 else 2))
        mask = components == components[y, x]
    return mask


def apply_selections(rgb, labels, plan):
    """Apply last, so watershed/island cleanup cannot undo reviewed repairs."""
    names = {p['semantic_id']: i for i, p in enumerate(plan['parts'], 1)}
    occupied = np.zeros(labels.shape, bool)
    original = labels.copy()
    for assignment in plan.get('selection_assignments', []):
        if assignment['to'] not in names or assignment['from'] not in names:
            raise ValueError('Unknown semantic part in selection assignment')
        if assignment['to'] == assignment['from']:
            raise ValueError('Selection source and destination must differ')
        scope = original == names[assignment['from']]
        mask = color_mask(rgb, **assignment['selection'], scope=scope)
        if np.any(occupied & mask):
            raise ValueError('Overlapping color selections: each pixel needs one assignment')
        occupied |= mask
        labels[mask] = names[assignment['to']]
    return labels


def apply_component_assignments(geometry, materials, labels, plan):
    """Reassign reviewed disconnected islands without broad color thresholds."""
    names={p['semantic_id']:i for i,p in enumerate(plan['parts'],1)}
    occupied=np.zeros(labels.shape,bool)
    original=labels.copy()
    for item in plan.get('component_assignments',[]):
        if item.get('from') not in names or item.get('to') not in names:
            raise ValueError('Unknown component source/destination part')
        if item.get('kind') not in ('geometry','material'):
            raise ValueError('Component kind must be geometry or material')
        source=geometry if item['kind']=='geometry' else materials
        cc,count=ndi.label(source==item['region'])
        ids=item.get('components',[])
        if not ids or len(set(ids))!=len(ids) or any(not isinstance(n,int) or not 1<=n<=count for n in ids):
            raise ValueError('Unknown or duplicate component ID')
        mask=np.isin(cc,ids)
        if np.any(occupied&mask) or np.any(original[mask]!=names[item['from']]):
            raise ValueError('Overlapping component assignments or source mismatch')
        occupied|=mask
        labels[mask]=names[item['to']]
    return labels


def review_components(job, geometry=None, material=None):
    from .compact import material_labels,region_sheets
    job=Path(job).resolve()
    if (geometry is None)==(material is None):
        raise ValueError('Specify exactly one geometry or material')
    with np.load(job/'compact_arrays.npz') as arrays:
        source=arrays['geometry'] if geometry is not None else material_labels(arrays,read_json(job/'semantic_plan.json'))
        number=geometry if geometry is not None else material
        if number not in np.unique(source):raise ValueError('Unknown region ID')
        cc,_=ndi.label(source==number)
        prefix=f'components_{"G" if geometry is not None else "M"}{number}'
        records=region_sheets(arrays['rgb'],cc,job,prefix)
    write_json(job/f'{prefix}.json',{'components':records})
    return {'job':str(job),'components':len(records),'prefix':prefix}


def select_color(job, x, y, name='selection', tolerance=15, metric='delta-e-2000',
                 global_match=False, connectivity=4, families=None, hue_range=None, source_part=None):
    from .compact import semantic_masks
    job = Path(job).resolve()
    if not re.fullmatch(r'[A-Za-z0-9_-]+', name):
        raise ValueError('Selection name must contain only letters, digits, _ or -')
    with np.load(job / 'compact_arrays.npz') as arrays:
        rgb = arrays['rgb']
        scope = None
        plan = read_json(job / 'semantic_plan.json')
        if source_part:
            names = [p['semantic_id'] for p in plan['parts']]
            if source_part not in names:
                raise ValueError('Unknown source part')
            # Same pre-repair partition used when building selection assignments.
            base_plan = dict(plan, selection_assignments=[])
            scope = semantic_masks(arrays, base_plan) == names.index(source_part) + 1
        spec = dict(x=x, y=y, tolerance=tolerance, metric=metric, contiguous=not global_match,
                    connectivity=connectivity, families=families, hue_range=hue_range)
        mask = color_mask(rgb, **spec, scope=scope)
        overlaps = {str(int(g)): int(np.count_nonzero(mask & (arrays['geometry'] == g)))
                    for g in np.unique(arrays['geometry'][mask])}
        preview = rgb.copy()
        preview[mask] = preview[mask] * .45 + np.array([0, 1, .5]) * .55
        directory = job / 'selections'
        save_image(Image.fromarray(np.uint8(mask) * 255), directory / f'{name}_mask.png')
        save_image(Image.fromarray(np.uint8(np.round(preview * 255))), directory / f'{name}_preview.png')
        record = dict(selection=spec, source_part=source_part, reference_hash=digest(job / 'reference.png'),
                      pixels=int(mask.sum()), bbox=bbox_of(mask), geometry_overlaps=overlaps,
                      seed_rgb=np.round(rgb[y, x] * 255).astype(int).tolist(),
                      preview=str(directory / f'{name}_preview.png'))
        write_json(directory / f'{name}.json', record)
    return dict(record, selection_file=str(directory / f'{name}.json'))


def assign_selection(job, selection, to, source_part):
    """A saved preview is a proposal; applying it invalidates semantic review."""
    from .compact import semantic_masks
    job = Path(job).resolve()
    record = read_json(selection)
    if record['reference_hash'] != digest(job / 'reference.png') or record['source_part'] != source_part:
        raise ValueError('Selection belongs to a different reference or source part')
    plan = read_json(job / 'semantic_plan.json')
    revised = dict(plan, selection_assignments=[*plan.get('selection_assignments', []),
                   {'from': source_part, 'to': to, 'selection': record['selection']}],
                   classification_source='partial_review')
    with np.load(job / 'compact_arrays.npz') as arrays:
        semantic_masks(arrays, revised)
    write_json(job / 'history' / f"before_selection_{len(revised['selection_assignments']):03d}.json", plan)
    write_json(job / 'semantic_plan.json', revised)
    return {'job': str(job), 'next_action': 'Review selection preview and revised parts; set astra_reviewed, then build-compact'}
