"""Explicit same-pose second-function development test against reviewed new Base.

Reuse exact fixed-input masks only after comparing them with the new PSD Base
alpha. Never copy source coordinates to a new pose.
"""
from pathlib import Path
import shutil,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
from PIL import Image,ImageDraw
from psd_tools import PSDImage
from anime_layer_agent.coloring_reference import load_coloring_reference
from anime_layer_agent.coloring import paint_flats,event
from anime_layer_agent.tone_review import source_appearance
from anime_layer_agent.storage import read_json,write_json,digest,save_image


def main():
    source=Path('work/normal_tones_calibrated')
    reference=Path('output/base_color_selection_example/coloring_reference.json')
    palette=load_coloring_reference(reference)
    psd=PSDImage.open(palette['psd_path'])
    layers={l.name:l for l in psd.descendants() if not l.is_group()}
    old=read_json(source/'semantic_plan.json')
    with np.load(source/'coloring_arrays.npz') as a:ink=a['ink'].copy()
    # The artist build refines a narrow boundary band. Use its final, reviewed
    # same-pose masks, rather than claiming the pre-refinement masks are equal.
    labels=np.load('work/base_color_selection_example/semantic_labels.npy')
    for i,p in enumerate(old['parts'],1):
        if p['semantic_id']=='background':continue
        layer=layers[p['display_name']+'_Base'];x,y,r,b=layer.bbox
        alpha=np.asarray(layer.topil().convert('RGBA'))[...,3]>0
        assert np.all(labels[y:b,x:r][alpha]==i) and alpha.sum()==(labels==i).sum()
    for name in ('new_base_imagegen','new_base_normals'):
        job=Path('work')/name
        if job.exists():raise ValueError('Use fresh jobs for this benchmark')
        job.mkdir(parents=True)
        event(job,'development_fixture','新Base参照PSDと既存固定線の同ポーズ開発試験。実Base alphaとの全35マスク一致を検証')
        for filename in ('lineart_binary.png','lineart_rgba.png','editing_lineart_white.png'):
            shutil.copy2(source/filename,job/filename)
        np.savez_compressed(job/'coloring_arrays.npz',ink=ink,barriers=ink,regions=labels)
        config=read_json(source/'coloring_job.json')
        config.update(palette_hash=digest(reference),arrays_hash=digest(job/'coloring_arrays.npz'),development_fixture=True)
        write_json(job/'source_palette.json',palette);config['palette_hash']=digest(job/'source_palette.json')
        write_json(job/'coloring_job.json',config)
        plan=dict(pipeline='coloring',classification_source='partial_review',extraction_hash=config['arrays_hash'],
            parts=old['parts'],background_rgb=[255,255,255],lighting=None,
            development_fixture='Exact same input line and already reviewed masks; verified new source PSD Base alpha equality.')
        write_json(job/'semantic_plan.json',plan)
        source_appearance(palette,job)
        swatches=Image.new('RGB',(700,40*len(palette['palettes'])+20),'white');d=ImageDraw.Draw(swatches)
        for i,(key,v) in enumerate(palette['palettes'].items()):
            d.rectangle((10,10+i*40,65,40+i*40),fill=tuple(v['rgb']));d.text((80,20+i*40),key,fill='black')
        save_image(swatches,job/'palette_review.png')
        # Preview labels are generated here without certifying classification.
        from anime_layer_agent.coloring import load_job,paint_preview
        _,_,p,pl,a=load_job(job);paint_preview(job,pl,a,p,True)
        write_json(job/'development_input_checks.json',dict(source_psd_hash=palette['psd_hash'],
            reference_hash=digest(reference),same_input_line=True,source_Base_mask_count=35,source_Base_masks_equal=True))
        print(job,flush=True)


if __name__=='__main__':main()
