"""Independent same-pose development fixture with already reviewed PSD masks.

This is an artist color/decomposition test, not a new segmentation judgment.
"""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
from scipy import ndimage as ndi
from PIL import Image,ImageDraw
from anime_layer_agent.storage import read_json,write_json,digest,save_image
from anime_layer_agent.artist import configure_editing,review_editing
from anime_layer_agent.coloring import event


def main():
    job=Path('work/base_color_selection_example');job.mkdir(parents=True,exist_ok=True)
    if (job/'compact_job.json').exists():raise ValueError('Use a fresh development job')
    event(job,'development_fixture','同ポーズの公開着色作例と既に目視済みのBaseマスク・線を使う第1機能の開発試験')
    source=Path('work/appearance_normals_left')
    plan=read_json(source/'semantic_plan.json');plan.pop('lighting',None)
    plan.pop('pipeline',None);plan.pop('extraction_hash',None)
    plan['recover_background_leaks']=False
    plan['development_fixture']='Same-pose artist Base candidate test; exact previously reviewed public PSD Base masks, not a new image segmentation.'
    for i,p in enumerate(plan['parts'],1):p['regions']=[i]
    arrays=np.load(source/'coloring_arrays.npz');labels=arrays['regions'];ink=arrays['ink']
    reference=source/'post_review/source_appearance.png';line=Path('work/normal_input/lineart.png')
    rgb=np.asarray(Image.open(reference).convert('RGB'),np.float32)/255
    target=rgb.copy()
    for i,p in enumerate(plan['parts'],1):
        mask=labels==i;inner=mask & ~ink
        if not inner.any():inner=mask
        nearest=ndi.distance_transform_edt(~inner,return_distances=False,return_indices=True)
        fill=mask & ink;target[fill]=rgb[tuple(ix[fill] for ix in nearest)]
    alpha=ink.astype(np.float32);zero=np.zeros_like(rgb)
    np.savez_compressed(job/'compact_arrays.npz',rgb=rgb,underlying=target,ink=zero,alpha=alpha,
                        guide=alpha,warped=alpha,geometry=labels)
    write_json(job/'compact_job.json',dict(config=dict(reference_hash=digest(reference),lineart_hash=digest(line),compact_version=2),
        reference_source=str(reference.resolve()),lineart_source=str(line.resolve()),development_fixture=True))
    write_json(job/'semantic_plan.json',plan)
    save_image(Image.open(reference).convert('RGB'),job/'reference.png')
    rgba=np.zeros((*ink.shape,4),np.uint8);rgba[...,3]=ink*255
    save_image(Image.fromarray(rgba),job/'aligned_lineart_rgba.png')
    save_image(Image.fromarray(np.uint8(~ink)*255),job/'aligned_lineart_white.png')
    original_line=Image.open(line).convert('RGB')
    sheet=Image.new('RGB',(rgb.shape[1]*3,rgb.shape[0]+30),'white');d=ImageDraw.Draw(sheet)
    for i,title in enumerate(['Fixed input line','Inherited same-grid line','Reference appearance']):
        sheet.paste(Image.open(reference).convert('RGB') if i==2 else original_line,(i*rgb.shape[1],30))
        d.text((i*rgb.shape[1]+5,5),title,fill='black')
    save_image(sheet,job/'alignment_comparison.png')
    write_json(job/'alignment.json',dict(development_fixture=True,size=list(original_line.size),
        warp='none; exact previously reviewed same-pose line and masks',source=digest(reference)))
    configure_editing(job,color_tolerance='0')
    print(review_editing(job)['base_candidates'])


if __name__=='__main__':main()
