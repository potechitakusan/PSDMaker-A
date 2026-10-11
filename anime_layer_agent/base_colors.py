"""Reviewable artist Base candidates; a frequent shaded color is not albedo."""
from pathlib import Path
import hashlib
import numpy as np
from PIL import Image,ImageDraw
from skimage.color import rgb2lab
from .storage import read_json,write_json,save_image


def base_candidates(plan,labels,target,alpha):
    from .artist import dominant_color
    fingerprint=hashlib.sha256()
    for a in (labels,target,alpha):
        fingerprint.update(str(a.shape).encode());fingerprint.update(np.ascontiguousarray(a).tobytes())
    groups={}
    for i,p in enumerate(plan['parts'],1):
        if p['semantic_id']=='background':continue
        key=p.get('palette_id',p['semantic_id'])
        fingerprint.update(f'{i}:{p["semantic_id"]}:{key}'.encode())
        mask=labels==i
        sample=target[mask & (alpha<.12)]
        if not len(sample):sample=target[mask]
        if not len(sample):raise ValueError('Cannot estimate Base for empty material')
        sample=sample[::max(1,len(sample)//20000)]
        L=rgb2lab(sample.reshape(-1,1,3))[:,0,0]
        candidates={'dominant':dominant_color(target,mask,alpha)}
        for name,band in [('midtone',(40,60)),('light',(60,80))]:
            lo,hi=np.percentile(L,band)
            candidates[name]=np.median(sample[(L>=lo)&(L<=hi)],axis=0)
        groups.setdefault(key,[]).append((p['semantic_id'],candidates))
    result={}
    for key,entries in groups.items():
        candidates={name:np.round(np.clip(np.median([c[name] for _,c in entries],axis=0),1/255,254/255)*255).astype(int).tolist()
                    for name in ('dominant','midtone','light')}
        lab=rgb2lab(np.array(list(candidates.values())).reshape(-1,1,3)/255)[:,0]
        result[key]=dict(parts=[sid for sid,_ in entries],candidates=candidates,
            dominant_to_light_delta_e76=float(np.linalg.norm(lab[0]-lab[2])),
            status='visual_review_required')
    return dict(input_hash=fingerprint.hexdigest(),palettes=result,
                method='Equal weight per semantic part; line-excluded dominant, L*40..60 and 60..80 percentile medians. Not intrinsic albedo.')


def load_candidate_inputs(job):
    from .compact import semantic_masks
    from .artist import clean_linework
    job=Path(job).resolve();plan=read_json(job/'semantic_plan.json')
    if plan.get('editing',{}).get('profile')!='artist':raise ValueError('Configure artist editing first')
    with np.load(job/'compact_arrays.npz') as a:
        labels=semantic_masks(a,plan);target=a['underlying'];alpha=a['alpha']
        if plan['editing'].get('line_cleanup',False):target,alpha,_=clean_linework(a)
        report=base_candidates(plan,labels,target,alpha)
        rgb=a['rgb'].copy()
    return job,plan,report,rgb,labels


def review_base_colors(job):
    job,plan,report,rgb,labels=load_candidate_inputs(job)
    choices=plan['editing'].get('base_colors',{})
    pages=[];items=list(report['palettes'].items())
    for page,start in enumerate(range(0,len(items),6),1):
        sheet=Image.new('RGB',(1020,180*min(6,len(items)-start)),'#eeeeee');d=ImageDraw.Draw(sheet)
        for row,(key,entry) in enumerate(items[start:start+6]):
            y=row*180;mask=np.isin(labels,[i for i,p in enumerate(plan['parts'],1) if p.get('palette_id',p['semantic_id'])==key])
            # Full-image material location inset: image geometry is Python's work.
            picture=np.uint8(np.round(np.clip(rgb,0,1)*255)).copy()
            picture[~mask]=picture[~mask]//4+185
            pic=Image.fromarray(picture);pic.thumbnail((165,160));sheet.paste(pic,(5,y+15))
            d.text((180,y+8),key+' / '+', '.join(entry['parts']),fill='black')
            for col,(name,color) in enumerate(entry['candidates'].items()):
                x=180+col*270;d.rectangle((x,y+40,x+240,y+110),fill=tuple(color))
                d.text((x,y+118),f'{name}: {color}',fill='black')
            choice=choices.get(key,{})
            valid=choice.get('input_hash')==report['input_hash']
            d.text((180,y+145),'Selection: '+(choice.get('candidate','pending') if valid else 'pending'),fill='black')
        path=job/f'base_candidates_{page:02d}.png';save_image(sheet,path);pages.append(path.name)
    report['sheets']=pages;write_json(job/'base_candidates.json',report)
    return dict(job=str(job),report=str(job/'base_candidates.json'),sheets=pages,
                next_action='Open every candidate sheet; select-base-color per material with visual notes')


def select_base_color(job,palette_id,candidate,notes):
    if not isinstance(notes,str) or not notes.strip():raise ValueError('Visual selection notes are required')
    job,plan,current,_,_=load_candidate_inputs(job)
    prior=read_json(job/'base_candidates.json')
    if prior['input_hash']!=current['input_hash']:raise ValueError('Base candidates are stale; review-base-colors again')
    entry=current['palettes'].get(palette_id)
    if not entry or candidate not in entry['candidates']:raise ValueError('Unknown palette or Base candidate')
    plan['editing'].setdefault('base_colors',{})[palette_id]=dict(candidate=candidate,
        rgb=entry['candidates'][candidate],notes=notes.strip(),input_hash=current['input_hash'],reviewed=True)
    # Existing review/build hashes become stale; do not change classification of
    # already reviewed geometry merely because its Base color changed.
    write_json(job/'semantic_plan.json',plan)
    return dict(job=str(job),palette_id=palette_id,selection=plan['editing']['base_colors'][palette_id],
                next_action='build-compact then fresh post-review; old PSD remains unchanged until build')
