"""Opt-in lineart coloring. No existing decomposition command calls this module."""
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw
from psd_tools import PSDImage
from scipy import ndimage as ndi
from scipy.spatial import cKDTree
from skimage.morphology import skeletonize

from .storage import read_json, write_json, digest, save_image, atomic_write, progress
from .compact import image_u8, region_sheets
from .regions import bbox_of
from .composition import export_psd, render_psd
from .artist import LIGHTING, count_groups


def event(job, phase, message, **extra):
    """Keep phase times as well as the most recent resumable status."""
    job=Path(job)
    now=datetime.now(timezone.utc)
    path=job/'timing.json'
    timing=read_json(path) if path.exists() else dict(started_at=now.isoformat(),
        origin='First coloring CLI work timestamp; request receipt unavailable', phases=[])
    elapsed=(now-datetime.fromisoformat(timing['started_at'])).total_seconds()
    timing['phases'].append(dict(phase=phase,at=now.isoformat(),elapsed_seconds=round(elapsed,3)))
    timing.update(elapsed_seconds=round(elapsed,3),elapsed_human=f'{int(elapsed)//60}m {int(elapsed)%60}s')
    if phase in ('complete','complete_with_limitations'):
        timing['completed_at']=now.isoformat()
    write_json(path,timing)
    state=progress(job,phase,message,**extra)
    if phase in ('complete','complete_with_limitations'):
        md=['# 着色作業時間','',f'開始: {timing["started_at"]}',f'完了: {now.isoformat()}',
            f'経過: {elapsed:.3f}秒 / {int(elapsed)//60}分{int(elapsed)%60}秒',f'起点: {timing["origin"]}','']
        md += [f'- {p["phase"]}: {p.get("at",p.get("completed_at"))} / {p["elapsed_seconds"]}秒' for p in timing['phases']]
        atomic_write(job/'time_log.md','\n'.join(md)+'\n')
        if state.get('psd') and Path(state['psd']).parent!=job:
            atomic_write(Path(state['psd']).parent/'time_log.md',(job/'time_log.md').read_bytes())
    return state


def binary_ink(path, threshold=192):
    if not isinstance(threshold,int) or not 1<=threshold<=254:
        raise ValueError('threshold must be 1..254')
    with Image.open(path) as source:
        rgba=np.asarray(source.convert('RGBA'),np.float32)/255
    gray=(rgba[...,:3]@np.array([.2126,.7152,.0722]))*rgba[...,3]+1-rgba[...,3]
    return gray < threshold/255


def binarize_lineart(input, output, threshold=192, transparent=False):
    if Path(input).resolve()==Path(output).resolve():
        raise ValueError('Save binarized lineart to a separate file')
    ink=binary_ink(input,threshold)
    if transparent:
        rgba=np.zeros((*ink.shape,4),np.uint8);rgba[...,3]=ink*255
        picture=Image.fromarray(rgba)
    else:
        picture=Image.fromarray(np.uint8(~ink)*255)
    save_image(picture,output)
    return dict(output=str(Path(output).resolve()),size=list(picture.size),threshold=threshold)


def gap_barriers(ink, gap_close):
    """Connect nearby facing stroke ends without thickening parallel strokes."""
    barriers=ink.copy()
    if not gap_close:return barriers
    skeleton=skeletonize(ink)
    count=ndi.convolve(skeleton.astype(np.uint8),np.ones((3,3),np.uint8),mode='constant')
    ends=np.column_stack(np.nonzero(skeleton&(count==2)))
    if len(ends)<2:return barriers
    tangents=[]
    for y,x in ends:
        previous=None;current=(int(y),int(x));start=np.array(current)
        for _ in range(5):
            cy,cx=current
            neighbors=[(ny,nx) for ny in range(max(0,cy-1),min(ink.shape[0],cy+2))
                for nx in range(max(0,cx-1),min(ink.shape[1],cx+2))
                if (ny,nx)!=current and (ny,nx)!=previous and skeleton[ny,nx]]
            if len(neighbors)!=1:break
            previous,current=current,neighbors[0]
        tangent=start-np.array(current)
        tangents.append(tangent/max(np.linalg.norm(tangent),1))
    candidates=[]
    for a,b in cKDTree(ends).query_pairs(gap_close+1):
        delta=ends[b]-ends[a];length=np.linalg.norm(delta);direction=delta/length
        if np.dot(tangents[a],direction)>.05 and np.dot(tangents[b],-direction)>.05:
            candidates.append((length,a,b))
    used=set();canvas=barriers.astype(np.uint8)
    for _,a,b in sorted(candidates):
        if a in used or b in used:continue
        cv2.line(canvas,tuple(int(v) for v in ends[a][::-1]),tuple(int(v) for v in ends[b][::-1]),1,1)
        used.update((a,b))
    return canvas.astype(bool)


def enclosed_regions(ink, gap_close=3, min_area=12):
    if not isinstance(gap_close,int) or not 0<=gap_close<=64:
        raise ValueError('gap_close must be 0..64 pixels')
    if not isinstance(min_area,int) or not 1<=min_area<=1000:
        raise ValueError('min_area must be 1..1000')
    if not ink.any() or ink.all():raise ValueError('Lineart needs both ink and open areas')
    # Closing affects flood-fill barriers only, never the delivered ink pixels.
    barriers=gap_barriers(ink,gap_close)
    cc,_=ndi.label(~barriers)
    counts=np.bincount(cc.ravel());keep=counts>=min_area;keep[0]=False
    cc=np.where(keep[cc],cc,0)
    if not cc.any():raise ValueError('No fillable regions; lower gap_close or min_area')
    lut=np.zeros(len(counts),np.int32);ids=np.flatnonzero(keep);lut[ids]=np.arange(1,len(ids)+1)
    cc=lut[cc]
    nearest=ndi.distance_transform_edt(cc==0,return_distances=False,return_indices=True)
    labels=cc[tuple(nearest)]
    return labels.astype(np.int32),barriers


def source_palette(source_job):
    from .coloring_reference import layer_path
    job=Path(source_job).resolve()
    manifest=read_json(job/'layers.json');plan=read_json(job/'semantic_plan.json')
    if digest(manifest['psd_path'])!=manifest['psd_hash'] or digest(job/'semantic_plan.json')!=manifest['semantic_plan_hash']:
        raise ValueError('Source PSD/plan changed; rebuild the source job first')
    psd=PSDImage.open(manifest['psd_path'])
    pixels={p.name:p for p in psd.descendants() if not p.is_group()}
    palettes={};parts=[]
    for part in plan['parts']:
        if part['semantic_id']=='background':continue
        name=part.get('display_name',part['semantic_id'])+'_Base'
        if name not in pixels:raise ValueError(f'Source Base layer missing: {name}')
        rgba=np.asarray(pixels[name].topil().convert('RGBA'))
        sample=rgba[rgba[...,3]>0,:3]
        if not len(sample):raise ValueError('Source Base is empty')
        colors=np.unique(sample,axis=0)
        if len(colors)!=1:raise ValueError('Source Base must have one solid color; use the artist source profile')
        key=part.get('palette_id',part['semantic_id']);color=colors[0].tolist()
        if key in palettes and palettes[key]['rgb']!=color:
            raise ValueError(f'Source palette contains different Base colors: {key}')
        palettes.setdefault(key,dict(rgb=color,source_parts=[]))['source_parts'].append(part['semantic_id'])
        parts.append(dict(semantic_id=part['semantic_id'],display_name=part.get('display_name',part['semantic_id']),
            palette_id=key,group_path=part.get('group_path',[]),coloring_notes=part.get('coloring_notes',''),
            base_layer_path=layer_path(pixels[name]),source_bbox=list(pixels[name].bbox)))
    return dict(source_job=str(job),psd_path=manifest['psd_path'],psd_hash=manifest['psd_hash'],
                semantic_plan_hash=manifest['semantic_plan_hash'],palettes=palettes,parts=parts)


def prepare_coloring(lineart, source_job=None, job=None, threshold=192, gap_close=3, min_area=12, source_reference=None):
    # All inputs are explicit. In particular there is no default lineart path.
    if (source_job is None) == (source_reference is None):
        raise ValueError('Specify exactly one source job or coloring reference')
    if job is None:raise ValueError('A separate coloring job is required')
    job=Path(job).resolve();lineart=Path(lineart).resolve()
    if source_job is not None and job==Path(source_job).resolve():raise ValueError('Use a separate coloring job')
    if any((job/name).exists() for name in ('compact_job.json','job.json','coloring_job.json','semantic_plan.json')):
        raise ValueError('Use a fresh coloring job; existing jobs are never overwritten')
    if source_reference is not None:
        from .coloring_reference import load_coloring_reference
        palette=load_coloring_reference(source_reference)
    else:
        palette=source_palette(source_job)
    ink=binary_ink(lineart,threshold);labels,barriers=enclosed_regions(ink,gap_close,min_area)
    job.mkdir(parents=True,exist_ok=True)
    event(job,'coloring_prepare','指定線画を二値化し、閉領域と参照PSDの配色を準備')
    save_image(Image.fromarray(np.uint8(~ink)*255),job/'lineart_binary.png')
    rgba=np.zeros((*ink.shape,4),np.uint8);rgba[...,3]=ink*255
    save_image(Image.fromarray(rgba),job/'lineart_rgba.png')
    save_image(Image.fromarray(np.uint8(~ink)*255),job/'editing_lineart_white.png')
    save_image(Image.fromarray(np.uint8(~barriers)*255),job/'fill_barriers.png')
    np.savez_compressed(job/'coloring_arrays.npz',ink=ink,barriers=barriers,regions=labels)
    write_json(job/'source_palette.json',palette)
    swatches=Image.new('RGB',(700,40*len(palette['palettes'])+20),'white')
    draw=ImageDraw.Draw(swatches)
    for i,(key,value) in enumerate(palette['palettes'].items()):
        draw.rectangle((10,10+i*40,65,40+i*40),fill=tuple(value['rgb']))
        draw.text((80,20+i*40),key,fill='black')
    save_image(swatches,job/'palette_review.png')
    config=dict(schema=1,pipeline='coloring',lineart_source=str(lineart),lineart_hash=digest(lineart),
                threshold=threshold,gap_close=gap_close,min_area=min_area,
                palette_hash=digest(job/'source_palette.json'),arrays_hash=digest(job/'coloring_arrays.npz'))
    write_json(job/'coloring_job.json',config)
    white=np.repeat((~ink)[...,None],3,axis=2).astype(np.float32)
    records=region_sheets(white,labels,job,'coloring_regions')
    for r in records:
        region=(labels==r['number'])&~barriers
        y,x=ndi.maximum_position(ndi.distance_transform_edt(region))
        r['seed_xy']=[int(x),int(y)]
        r['touches_border']=bool(region[0].any() or region[-1].any() or region[:,0].any() or region[:,-1].any())
    write_json(job/'coloring_regions.json',records)
    plan=dict(pipeline='coloring',classification_source='unreviewed',
        extraction_hash=config['arrays_hash'],parts=[],background_rgb=[255,255,255],lighting=None)
    write_json(job/'semantic_plan.json',plan)
    event(job,'coloring_semantic_review','領域一覧を確認し参照パレットと意味パーツを割当',
        next_action='coloring_regions_*.pngを確認しsemantic_planへregionsまたはseedsを記入')
    return dict(job=str(job),regions=len(records),palettes=list(palette['palettes']),
                binary=str(job/'lineart_binary.png'),transparent=str(job/'lineart_rgba.png'))


def load_job(job):
    job=Path(job).resolve();config=read_json(job/'coloring_job.json')
    if digest(config['lineart_source'])!=config['lineart_hash']:
        raise ValueError('Input lineart changed; use a new job')
    for name,key in [('source_palette.json','palette_hash'),('coloring_arrays.npz','arrays_hash')]:
        if digest(job/name)!=config[key]:raise ValueError(f'Coloring input changed: {name}')
    palette=read_json(job/'source_palette.json')
    if digest(palette['psd_path'])!=palette['psd_hash']:
        raise ValueError('Source PSD changed; prepare a new coloring job')
    if 'reference_path' in palette:
        if digest(palette['reference_path'])!=palette['reference_hash']:
            raise ValueError('Source reference changed; prepare a new coloring job')
    elif digest(Path(palette['source_job'])/'semantic_plan.json')!=palette['semantic_plan_hash']:
        raise ValueError('Source plan changed; prepare a new coloring job')
    plan=read_json(job/'semantic_plan.json')
    if plan.get('pipeline')!='coloring' or plan.get('extraction_hash')!=config['arrays_hash']:
        raise ValueError('Plan does not match coloring regions')
    with np.load(job/'coloring_arrays.npz') as a:arrays={k:a[k] for k in a.files}
    return job,config,palette,plan,arrays


def seed_region(arrays,x,y):
    regions=arrays['regions']
    if not isinstance(x,int) or not isinstance(y,int) or not 0<=x<regions.shape[1] or not 0<=y<regions.shape[0]:
        raise ValueError('Seed outside lineart')
    if arrays['barriers'][y,x]:raise ValueError('Seed is on an ink/fill barrier; choose inside an area')
    return int(regions[y,x])


def split_color_region(job,region,gap_close=16):
    """Retry gap closure inside one reviewed region; all other IDs stay stable."""
    job,config,palette,plan,arrays=load_job(job)
    scope=arrays['regions']==region
    if not scope.any():raise ValueError('Unknown region ID')
    # The scope boundary is an additional barrier for this local operation.
    local,barriers=enclosed_regions(arrays['barriers']|~scope,gap_close,config['min_area'])
    ids,counts=np.unique(local[scope],return_counts=True)
    if len(ids)<2:raise ValueError('No additional enclosed region; inspect the open boundary')
    ordering=ids[np.argsort(-counts)]
    new_ids=[region]+list(range(int(arrays['regions'].max())+1,int(arrays['regions'].max())+len(ids)))
    original=arrays['regions'].copy()
    for old,new in zip(ordering,new_ids):arrays['regions'][scope&(local==old)]=new
    arrays['barriers']|=barriers&scope
    atomic_write(job/'history'/f'arrays_{config["arrays_hash"][:12]}.npz',(job/'coloring_arrays.npz').read_bytes())
    atomic_write(job/'history'/f'plan_{digest(job/"semantic_plan.json")[:12]}.json',(job/'semantic_plan.json').read_bytes())
    np.savez_compressed(job/'coloring_arrays.npz',**arrays)
    config['arrays_hash']=digest(job/'coloring_arrays.npz')
    config.setdefault('local_gap_repairs',[]).append(dict(region=region,gap_close=gap_close,new_ids=new_ids))
    plan.update(extraction_hash=config['arrays_hash'],classification_source='partial_review',lighting=None)
    write_json(job/'coloring_job.json',config);write_json(job/'semantic_plan.json',plan)
    save_image(Image.fromarray(np.uint8(~arrays['barriers'])*255),job/'fill_barriers.png')
    white=np.repeat((~arrays['ink'])[...,None],3,axis=2).astype(np.float32)
    records=region_sheets(white,np.where(scope,arrays['regions'],0),job,f'coloring_split_{region}')
    for r in records:
        mask=(arrays['regions']==r['number'])&~arrays['barriers']
        y,x=ndi.maximum_position(ndi.distance_transform_edt(mask));r['seed_xy']=[int(x),int(y)]
    write_json(job/f'coloring_split_{region}.json',records)
    assert np.array_equal(original[~scope],arrays['regions'][~scope])
    event(job,'coloring_semantic_review','対象領域だけ隙間補助を強めて再分割',next_action=f'coloring_split_{region}_*.pngを確認し新IDの意味を割当')
    return dict(job=str(job),new_regions=records)


def assigned_labels(plan,arrays,palette,complete=True):
    parts=plan['parts'];names=[p['semantic_id'] for p in parts]
    titles=[p.get('display_name',p['semantic_id']) for p in parts]
    if len(set(names))!=len(names) or len(set(titles))!=len(titles):raise ValueError('Unique part IDs and display names required')
    if complete and names.count('background')!=1:raise ValueError('Exactly one background part required')
    labels=np.zeros_like(arrays['regions']);used=set()
    for i,part in enumerate(parts,1):
        if part['semantic_id']!='background' and part.get('palette_id') not in palette['palettes']:
            raise ValueError('Unknown source palette')
        ids=list(part.get('regions',[]))
        ids.extend(seed_region(arrays,*seed) for seed in part.get('seeds',[]))
        if not ids or len(ids)!=len(set(ids)):raise ValueError('Empty or duplicate part region assignment')
        if any(not isinstance(n,int) or n<1 or n>arrays['regions'].max() for n in ids):raise ValueError('Unknown region ID')
        if used.intersection(ids):raise ValueError('A region cannot belong to two parts')
        used.update(ids);labels[np.isin(arrays['regions'],ids)]=i
    if complete and np.any(labels==0):raise ValueError('Unassigned regions; inspect every coloring region')
    return labels


def paint_preview(job,plan,arrays,palette,complete):
    labels=assigned_labels(plan,arrays,palette,complete)
    background=plan.get('background_rgb',[255,255,255])
    if not isinstance(background,list) or len(background)!=3 or any(not isinstance(c,int) or not 0<=c<=255 for c in background):
        raise ValueError('background_rgb must contain three RGB integers in 0..255')
    lut=np.full((len(plan['parts'])+1,3),.65,np.float32)
    for i,p in enumerate(plan['parts'],1):
        lut[i]=np.array(background if p['semantic_id']=='background' else palette['palettes'][p['palette_id']]['rgb'])/255
    flat=lut[labels];flat[arrays['ink']]=0
    save_image(image_u8(flat),job/'flat_preview.png')
    bg=next((i for i,p in enumerate(plan['parts'],1) if p['semantic_id']=='background'),None)
    if bg is not None:
        rgba=np.zeros((*labels.shape,4),np.uint8)
        rgba[...,:3]=np.round(lut[labels]*255).astype(np.uint8);rgba[...,3]=(labels==bg)*255
        save_image(Image.fromarray(rgba),job/'background_only.png')
    region_sheets(flat,labels,job,'semantic_review',{i:p['semantic_id'] for i,p in enumerate(plan['parts'],1)})
    return labels,flat,lut


def fill_region(job,x,y,to,palette_id=None,display_name=None):
    job,_,palette,plan,arrays=load_job(job)
    n=seed_region(arrays,x,y)
    part=next((p for p in plan['parts'] if p['semantic_id']==to),None)
    if part is None:
        part=dict(semantic_id=to,display_name=display_name or to,palette_id=palette_id,regions=[],group_path=['着色'])
        plan['parts'].append(part)
    elif palette_id is not None and part.get('palette_id')!=palette_id:
        raise ValueError('Existing part uses another palette')
    part.setdefault('regions',[]).append(n)
    # Validate before saving. Reassignments must explicitly remove the old owner.
    assigned_labels(plan,arrays,palette,False)
    atomic_write(job/'history'/f'plan_{digest(job/"semantic_plan.json")[:12]}.json',(job/'semantic_plan.json').read_bytes())
    plan['classification_source']='partial_review';plan['lighting']=None
    write_json(job/'semantic_plan.json',plan)
    paint_preview(job,plan,arrays,palette,False)
    event(job,'coloring_semantic_review','座標の閉領域を参照色で着色',next_action='flat_preview.pngで変更範囲を確認')
    return dict(job=str(job),region=n,part=to,preview=str(job/'flat_preview.png'))


def paint_flats(job):
    job,_,palette,plan,arrays=load_job(job)
    labels,flat,_=paint_preview(job,plan,arrays,palette,True)
    if plan.get('classification_source')!='astra_reviewed':raise ValueError('Review all region assignments before paint-flats')
    np.save(job/'semantic_labels.npy',labels)
    save_image(image_u8(flat),job/'reference.png')
    write_json(job/'flat_context.json',dict(plan_hash=digest(job/'semantic_plan.json'),flat_hash=digest(job/'flat_preview.png')))
    event(job,'coloring_flats_ready','参照PSDのBase色で下塗り完了',next_action='必要ならflat_preview.png全体のAI照明ガイドを作りprepare-lighting')
    return dict(job=str(job),flat=str(job/'flat_preview.png'),parts=len(plan['parts']))


def prepare_lighting(job,image,max_shift=12):
    job,_,palette,plan,arrays=load_job(job)
    if not isinstance(max_shift,int) or not 0<=max_shift<=32:raise ValueError('max_shift must be 0..32')
    context=read_json(job/'flat_context.json')
    if context['plan_hash']!=digest(job/'semantic_plan.json') or context['flat_hash']!=digest(job/'flat_preview.png'):
        raise ValueError('Flats are stale; run paint-flats after reviewing the changed plan')
    with Image.open(image) as source:
        size=list(source.size)
        guide=np.asarray(source.convert('RGB').resize((arrays['ink'].shape[1],arrays['ink'].shape[0])),np.float32)/255
    target=np.uint8(arrays['ink'])*255
    edges=cv2.Canny(np.uint8(guide*255),60,130)
    flow=cv2.calcOpticalFlowFarneback(cv2.GaussianBlur(target,(0,0),1.2),cv2.GaussianBlur(edges,(0,0),1.2),None,.5,5,31,5,7,1.5,0)
    flow=np.clip(flow,-max_shift,max_shift)
    yy,xx=np.mgrid[:target.shape[0],:target.shape[1]].astype(np.float32)
    aligned=cv2.remap(guide,xx+flow[...,0],yy+flow[...,1],cv2.INTER_LINEAR,borderMode=cv2.BORDER_REPLICATE)
    save_image(image_u8(guide),job/'lighting_before.png');save_image(image_u8(aligned),job/'lighting_aligned.png')
    from .post_review import sheet
    sheet([('Input lineart',Image.open(job/'lineart_binary.png')),('Guide before',image_u8(guide)),('Guide aligned',image_u8(aligned))],job/'alignment_comparison.png')
    distance=ndi.distance_transform_edt(~arrays['ink'])
    def score(rgb):
        e=cv2.Canny(np.uint8(rgb*255),60,130)>0
        return float(distance[e].mean()) if e.any() else None
    stats=dict(original_size=size,target_size=[target.shape[1],target.shape[0]],
        guide_edge_distance_before=score(guide),guide_edge_distance_after=score(aligned),max_shift=max_shift,
        input_lineart_warped=False,median_flow=float(np.median(np.linalg.norm(flow,axis=2))))
    write_json(job/'lighting_alignment.json',stats)
    plan['lighting']=dict(reviewed=False,guide_path=str(Path(image).resolve()),guide_hash=digest(image),
        aligned_hash=digest(job/'lighting_aligned.png'),flat_hash=context['flat_hash'],
        notes='',strength=1.0,temperature='neutral')
    write_json(job/'semantic_plan.json',plan)
    event(job,'coloring_lighting_review','照明ガイドを入力線画に位置合わせ。元の線・パーツは固定',next_action='alignment_comparisonを目視しlighting.reviewedとnotesを記入')
    return dict(job=str(job),alignment=stats,next_action='Review alignment; set lighting.reviewed=true and notes before build-colored')


def build_colored(job,output=None):
    job,config,palette,plan,arrays=load_job(job)
    if plan.get('classification_source')!='astra_reviewed':raise ValueError('Coloring requires visually reviewed assignments')
    labels,flat,lut=paint_preview(job,plan,arrays,palette,True)
    lighting=plan.get('lighting');guide=None
    if lighting:
        if lighting.get('reviewed') is not True or not str(lighting.get('notes','')).strip():raise ValueError('Review lighting alignment and record notes first')
        if digest(lighting['guide_path'])!=lighting['guide_hash'] or digest(job/'lighting_aligned.png')!=lighting['aligned_hash']:
            raise ValueError('Lighting guide changed')
        if digest(job/'flat_preview.png')!=lighting['flat_hash']:raise ValueError('Coloring changed; regenerate/review lighting')
        guide=np.asarray(Image.open(job/'lighting_aligned.png').convert('RGB'),np.float32)/255
    output=Path(output or job/'output.psd').resolve()
    protected=[palette['psd_path'],config['lineart_source']]
    if 'reference_path' in palette:protected.append(palette['reference_path'])
    if output in [Path(path).resolve() for path in protected]:raise ValueError('Never overwrite source inputs')
    event(job,'coloring_build','下塗り・影・光・固定線画のPSDを構築',psd=str(output))
    layers=[];expected=lut[labels].copy()
    def add(name,role,color,alpha,path,blend='normal'):
        mask=alpha>0
        if not mask.any():return
        bbox=bbox_of(mask);rgba=np.zeros((*alpha.shape,4),np.uint8)
        rgba[...,:3]=np.round(np.clip(color,0,1)*255).astype(np.uint8)
        rgba[...,3]=np.round(np.clip(alpha,0,1)*255).astype(np.uint8)
        image_path=f'colored_layers/{len(layers):03d}.png'
        save_image(Image.fromarray(rgba).crop(bbox),job/image_path)
        layers.append(dict(name=name,group=role,image_path=image_path,bbox=bbox,blend_mode=blend,opacity=255,group_path=path))
    strength=lighting.get('strength',1) if lighting else 0
    temperature=lighting.get('temperature','neutral') if lighting else 'neutral'
    if not isinstance(strength,(int,float)) or not np.isfinite(strength) or not 0<=strength<=2:raise ValueError('Lighting strength must be 0..2')
    if temperature not in LIGHTING:raise ValueError('Unknown lighting temperature')
    shadow,highlight=[np.asarray(c,np.float32) for c in LIGHTING[temperature]]
    weights=np.array([.2126,.7152,.0722])
    for i,part in enumerate(plan['parts'],1):
        mask=labels==i;title=part.get('display_name',part['semantic_id']);base=lut[i]
        path=part.get('group_path',['着色'])+[f'{title} [{part["semantic_id"]}]']
        if part['semantic_id']=='background':
            add(title+'_Base','Background',base,mask.astype(np.float32),['背景']);continue
        add(title+'_Base','Base',base,mask.astype(np.float32),path)
        if guide is not None:
            # Remove line darkness before inferring lighting. RGB hues of the AI
            # guide are never copied: only luminance modulates neutral lighting.
            interior=mask & ~ndi.binary_dilation(arrays['ink'],iterations=2)
            if not interior.any():interior=mask
            nearest=ndi.distance_transform_edt(~interior,return_distances=False,return_indices=True)
            luminance=guide@weights
            target_luma=luminance[tuple(nearest)]
            base_luma=float(base@weights)
            dark_capacity=max(float((base*(1-shadow))@weights),1e-6)
            light_capacity=max(float(((1-base)*highlight)@weights),1e-6)
            sa=np.clip((base_luma-target_luma)/dark_capacity*strength,0,1)*mask
            ha=np.clip((target_luma-base_luma)/light_capacity*strength,0,1)*mask
            add(title+'_Shadow','Shadows',shadow,sa,path,'multiply')
            add(title+'_Highlight','Highlights',highlight,ha,path,'screen')
            expected=expected*(1-sa[...,None]+shadow*sa[...,None])
            expected=expected+(1-expected)*highlight*ha[...,None]
    add('Lineart_入力線画','Lineart',np.zeros(3),arrays['ink'].astype(np.float32),['線画'])
    expected[arrays['ink']]=0
    maximum=plan.get('max_pixel_layers',3*(len(plan['parts'])-1)+2)
    if not isinstance(maximum,int) or maximum<1 or len(layers)>maximum:raise ValueError('Drawing layer budget exceeded')
    save_image(image_u8(expected),job/'coloring_target.png')
    save_image(image_u8(flat),job/'reference.png');np.save(job/'semantic_labels.npy',labels)
    manifest=dict(pipeline='coloring',hierarchy='parts',size=[labels.shape[1],labels.shape[0]],layers=layers,
        semantic_plan_hash=digest(job/'semantic_plan.json'),input_lineart_hash=config['lineart_hash'],
        source_psd_hash=palette['psd_hash'],target_hash=digest(job/'coloring_target.png'))
    export_psd(job,manifest,output)
    manifest.update(psd_path=str(output),psd_hash=digest(output));write_json(job/'layers.json',manifest)
    actual=render_psd(output,generated=True);save_image(actual,job/'reconstruction.png')
    difference=np.abs(np.asarray(actual,np.float32)-np.asarray(image_u8(expected),np.float32))
    save_image(image_u8(np.clip(difference*8/255,0,1)),job/'diff.png')
    passed=bool(difference.mean()<=.5 and difference.max()<=3)
    analysis=dict(passed=passed,psd_hash=manifest['psd_hash'],pipeline='coloring',
        metrics=dict(mae=float(difference.mean()),max_channel_error=float(difference.max())),
        evaluation_reference='Python coloring target, not the source character pose or AI pixels',
        palette_preserved=True,input_ink_preserved=True,pixel_layers=len(layers),folders=count_groups(layers))
    write_json(job/'analysis.json',analysis)
    for name in ('analysis.json','source_palette.json','lineart_binary.png','lineart_rgba.png'):
        if output.parent!=job:atomic_write(output.parent/name,(job/name).read_bytes())
    save_image(actual,output.parent/'preview.png')
    event(job,'post_review_required','着色PSD保存・読み戻し検証済み。Astra事後確認待ち',
        passed=passed,metrics=analysis['metrics'],next_action='post-review→全比較画像確認→finish-review',
        pixel_layers=len(layers),total_layers=len(layers)+count_groups(layers))
    return dict(job=str(job),psd=str(output),**analysis)
