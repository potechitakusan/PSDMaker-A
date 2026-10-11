"""Read back the two new-Base coloring PSDs, verify masks/ink and compare tones."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
from PIL import Image,ImageDraw
from psd_tools import PSDImage
from skimage.color import rgb2lab,deltaE_ciede2000
from anime_layer_agent.storage import read_json,write_json,digest,save_image
from anime_layer_agent.coloring_reference import solid_rgb
from anime_layer_agent.tone_review import summarize,ANCHOR_BANDS
from anime_layer_agent.post_review import sheet


def main():
    dest=Path('output/new_base_coloring_validation');dest.mkdir(exist_ok=True,parents=True)
    source=PSDImage.open('output/base_color_selection_example/output.psd')
    source_layers={l.name:l for l in source.descendants() if not l.is_group()}
    records={};images={};tone_records={}
    for name in ('new_base_imagegen','new_base_normals'):
        job=Path('work')/name;plan=read_json(job/'semantic_plan.json');manifest=read_json(job/'layers.json')
        psd=PSDImage.open(manifest['psd_path']);current={l.name:l for l in psd.descendants() if not l.is_group()}
        labels=np.load(job/'semantic_labels.npy');arrays=np.load(job/'coloring_arrays.npz')
        for i,p in enumerate(plan['parts'],1):
            if p['semantic_id']=='background':continue
            key=p['display_name']+'_Base';a=source_layers[key];b=current[key]
            assert a.bbox==b.bbox
            aa=np.asarray(a.topil().convert('RGBA'));bb=np.asarray(b.topil().convert('RGBA'))
            assert np.array_equal(aa[...,3],bb[...,3]) and solid_rgb(a)==solid_rgb(b)
            for role in ('Shadow','Highlight'):
                layer=current.get(p['display_name']+'_'+role)
                if layer is None:continue
                x,y,r,bot=layer.bbox;alpha=np.asarray(layer.topil().convert('RGBA'))[...,3]>0
                assert np.all(labels[y:bot,x:r][alpha]==i)
        line=current['Lineart_入力線画'];x,y,r,b=line.bbox
        ink=np.zeros(labels.shape,bool);ink[y:b,x:r]=np.asarray(line.topil().convert('RGBA'))[...,3]>0
        assert np.array_equal(ink,arrays['ink'])
        palette=read_json(job/'source_palette.json')
        assert digest(palette['psd_path'])==palette['psd_hash'] and digest(palette['reference_path'])==palette['reference_hash']
        before=Image.open(job/'source_appearance.png').convert('RGB')
        after=Image.open(job/'reconstruction.png').convert('RGB');images[name]=after
        clean=~arrays['ink']
        tones={}
        for key in palette['palettes']:
            mask=clean & np.isin(labels,[i for i,p in enumerate(plan['parts'],1) if p.get('palette_id')==key])
            a=summarize(np.asarray(before),mask,ANCHOR_BANDS);b=summarize(np.asarray(after),mask,ANCHOR_BANDS)
            if a['status']!='measured' or b['status']!='measured':continue
            errors={}
            for band in a['bands']:
                labs=rgb2lab(np.array([a['bands'][band]['rgb'],b['bands'][band]['rgb']]).reshape(1,2,3)/255)[0]
                errors[band]=dict(deltaE00=float(deltaE_ciede2000(labs[0],labs[1])),delta_L=float(labs[1,0]-labs[0,0]))
            tones[key]=dict(source=a,current=b,errors=errors,mean_deltaE00=float(np.mean([v['deltaE00'] for v in errors.values()])))
        tone_records[name]=tones
        records[name]=dict(analysis=read_json(job/'analysis.json'),
            all_35_Base_RGB_and_alpha_preserved=True,input_line_preserved=True,lighting_outside_mask_pixels=0,
            source_PSD_and_reference_unchanged=True)
    write_json(dest/'verification.json',records);write_json(dest/'material_tones.json',tone_records)
    sheet([('New source finished PSD',Image.open('work/new_base_imagegen/source_appearance.png')),
           ('New inherited Base + line',Image.open('work/new_base_imagegen/flat_preview.png')),
           ('Fresh built-in imagegen guide',Image.open('work/new_base_imagegen/lighting_guide.png')),
           ('Imagegen-derived editable PSD',images['new_base_imagegen']),
           ('Normal-derived editable PSD / preset 1',images['new_base_normals']),
           ('Previous coloring example',Image.open('work/normal_input/existing_preview.png'))],dest/'comparison.png')
    print({name:{k:round(t['mean_deltaE00'],2) for k,t in values.items() if k in ('hair_pink','white_fabric','skin','black_fabric')}
          for name,values in tone_records.items()})


if __name__=='__main__':main()
