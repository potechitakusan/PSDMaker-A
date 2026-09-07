"""Reference-registered linework and a material/part based editable PSD."""
from pathlib import Path
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage as ndi
from scipy.spatial import cKDTree
from skimage.color import rgb2lab

from .storage import digest, read_json, write_json, save_image, progress, monitor
from .regions import geometry_labels, bbox_of


def image_u8(rgb):
    return Image.fromarray(np.round(np.clip(rgb, 0, 1) * 255).astype(np.uint8))


def align_linework(rgb, guide):
    """Warp a guide locally, then take actual line locations/colors from reference.

    The supplied guide is never painted onto the reference: new geometry in an
    AI-produced guide cannot override the original art. Inpaint only a narrow
    reference ridge mask; solve its RGBA so linework + underpaint reproduces RGB.
    """
    gray = cv2.cvtColor(np.round(rgb * 255).astype(np.uint8), cv2.COLOR_RGB2GRAY)
    closed = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)))
    ridge = np.maximum(closed.astype(np.float32) - gray, 0) / 255
    ref_feature = np.clip(ridge * 6, 0, 1)
    target = np.uint8(cv2.GaussianBlur(ref_feature, (0, 0), 1.2) * 255)
    source = np.uint8(cv2.GaussianBlur(guide, (0, 0), 1.2) * 255)
    flow = cv2.calcOpticalFlowFarneback(target, source, None, 0.5, 5, 31, 6, 7, 1.5, 0)
    flow = np.clip(flow, -16, 16)
    yy, xx = np.mgrid[:gray.shape[0], :gray.shape[1]].astype(np.float32)
    mx, my = xx + flow[..., 0], yy + flow[..., 1]
    warped = cv2.remap(guide, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
    nearby = ndi.binary_dilation(warped > 0.12, iterations=4)
    core = (ridge > 0.018) & nearby
    mask = ndi.binary_dilation(core, iterations=1) & nearby
    underlying = cv2.inpaint(np.round(rgb * 255).astype(np.uint8), mask.astype(np.uint8) * 255, 3, cv2.INPAINT_TELEA).astype(np.float32) / 255
    # The minimum feasible alpha for RGB source-over; both dark and colored ink.
    darker = np.maximum(underlying - rgb, 0) / np.maximum(underlying, 1e-6)
    lighter = np.maximum(rgb - underlying, 0) / np.maximum(1 - underlying, 1e-6)
    alpha = np.clip(np.maximum(darker, lighter).max(2) * 1.03, 0, 1)
    alpha[~mask] = 0
    # Keep weak texture in the paint instead of introducing speckled linework.
    alpha[alpha < .07] = 0
    underlying[alpha == 0] = rgb[alpha == 0]
    ink = np.clip((rgb - underlying * (1 - alpha[..., None])) / np.maximum(alpha[..., None], 1e-6), 0, 1)
    ink[alpha == 0] = 0
    distance = ndi.distance_transform_edt(ridge < 0.025)
    def error(line):
        use = line > 0.2
        return float(distance[use].mean()) if use.any() else 0.0
    stats = {"method": "dense_guide_registration_then_reference_ridges", "guide_distance_before_px": error(guide), "guide_distance_after_warp_px": error(warped), "corrected_line_distance_px": error(alpha), "flow_median_px": float(np.median(np.linalg.norm(flow[guide > 0.2], axis=1))) if np.any(guide > .2) else 0.0, "line_pixels": int(np.count_nonzero(alpha > .01)), "max_recomposition_error": float(np.abs(underlying * (1-alpha[..., None]) + ink * alpha[..., None] - rgb).max())}
    return underlying, ink, alpha, warped, mx, my, stats


def region_sheets(rgb, labels, job, prefix="geometry_review", names=None):
    original = image_u8(rgb)
    font = ImageFont.load_default(size=16)
    records = []
    for n in np.unique(labels):
        if n == 0:
            continue
        mask = labels == n
        records.append({"id": f"G{n:04d}", "number": int(n), "bbox": bbox_of(mask), "pixel_area": int(mask.sum())})
    for page, start in enumerate(range(0, len(records), 36), 1):
        batch = records[start:start+36]
        sheet = Image.new("RGB", (1080, ((len(batch)+5)//6)*170), "#202630")
        draw = ImageDraw.Draw(sheet)
        for i, r in enumerate(batch):
            crop = original.crop(r["bbox"])
            mask = Image.fromarray(np.uint8(labels == r["number"])*255).crop(r["bbox"])
            bg = Image.new("RGB", crop.size, "#697381")
            bg.paste(crop, mask=mask)
            bg.thumbnail((170, 138))
            x, y = i%6*180, i//6*170
            sheet.paste(bg, (x+(180-bg.width)//2, y))
            draw.text((x+3, y+141), names.get(r["number"], r["id"]) if names else r["id"], fill="white", font=font)
        save_image(sheet, job / f"{prefix}_{page:02d}.png")
    return records


def prepare_compact(reference, lineart, job):
    job = Path(job).resolve()
    job.mkdir(parents=True, exist_ok=True)
    config = {"reference_hash": digest(reference), "lineart_hash": digest(lineart), "compact_version": 2}
    if (job / "compact_job.json").exists():
        if read_json(job / "compact_job.json")["config"] != config:
            raise ValueError("Use a fresh job for different compact inputs")
    progress(job, "line_registration", "線画ガイドを局所位置合わせし、元絵の線位置と色を回収中")
    with Image.open(reference) as source:
        rgba = np.asarray(source.convert("RGBA"))
    if np.any(rgba[...,3] != 255):
        raise ValueError("Opaque reference required")
    rgb = rgba[...,:3].astype(np.float32)/255
    with Image.open(lineart) as source:
        lineart_size = list(source.size)
        line = np.asarray(source.convert("RGBA").resize((rgb.shape[1],rgb.shape[0]))).astype(np.float32)/255
    guide = line[...,3] if np.any(line[...,3]<1) else 1-line[...,:3].min(2)
    underlying, ink, alpha, warped, mx, my, stats = align_linework(rgb, guide)
    stats.update(reference_size=[rgb.shape[1],rgb.shape[0]], lineart_original_size=lineart_size,
                 lineart_resized=lineart_size != [rgb.shape[1],rgb.shape[0]])
    original_labels = geometry_labels(guide)
    labels = cv2.remap(original_labels.astype(np.float32), mx, my, cv2.INTER_NEAREST,
                       borderMode=cv2.BORDER_REPLICATE).astype(np.int32)
    labels[labels == 0] = 1
    np.savez_compressed(job / "compact_arrays.npz", rgb=rgb, underlying=underlying, ink=ink, alpha=alpha, guide=guide, warped=warped, geometry=labels)
    save_image(image_u8(rgb), job / "reference.png")
    save_image(image_u8(np.dstack([ink, alpha])), job / "aligned_lineart_rgba.png")
    save_image(image_u8(1-alpha), job / "aligned_lineart_white.png")
    save_image(image_u8(underlying), job / "underpainting.png")
    sheet = Image.new("RGB", (rgb.shape[1]*3, rgb.shape[0]+30), "white")
    draw = ImageDraw.Draw(sheet)
    for i, (title, array) in enumerate((("Input guide", 1-guide), ("Registered guide", 1-warped), ("Reference-aligned linework", 1-alpha))):
        sheet.paste(image_u8(array).convert("RGB"), (i*rgb.shape[1],30))
        draw.text((i*rgb.shape[1]+12,5), title, fill="black")
    save_image(sheet, job / "alignment_comparison.png")
    records = region_sheets(rgb, labels, job)
    write_json(job / "compact_regions.json", {"regions": records})
    write_json(job / "alignment.json", stats)
    write_json(job / "compact_job.json", {"config":config,"reference_source":str(Path(reference).resolve()),"lineart_source":str(Path(lineart).resolve())})
    progress(job, "compact_semantic_review", "線位置補正済み。形状領域を意味別にまとめる判断待ち", next_action="geometry_review_*.pngを見てsemantic_plan.jsonを作成")
    return {"job":str(job),"geometry_regions":len(records),"alignment":stats}


def material_labels(arrays, plan):
    from .regions import cluster_region
    result = np.zeros(arrays['geometry'].shape, np.int32)
    for split in plan.get('material_splits', []):
        gid = int(split['geometry'])
        mask = arrays['geometry'] == gid
        result[mask] = gid * 1000 + cluster_region(arrays['underlying'], mask, split['clusters']) + 1
    return result


def review_materials(job, geometry, clusters=6):
    job=Path(job).resolve()
    if not 1<=clusters<=16: raise ValueError('clusters must be 1..16')
    arrays=np.load(job/'compact_arrays.npz')
    if geometry not in np.unique(arrays['geometry']): raise ValueError('Unknown geometry ID')
    labels=material_labels(arrays,{'material_splits':[{'geometry':geometry,'clusters':clusters}]})
    records=region_sheets(arrays['rgb'],labels,job,f'material_G{geometry:04d}')
    write_json(job/f'material_G{geometry:04d}.json',{'regions':records})
    return {'job':str(job),'material_ids':[r['number'] for r in records],'sheet':str(job/f'material_G{geometry:04d}_01.png')}


def semantic_masks(arrays, plan):
    """Use reviewed IDs as seeds and reference color/edges to refine boundaries."""
    geometry, rgb = arrays['geometry'], arrays['rgb']
    materials = material_labels(arrays, plan)
    parts = plan['parts']
    labels = np.zeros(geometry.shape, np.int32)
    seen_g, seen_m = set(), set()
    for index, part in enumerate(parts, 1):
        gs, ms = part.get('regions', []), part.get('material_clusters', [])
        if seen_g.intersection(gs) or seen_m.intersection(ms):
            raise ValueError('Duplicate semantic region assignment')
        seen_g.update(gs); seen_m.update(ms)
        labels[np.isin(geometry, gs)] = index
        labels[np.isin(materials, ms)] = index
    required_g = set(np.unique(geometry)) - {int(s['geometry']) for s in plan.get('material_splits',[])}
    if seen_g != required_g or seen_m != set(np.unique(materials)) - {0}:
        raise ValueError(f'Incomplete reviewed plan. Missing geometry: {sorted(required_g-seen_g)}, material: {sorted(set(np.unique(materials))-{0}-seen_m)}')
    from .selection import apply_component_assignments
    labels = apply_component_assignments(geometry, materials, labels, plan)
    for index,part in enumerate(parts,1):
        for gid in part.get('keep_largest_in_geometry',[]):
            selected=(labels==index)&(geometry==gid)
            cc,n=ndi.label(selected)
            if n>1:
                sizes=np.bincount(cc.ravel()); sizes[0]=0
                fallback=next(i for i,p in enumerate(parts,1) if p['semantic_id']==part['island_fallback'])
                labels[selected & (cc!=sizes.argmax())]=fallback
    background = next(i for i,p in enumerate(parts,1) if p['semantic_id']=='background')
    lab = rgb2lab(arrays['underlying'])
    # Open linework may connect a foreground patch to the background component.
    # Recover only nonwhite leaked pixels using nearby reviewed material seeds.
    border=np.zeros(labels.shape,bool)
    border[[0,-1],:]=True; border[:,[0,-1]]=True
    border_samples=lab[border & (labels==background)]
    if not len(border_samples): border_samples=lab[labels==background]
    background_lab=np.median(border_samples,axis=0)
    leaked = (labels == background) & (np.linalg.norm(lab-background_lab,axis=2)>12)
    stable = (labels != background) & (arrays['alpha'] < .12)
    yy,xx = np.mgrid[:labels.shape[0],:labels.shape[1]]
    def features(mask):
        return np.column_stack((xx[mask]*.09, yy[mask]*.09, lab[mask]))
    if plan.get('recover_background_leaks', True) and leaked.any() and stable.any():
        sample = np.flatnonzero(stable)[::3]
        sample_mask = np.zeros_like(stable); sample_mask.flat[sample]=True
        tree = cKDTree(features(sample_mask))
        _, idx = tree.query(features(leaked), workers=1)
        labels[leaked] = labels[sample_mask][idx]
    # Smooth only a narrow boundary band by flooding on reference color edges.
    # Eroded cores remain fixed; small isolated details are retained as seeds.
    from skimage.segmentation import watershed
    markers = np.zeros_like(labels)
    for index in range(1,len(parts)+1):
        mask = labels == index
        eroded = ndi.binary_erosion(mask, iterations=2)
        cc,n = ndi.label(mask)
        markers[eroded]=index
        counts=np.bincount(cc[eroded],minlength=n+1)
        missing=np.flatnonzero(counts[1:]==0)+1
        if len(missing):
            positions=ndi.maximum_position(ndi.distance_transform_edt(mask),cc,missing)
            for position in positions:
                markers[position]=index
    gradient = np.zeros(labels.shape,np.float32)
    for channel in range(3):
        gx=ndi.sobel(lab[...,channel],axis=1); gy=ndi.sobel(lab[...,channel],axis=0)
        gradient += np.hypot(gx,gy)
    labels=watershed(gradient,markers,compactness=.01).astype(np.int32)
    # Remove numerical islands from a material without erasing intentional small
    # accessories. Reassign only removed pixels to the nearest retained material.
    cleaned=labels.copy()
    for index,part in enumerate(parts,1):
        mask=labels==index
        cc,n=ndi.label(mask)
        sizes=np.bincount(cc.ravel()); sizes[0]=0
        threshold=part.get('min_island_area',max(12,min(120,int(mask.sum()*.004))))
        keep=sizes>=threshold
        if n and not keep.any(): keep[int(sizes.argmax())]=True
        cleaned[mask & ~keep[cc]]=0
    if np.any(cleaned==0):
        nearest=ndi.distance_transform_edt(cleaned==0,return_distances=False,return_indices=True)
        cleaned[cleaned==0]=cleaned[tuple(nearest[:,cleaned==0])]
    labels=cleaned
    for index,part in enumerate(parts,1):
        derived=part.get('derived')
        if derived:
            source=next(i for i,p in enumerate(parts,1) if p['semantic_id']==derived['from'])
            near=next(i for i,p in enumerate(parts,1) if p['semantic_id']==derived['near'])
            if derived['material']!='neutral_light': raise ValueError('Unknown derived material')
            candidate=(labels==source)&ndi.binary_dilation(labels==near,iterations=derived['radius'])
            original_lab=rgb2lab(rgb)
            candidate&=(np.hypot(original_lab[...,1],original_lab[...,2])<derived.get('max_chroma',7))&(original_lab[...,0]>derived.get('min_lightness',83))
            labels[candidate]=index
    from .selection import apply_selections, recover_background_boundary
    labels = recover_background_boundary(rgb, labels, parts, plan.get('background_cleanup'))
    return apply_selections(rgb, labels, plan)


def decompose_material(target, base_color):
    """Solve per-pixel Multiply and Screen with transparent neutral pixels.

    This retains paint gradients without making one layer per color island.
    Neither layer contains a flattened reference nor an all-image correction.
    """
    base = np.broadcast_to(np.clip(base_color,1/255,254/255),target.shape)
    ratio = np.minimum(target/np.maximum(base,1e-6),1)
    shadow_alpha = np.clip((1-ratio).max(2)/.7,0,1)
    shadow_rgb=1-(1-ratio)/np.maximum(shadow_alpha[...,None],1e-6)
    shaded=base*ratio
    light=np.clip((target-shaded)/np.maximum(1-shaded,1e-6),0,1)
    light_alpha=np.clip(light.max(2)/.7,0,1)
    light_rgb=light/np.maximum(light_alpha[...,None],1e-6)
    return base.copy(), np.clip(shadow_rgb,0,1),shadow_alpha,np.clip(light_rgb,0,1),light_alpha


def build_compact(job, output=None):
    from .composition import export_psd,render_psd
    from .pipeline import evaluate
    job=Path(job).resolve()
    config=read_json(job/'compact_job.json')
    plan=read_json(job/'semantic_plan.json')
    if plan.get('reference_hash',config['config']['reference_hash']) != config['config']['reference_hash']:
        raise ValueError('Semantic plan belongs to another reference')
    if plan.get('classification_source') != 'astra_reviewed':
        raise ValueError('Compact build requires a visually reviewed semantic plan')
    names=[p['semantic_id'] for p in plan['parts']]
    if len(set(names))!=len(names) or names.count('background')!=1:
        raise ValueError('Unique semantic parts and exactly one background are required')
    output=Path(output or read_json(job/'status.json').get('psd',job/'output.psd')).resolve()
    if output.suffix.lower()!='.psd':
        raise ValueError('Output must have .psd extension')
    monitor(output,job)
    progress(job,'compact_masks','意味・素材別のマスクを構築中',psd=str(output))
    arrays=np.load(job/'compact_arrays.npz')
    labels=semantic_masks(arrays,plan)
    np.save(job/'semantic_labels.npy',labels)
    records=region_sheets(arrays['rgb'],labels,job,'semantic_review', {i:p['semantic_id'] for i,p in enumerate(plan['parts'],1)})
    layers=[]; solvers=[]
    target=arrays['underlying']
    artist = plan.get('editing', {}).get('profile') == 'artist'
    if 'editing' in plan and not artist:
        raise ValueError('Unknown editing profile; use configure-editing')
    group_path = None
    paint_target = target.copy()
    paint_delta = 0.0
    if artist:
        from .artist import resolve_settings, decompose_artist, part_group_path, count_groups, background_audit, write_editing_report, relative_color_layers, clean_linework
        line_alpha = arrays['alpha']
        if plan['editing'].get('line_cleanup',False):
            target, line_alpha, line_stats = clean_linework(arrays)
            paint_target = target.copy()
            write_json(job/'line_cleanup.json',line_stats)
        shared_colors, settings = resolve_settings(plan, labels, target, line_alpha)
        audit = background_audit(arrays['rgb'], labels, plan['parts'], job)
    def add(name,group,rgb,alpha,mode='normal'):
        if not (alpha>.003).any():
            return
        x0,y0,x1,y1=bbox_of(alpha>0)
        rgba=np.dstack([rgb[y0:y1,x0:x1],alpha[y0:y1,x0:x1]])
        path=f'compact_layers/{len(layers):03d}.png'
        save_image(image_u8(rgba),job/path)
        layers.append({'name':name,'group':group,'image_path':path,'bbox':[x0,y0,x1,y1],'blend_mode':mode,'opacity':255})
        if artist:
            layers[-1]['group_path'] = group_path
    from .regions import metadata
    evaluation_records=[]
    eval_parts=[]
    for i,part in enumerate(plan['parts'],1):
        mask=labels==i
        if not mask.any():
            raise ValueError(f'Empty semantic part {part["semantic_id"]}')
        title=part.get('display_name',part['semantic_id'])
        if artist:
            group_path = ['背景'] if part['semantic_id'] == 'background' else part_group_path(part)
        evaluation_records.append(metadata(f'G{i:04d}',None,mask,arrays['rgb'],job))
        eval_parts.append({'semantic_id':part['semantic_id'],'geometry_regions':[f'G{i:04d}']})
        if part['semantic_id']=='background':
            add(f'{title}_Base','Background',target,mask.astype(np.float32))
            continue
        if artist:
            base_color = shared_colors[part['semantic_id']]
            b, s, sa, h, ha, detail, da, changed = decompose_artist(
                target[mask][:, None, :], base_color, settings['resolved_color_tolerance'],
                plan['editing'].get('lighting', 'neutral'))
            def full_rgb(values):
                result = np.zeros_like(target)
                result[mask] = values[:, 0]
                return result
            def full_alpha(values):
                result = np.zeros(mask.shape, np.float32)
                result[mask] = values[:, 0]
                return result
            add(f'{title}_Base', 'Base', full_rgb(b), mask.astype(np.float32))
            add(f'{title}_Shadow', 'Shadows', full_rgb(s), full_alpha(sa), 'multiply')
            add(f'{title}_Highlight', 'Highlights', full_rgb(h), full_alpha(ha), 'screen')
            detail_mode = part.get('detail_mode',plan['editing'].get('detail_mode','pigment'))
            if detail_mode == 'relative':
                fitted = b*(1-sa[...,None]+s*sa[...,None])
                fitted = fitted+(1-fitted)*h*ha[...,None]
                cs,csa,ch,cha = relative_color_layers(fitted,changed)
                add(f'{title}_色補正・乗算', 'ColorAdjustments', full_rgb(cs), full_alpha(csa), 'multiply')
                add(f'{title}_色補正・スクリーン', 'ColorAdjustments', full_rgb(ch), full_alpha(cha), 'screen')
            elif detail_mode == 'pigment':
                add(f'{title}_固有色・模様', 'Details', full_rgb(detail), full_alpha(da))
            else:
                raise ValueError('Unknown part detail_mode')
            paint_target[mask] = changed[:, 0]
            paint_delta = max(paint_delta, float(np.linalg.norm(rgb2lab(changed) - rgb2lab(target[mask][:, None, :]), axis=-1).max()))
            solvers.append({'semantic_id':part['semantic_id'], 'base_color':np.clip(base_color,1/255,254/255).tolist(),
                            'palette_id':part.get('palette_id',part['semantic_id']), 'method':'fixed_lighting_with_'+detail_mode,
                            'detail_pixels':int(np.count_nonzero(da>.003))})
            continue
        sample=target[mask & (arrays['alpha']<.12)]
        if not len(sample): sample=target[mask]
        # Dominant Lab color bin avoids averaging mixed highlight/shadow tones.
        sample=sample[::max(1,len(sample)//20000)]
        sample_lab=rgb2lab(sample.reshape(-1,1,3)).reshape(-1,3)
        bins=np.round(sample_lab/8).astype(int)
        unique,inv,counts=np.unique(bins,axis=0,return_inverse=True,return_counts=True)
        dominant=int(counts.argmax())
        base_color=np.median(sample[inv==dominant],axis=0)
        base,shadow,sa,light,ha=decompose_material(target,base_color)
        add(f'{title}_Base','Base',base,mask.astype(np.float32))
        add(f'{title}_Shadow','Shadows',shadow,sa*mask,'multiply')
        add(f'{title}_Highlight','Highlights',light,ha*mask,'screen')
        solvers.append({'semantic_id':part['semantic_id'],'base_color':base_color.tolist(),'method':'spatial_multiply_screen_inverse','shadow_pixels':int(np.count_nonzero(sa*mask>.01)),'highlight_pixels':int(np.count_nonzero(ha*mask>.01))})
    ink = arrays['ink']
    if not artist:
        line_alpha = arrays['alpha']
    if artist:
        group_path = ['線画']
        if plan['editing'].get('lineart', 'monochrome') == 'monochrome':
            gray = np.sum(ink * np.array([.2126, .7152, .0722]), axis=2)
            ink = np.repeat(gray[..., None], 3, axis=2)
        save_image(image_u8(np.dstack([ink, line_alpha])), job/'editing_lineart_rgba.png')
        save_image(image_u8(ink*line_alpha[...,None]+1-line_alpha[...,None]), job/'editing_lineart_white.png')
        intended = paint_target*(1-line_alpha[...,None]) + ink*line_alpha[...,None]
        save_image(image_u8(intended), job/'editing_target.png')
    add('Lineart_モノクロ' if artist and plan['editing'].get('lineart','monochrome')=='monochrome' else 'Lineart_元絵位置補正済み','Lineart',ink,line_alpha)
    order={name:i for i,name in enumerate(('Background','Base','Shadows','Highlights','Lineart'))}
    if not artist:
        layers.sort(key=lambda x:order[x['group']])
    groups = count_groups(layers) if artist else 5
    total=len(layers)+groups
    if artist:
        report = write_editing_report(job, settings, layers, audit, paint_delta)
        if len(layers) > settings['resolved_layer_range'][1]:
            raise ValueError(f"{len(layers)} pixel layers exceeds budget; merge semantic parts or revise requested range")
    elif total>plan.get('max_total_layers',100):
        raise ValueError(f'{total} layers including groups exceeds budget; merge semantic parts')
    # Evaluation metadata for the reviewed semantic partition.
    write_json(job/'regions.json',{'regions':evaluation_records,'geometry_names':{i:f'G{i:04d}' for i in range(1,len(plan['parts'])+1)}})
    np.savez_compressed(job/'labels.npz',geometry=labels)
    write_json(job/'layer_plan.json',{'schema_version':2,'classification_source':'astra_reviewed','parts':eval_parts,'semantic_plan':plan})
    manifest={'size':[target.shape[1],target.shape[0]],'layers':layers,'solvers':solvers,'plan_hash':digest(job/'layer_plan.json'),'semantic_plan_hash':digest(job/'semantic_plan.json'),'pixel_layer_count':len(layers),'total_layer_count':total,'pipeline':'compact'}
    manifest['group_count'] = groups
    if artist:
        manifest.update(hierarchy='parts', editing_target='editing_target.png', editing_target_hash=digest(job/'editing_target.png'),
                        editing_report='editing_report.json', editing_report_hash=digest(job/'editing_report.json'))
    write_json(job/'layers.json',manifest)
    progress(job,'psd_composition',f'{len(layers)}描画レイヤー（グループ込み{total}）を保存中')
    export_psd(job,manifest,output)
    manifest.update(psd_hash=digest(output),psd_path=str(output))
    write_json(job/'layers.json',manifest)
    if artist:
        from .coloring_reference import export_coloring_reference
        export_coloring_reference(job)
    result=evaluate(job)
    result.update(pixel_layers=len(layers),total_layers=total,psd=str(output))
    write_json(job/'compact_result.json',result)
    from .storage import atomic_write
    if output.parent != job:
        for filename in ('aligned_lineart_rgba.png','aligned_lineart_white.png','alignment_comparison.png','semantic_plan.json',
                         *(('editing_report.json','palette_review.png','editing_lineart_rgba.png','editing_lineart_white.png','background_only.png','background_review.png','editing_target.png') if artist else ())):
            atomic_write(output.parent/filename,(job/filename).read_bytes())
    progress(job,('post_review_required' if artist else 'complete') if result['passed'] else 'needs_repair','PSD構築・数値評価済み。事後レビューを実施',pixel_layers=len(layers),total_layers=total,classification_source='astra_reviewed',next_action='post-review --job で色替え・単独表示を生成しPOST_REVIEW_CHECKLIST.mdを確認')
    return result
