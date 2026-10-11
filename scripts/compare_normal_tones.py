"""Same-pose development benchmark; AI RGB is measured, never used for PSD."""
from pathlib import Path
import shutil
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage as ndi
from skimage.color import rgb2lab, deltaE_ciede2000
from psd_tools import PSDImage
from anime_layer_agent.coloring import load_job, paint_flats, paint_preview, material_lighting, build_colored, event
from anime_layer_agent.normal_lighting import normal_dot, normal_alphas
from anime_layer_agent.storage import read_json, write_json, save_image
from anime_layer_agent.storage import digest
from anime_layer_agent.tone_review import summarize

DEST=Path('output/normal_tone_validation')
JOB=Path('work/normal_tones_calibrated')
SOURCE=Path('work/appearance_normals_left')
BANDS={'shadow':(3,15),'midtone':(40,60),'light':(60,80),'bright':(80,95),'peak':(95,99.5)}


def setup():
    if (JOB/'layers.json').exists():raise ValueError('Benchmark already built; do not overwrite finished job')
    for name in ('coloring_job.json','coloring_arrays.npz','source_palette.json','semantic_plan.json',
                 'lineart_binary.png','lineart_rgba.png','editing_lineart_white.png',
                 'lighting_aligned.png','lighting_before.png','lighting_alignment.json'):
        shutil.copy2(SOURCE/name,JOB/name)
    plan=read_json(JOB/'semantic_plan.json')
    plan['lighting'].update(tone_mapping='source_ramp',normal_curve=None,
        reviewed=True,notes='Existing same-grid shaded-input normals visually inspected: hair curvature, nose, cloth folds; ink grooves remain. Palette-local monotone diffuse rank curve uses original extracted RGB tones, not AI image pixels. No cast shadows/reflections.')
    write_json(JOB/'semantic_plan.json',plan)
    paint_flats(JOB)
    event(JOB,'normal_tone_calibration','元絵の5色帯と法線明暗を素材別に対応。Baseと線を固定')


def curve_preview():
    _,_,palette,plan,arrays=load_job(SOURCE)
    labels,flat,lut=paint_preview(SOURCE,plan,arrays,palette,True)
    normals=np.load(plan['lighting']['normal_path']);out=flat.copy()
    curve=dict(shadow_gain=2,highlight_gain=2,gamma=.6)
    for i,part in enumerate(plan['parts'],1):
        if part['semantic_id']=='background':continue
        mask=labels==i;inner=mask & ~ndi.binary_dilation(arrays['ink'],iterations=2)
        if not inner.any():inner=mask
        ix=ndi.distance_transform_edt(~inner,return_distances=False,return_indices=True)
        sa,ha=normal_alphas(normals[tuple(ix)],plan['lighting']['light'],curve=curve)
        s,h,_=material_lighting(lut[i],palette['palettes'][part['palette_id']].get('tones'),([.3]*3,[.7]*3))
        rgb=lut[i]*(1-sa[...,None]+s*sa[...,None]);rgb+= (1-rgb)*h*ha[...,None]
        out[mask]=rgb[mask]
    out[arrays['ink']]=0
    save_image(Image.fromarray(np.uint8(np.round(np.clip(out,0,1)*255))),DEST/'curve_only.png')
    return curve


def report():
    _,_,palette,plan,arrays=load_job(JOB)
    labels=np.load(JOB/'semantic_labels.npy');clean=~ndi.binary_dilation(arrays['ink'],iterations=5)
    paths={'Flat':SOURCE/'flat_preview.png','Legacy':SOURCE/'reconstruction.png',
           'Curve':DEST/'curve_only.png','Calibrated':JOB/'reconstruction.png',
           'Imagegen':DEST/'imagegen_reference.png'}
    images={k:Image.open(p).convert('RGB') for k,p in paths.items()}
    if any(im.size!=images['Flat'].size for im in images.values()):
        raise ValueError('AI benchmark size differs; visually check resampling separately')
    records={};swatches=Image.new('RGB',(1000,len(palette['palettes'])*160),'#eee')
    draw=ImageDraw.Draw(swatches)
    for row,(key,entry) in enumerate(palette['palettes'].items()):
        mask=clean & np.isin(labels,[i for i,p in enumerate(plan['parts'],1) if p.get('palette_id')==key])
        source=entry.get('tones',{}).get('bands',{});rec={'base_rgb':entry['rgb'],'original':source,'methods':{}}
        if not source:continue
        draw.text((5,row*160+2),key,fill='black')
        methods={'Original':source}
        for name,img in images.items():
            measured=summarize(np.asarray(img),mask,bands=[(k,*v) for k,v in BANDS.items()])
            if measured['status']!='measured':continue
            methods[name]=measured['bands']
            errors={}
            for b,v in measured['bands'].items():
                if b not in source:continue
                labs=rgb2lab(np.array([v['rgb'],source[b]['rgb']]).reshape(1,2,3)/255)[0]
                errors[b]=dict(deltaE=float(deltaE_ciede2000(labs[0],labs[1])),delta_L=float(labs[0,0]-labs[1,0]))
            rec['methods'][name]=dict(bands=measured['bands'],errors=errors,
                mean_deltaE=float(np.mean([v['deltaE'] for v in errors.values()])))
        records[key]=rec
        for col,(name,bands) in enumerate(methods.items()):
            x=col*166;draw.text((x+5,row*160+20),name,fill='black')
            for j,(b,_) in enumerate(BANDS.items()):
                if b in bands:
                    rgb=tuple(np.uint8(np.clip(np.round(bands[b]['rgb']),0,255)))
                    draw.rectangle((x+j*32+4,row*160+40,x+j*32+33,row*160+100),fill=rgb)
                    draw.text((x+j*32+4,row*160+105),b[:3],fill='black')
    save_image(swatches,DEST/'tone_swatches.png');write_json(DEST/'tone_comparison.json',records)
    sheet=Image.new('RGB',(360*3,570*2),'#eee');d=ImageDraw.Draw(sheet)
    panels=[('Reference source',Image.open('examples/reference.jpg'))]+list(images.items())
    for i,(name,img) in enumerate(panels):
        x=i%3*360;y=i//3*570;d.text((x+6,y+6),name,fill='black')
        pic=img.copy();pic.thumbnail((350,530));sheet.paste(pic,(x+5,y+30))
    save_image(sheet,DEST/'comparison.png')
    diagnostics=read_json(JOB/'analysis.json')['normal_mapping']
    print({k:{name:round(v['mean_deltaE'],2) for name,v in records[k]['methods'].items()}
        for k in ('hair_pink','skin','white_fabric','black_fabric')},flush=True)
    print('Normal fallback:',{k:v for k,v in diagnostics.items() if v['status']!='mapped'},flush=True)


def verify():
    old=PSDImage.open('examples/coloring/output.psd')
    manifest=read_json(JOB/'layers.json');new=PSDImage.open(manifest['psd_path'])
    layers={l.name:l for l in new.descendants() if not l.is_group()}
    protected=[l for l in old.descendants() if not l.is_group() and
               (l.name.endswith('_Base') or l.name.startswith('Lineart'))]
    for l in protected:
        other=layers[l.name];a=np.asarray(l.topil().convert('RGBA'));b=np.asarray(other.topil().convert('RGBA'))
        assert l.bbox==other.bbox and np.array_equal(a[...,3],b[...,3])
        assert np.array_equal(a[a[...,3]>0],b[b[...,3]>0])
    labels=np.load(JOB/'semantic_labels.npy');plan=read_json(JOB/'semantic_plan.json')
    for i,p in enumerate(plan['parts'],1):
        for role in ('Shadow','Highlight'):
            l=layers.get(p['display_name']+'_'+role)
            if l is None:continue
            x,y,r,b=l.bbox;alpha=np.asarray(l.topil().convert('RGBA'))[...,3]>0
            assert np.all(labels[y:b,x:r][alpha]==i)
    assert digest('examples/coloring/output.psd')==read_json('work/normal_input/preservation.json')['source_hash']
    assert digest(plan['lighting']['normal_path'])==plan['lighting']['normal_hash']
    write_json(DEST/'verification.json',dict(protected_layers=len(protected),
        base_and_ink_equal=True,lighting_outside_mask_pixels=0,source_unchanged=True,
        normal_unchanged=True,analysis=read_json(JOB/'analysis.json')))


if __name__=='__main__':
    DEST.mkdir(exist_ok=True,parents=True)
    if '--report-only' not in sys.argv:
        setup();write_json(DEST/'curve_settings.json',curve_preview())
        print(build_colored(JOB,Path('output/normal_tones_calibrated/output.psd')),flush=True)
    report();verify()
