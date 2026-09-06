"""Visual editability proof for a compact job, without modifying its PSD."""
import argparse
from pathlib import Path
import sys
import numpy as np
from PIL import Image, ImageDraw
from psd_tools import PSDImage

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from anime_layer_agent.composition import render_generated_psd
from anime_layer_agent.storage import read_json,write_json,save_image


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--job', required=True, help='Path to a compact job directory')
    args=parser.parse_args()
    job=Path(args.job)
    manifest=read_json(job/'layers.json')
    psd=PSDImage.open(manifest['psd_path'])
    pixels = [layer for layer in psd.descendants() if not layer.is_group()]
    items = {item['name']:item for item in manifest['layers']}
    panels=[('Reference',Image.open(job/'reference.png').convert('RGB'))]
    for title,hidden in [('PSD reconstruction',[]),('Shadows OFF',['Shadows']),('Highlights OFF',['Highlights']),('Lineart OFF',['Lineart']),('Base colors + lineart',['Shadows','Highlights','Details','ColorAdjustments'])]:
        for child in pixels:
            child.visible = items[child.name]['group'] not in hidden
        rendered=render_generated_psd(psd)
        white=Image.new('RGBA',rendered.size,'white')
        rendered=Image.alpha_composite(white,rendered).convert('RGB')
        panels.append((title,rendered))
        save_image(rendered,job/('qa_'+title.lower().replace(' ','_')+'.png'))
    sheet=Image.new('RGB',(1080,1260),'#e5e7eb')
    draw=ImageDraw.Draw(sheet)
    for i,(title,picture) in enumerate(panels):
        picture=picture.copy(); picture.thumbnail((350,596))
        x,y=i%3*360,i//3*630
        sheet.paste(picture,(x+(360-picture.width)//2,y+30))
        draw.text((x+12,y+8),title,fill='black')
    save_image(sheet,job/'editability_review.png')
    reference=np.asarray(panels[1][1],dtype=float)
    effects={title:float(np.abs(np.asarray(img,dtype=float)-reference).mean()) for title,img in panels[2:]}
    result={'pixel_layers':len([l for l in psd.descendants() if not l.is_group()]),'groups':len([l for l in psd.descendants() if l.is_group()]),'toggle_mean_changes':effects,'contact_sheet':str(job/'editability_review.png')}
    assert all(value>0 for value in effects.values())
    write_json(job/'editability_check.json',result)
    print(result)


if __name__=='__main__': main()
