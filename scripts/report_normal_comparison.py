"""Read back all experiment PSDs, verify invariants, save comparison evidence."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
from PIL import Image
from psd_tools import PSDImage
from anime_layer_agent.storage import read_json,write_json,save_image,digest
from anime_layer_agent.post_review import sheet


def main():
    destination=Path('output/normal_validation');destination.mkdir(parents=True,exist_ok=True)
    original=PSDImage.open('examples/coloring/output.psd')
    source={l.name:l for l in original.descendants() if not l.is_group()}
    protected=[l for l in source.values() if l.name.endswith('_Base') or l.name.startswith('Lineart')]
    names=['normals_left','normals_right','appearance_normals_left','appearance_normals_right']
    records={};images={}
    for name in names:
        job=Path('work')/name;manifest=read_json(job/'layers.json')
        psd=PSDImage.open(manifest['psd_path'])
        current={l.name:l for l in psd.descendants() if not l.is_group()}
        for l in protected:
            other=current[l.name]
            a=np.asarray(l.topil().convert('RGBA'));b=np.asarray(other.topil().convert('RGBA'))
            assert l.bbox==other.bbox and np.array_equal(a[...,3],b[...,3]),l.name
            assert np.array_equal(a[a[...,3]>0],b[b[...,3]>0]),l.name
        labels=np.load(job/'semantic_labels.npy')
        plan=read_json(job/'semantic_plan.json')
        for i,part in enumerate(plan['parts'],1):
            for role in ('Shadow','Highlight'):
                l=current.get(part['display_name']+'_'+role)
                if l is None:continue
                x,y,r,b=l.bbox;alpha=np.asarray(l.topil().convert('RGBA'))[...,3]>0
                assert np.all(labels[y:b,x:r][alpha]==i),l.name
        images[name]=Image.open(job/'reconstruction.png').convert('RGB')
        records[name]=read_json(job/'analysis.json')|dict(
            base_masks_and_rgba_preserved=True,line_layer_preserved=True,lighting_outside_mask_pixels=0)
    records['source_unchanged']=digest('examples/coloring/output.psd')==read_json('work/normal_input/preservation.json')['source_hash']
    for prefix in ('normals','appearance_normals'):
        a=np.asarray(images[prefix+'_left'],float);b=np.asarray(images[prefix+'_right'],float)
        labels=np.load(Path('work')/(prefix+'_left')/'semantic_labels.npy')
        parts=read_json(Path('work')/(prefix+'_left')/'semantic_plan.json')['parts']
        delta=np.abs(a-b)
        records[prefix+'_direction_response']={p['semantic_id']:dict(
            mean_rgb_delta=float(delta[labels==i].mean()),
            percent_pixels_over_3=float((delta.max(-1)[labels==i]>3).mean()*100))
            for i,p in enumerate(parts,1) if p['semantic_id']!='background'}
        save_image(Image.fromarray(np.uint8(np.clip(delta*8,0,255))),destination/(prefix+'_direction_delta_x8.png'))
    panel=[('Existing AI-guide result',Image.open('work/normal_input/existing_preview.png')),
           ('Preserved flats + ink',Image.open('work/normals_left/flat_preview.png')),
           ('Flat-input normals / upper left',images['normals_left']),
           ('Flat-input normals / upper right',images['normals_right']),
           ('Shaded-input normals / upper left',images['appearance_normals_left']),
           ('Shaded-input normals / upper right',images['appearance_normals_right'])]
    sheet(panel,destination/'comparison.png')
    # Crop from existing face/hair Base bboxes; no manually painted geometry.
    face=next(l for l in protected if '[skin_face]' in l.parent.name)
    hair=next(l for l in protected if '[hair_front]' in l.parent.name)
    bbox=(min(face.left,hair.left),min(face.top,hair.top),max(face.right,hair.right),max(face.bottom,hair.bottom))
    crops=Image.new('RGB',((bbox[2]-bbox[0])*3,(bbox[3]-bbox[1]+30)*2),'white')
    from PIL import ImageDraw
    draw=ImageDraw.Draw(crops)
    for i,(title,picture) in enumerate(panel):
        x=(i%3)*(bbox[2]-bbox[0]);y=(i//3)*(bbox[3]-bbox[1]+30)
        draw.text((x+5,y+5),title,fill='black');crops.paste(picture.crop(bbox),(x,y+30))
    save_image(crops,destination/'face_hair_100percent.png')
    write_json(destination/'verification.json',records)
    print({k:(v['pixel_layers'],v['metrics'],v.get('build_elapsed_seconds')) for k,v in records.items() if k in names})


if __name__=='__main__':main()
