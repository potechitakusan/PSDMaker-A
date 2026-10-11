"""Development test: inherit the public coloring PSD's exact Base masks and ink."""
import json
from pathlib import Path
import re
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from PIL import Image
from psd_tools import PSDImage
from anime_layer_agent.coloring_reference import layer_path, solid_rgb, load_coloring_reference
from anime_layer_agent.storage import digest, write_json, save_image
from anime_layer_agent.coloring import paint_preview, load_job
from anime_layer_agent.composition import render_psd


def main():
    root = Path('work/normal_input').resolve()
    root.mkdir(parents=True, exist_ok=True)
    target = root/'output.psd'
    shutil.copy2('examples/coloring/output.psd', target)
    psd = PSDImage.open(target)
    pixels = [l for l in psd.descendants() if not l.is_group()]
    source = json.loads(Path('examples/output/coloring_reference.json').read_text('utf8'))
    by_rgb = {tuple(v['rgb']): (k,v) for k,v in source['palettes'].items()}
    labels = np.zeros((psd.height,psd.width), np.int32)
    parts=[]; palettes={}; overlap=0
    for layer in pixels:
        if not layer.name.endswith('_Base'):continue
        rgb = solid_rgb(layer)
        index = len(parts)+1
        a = np.asarray(layer.topil().convert('RGBA'))[...,3]
        if not set(np.unique(a)) <= {0,255}:raise ValueError('Fixture requires binary masks')
        x,y,r,b = layer.bbox
        mask = a>0
        overlap += int(((labels[y:b,x:r]>0)&mask).sum())
        labels[y:b,x:r][mask] = index
        sid = re.search(r'\[([^]]+)\]',layer.parent.name)
        sid = sid.group(1) if sid else 'background'
        title = layer.name[:-5]
        part = dict(semantic_id=sid,display_name=title,regions=[index],group_path=layer_path(layer)[:-2])
        if sid!='background':
            key, old = by_rgb[tuple(rgb)]
            part['palette_id'] = key
            part['coloring_notes'] = 'Same-pose test; exact existing PSD mask inherited, not new region classification.'
            entry = palettes.setdefault(key,dict(rgb=rgb,source_parts=[]))
            if 'tones' in old:entry['tones']=old['tones']
            entry['source_parts'].append(sid)
        parts.append(part)
    if overlap or (labels==0).any():raise ValueError('Overlapping or incomplete original Base masks')
    line = next(l for l in pixels if l.name.startswith('Lineart'))
    ink = np.zeros(labels.shape,bool)
    x,y,r,b = line.bbox
    ink[y:b,x:r] = np.asarray(line.topil().convert('RGBA'))[...,3]>0
    linepath=root/'lineart.png'
    save_image(Image.fromarray(np.uint8(~ink)*255),linepath)
    refparts=[]
    bases=[l for l in pixels if l.name.endswith('_Base')]
    for part,layer in zip(parts,bases):
        if part['semantic_id']=='background':continue
        refparts.append({k:v for k,v in part.items() if k!='regions'} | dict(base_layer_path=layer_path(layer),source_bbox=list(layer.bbox)))
    ref = dict(schema=1,kind='coloring_reference',psd_file='output.psd',psd_hash=digest(target),
               semantic_plan_hash=digest('examples/output/coloring_reference.json'),size=list(psd.size),
               classification_source='inherited_public_fixture',palettes=palettes,parts=refparts)
    write_json(root/'coloring_reference.json',ref)
    palette=load_coloring_reference(root/'coloring_reference.json')
    save_image(render_psd(target,generated=True),root/'existing_preview.png')
    for name in ('normals_left','normals_right'):
        job=Path('work')/name
        if (job/'coloring_job.json').exists():
            prior=json.loads((job/'coloring_job.json').read_text('utf8'))
            if not prior.get('development_fixture') or (job/'layers.json').exists():
                raise ValueError('Use a fresh fixture job')
        job.mkdir(parents=True,exist_ok=True)
        np.savez_compressed(job/'coloring_arrays.npz',ink=ink,barriers=ink,regions=labels)
        write_json(job/'source_palette.json',palette)
        for file in ('lineart_binary.png','editing_lineart_white.png'):
            save_image(Image.fromarray(np.uint8(~ink)*255),job/file)
        rgba=np.zeros((*ink.shape,4),np.uint8);rgba[...,3]=ink*255
        save_image(Image.fromarray(rgba),job/'lineart_rgba.png')
        config=dict(schema=1,pipeline='coloring',lineart_source=str(linepath),lineart_hash=digest(linepath),
                    threshold=192,gap_close=0,min_area=1,palette_hash=digest(job/'source_palette.json'),
                    arrays_hash=digest(job/'coloring_arrays.npz'),development_fixture=True)
        write_json(job/'coloring_job.json',config)
        write_json(job/'semantic_plan.json',dict(pipeline='coloring',classification_source='partial_review',
            extraction_hash=config['arrays_hash'],parts=parts,background_rgb=[255,255,255],lighting=None))
        _,_,pal,plan,arrays=load_job(job)
        paint_preview(job,plan,arrays,pal,True)
    write_json(root/'preservation.json',dict(size=list(psd.size),parts=len(parts),overlap=overlap,
        source_hash=digest('examples/coloring/output.psd'),copy_hash=digest(target),
        binary_ink_pixels=int(ink.sum()),masks='exact original Base alpha; no segmentation or geometry edit'))


if __name__=='__main__':main()
