"""Stable 1..50 light choices, signed screen coordinates (Y up, Z viewer)."""
import numpy as np


def light_presets():
    choices=[]
    offsets=[(0,0)]+[(x,y) for y in (-.2,0,.2) for x in (-.2,0,.2) if (x,y)!=(0,0)]
    for sx,sy,title in [(-1,1,'左上手前'),(1,1,'右上手前'),(-1,-1,'左下手前'),(1,-1,'右下手前')]:
        for i,(dx,dy) in enumerate(offsets):
            choices.append(dict(id=len(choices)+1,label=f'{title} {i+1}/9',
                light=[round(sx*.65+dx,2),round(sy*.65+dy,2),.55],enabled=True))
    for x,y,title in [(0,1,'上手前'),(0,-1,'下手前'),(-1,0,'左手前'),(1,0,'右手前')]:
        for i,r in enumerate((.85,.55,.25)):
            choices.append(dict(id=len(choices)+1,label=f'{title}'+('' if i==0 else f' 中央寄り{i}'),
                light=[x*r,y*r,.55],enabled=True))
    choices.append(dict(id=49,label='中央手前',light=[0,0,1],enabled=True))
    choices.append(dict(id=50,label='法線なし（Baseのみ）',light=None,enabled=False))
    return choices


def light_preset(preset=1):
    if isinstance(preset,bool) or not isinstance(preset,int) or not 1<=preset<=50:
        raise ValueError('Light preset must be an integer 1..50')
    return light_presets()[preset-1]


def matching_preset(light, enabled=True):
    if not enabled:return 50
    value=np.asarray(light,dtype=float)
    for item in light_presets()[:-1]:
        a=np.asarray(item['light'],float)
        if np.allclose(value/np.linalg.norm(value),a/np.linalg.norm(a),atol=1e-6):return item['id']
    return None


def list_normal_lights():
    return dict(default=1,count=50,presets=light_presets())


def select_normal_light(job,preset=1):
    from .coloring import load_job,event
    from .storage import write_json
    item=light_preset(preset)
    job,_,_,plan,_=load_job(job)
    lighting=plan.get('lighting')
    if not lighting or lighting.get('method')!='normals':
        raise ValueError('Prepare normal lighting before choosing a light preset')
    lighting.update(preset=preset,enabled=item['enabled'],reviewed=False,notes='')
    if item['enabled']:lighting['light']=item['light']
    write_json(job/'semantic_plan.json',plan)
    event(job,'coloring_lighting_review',f'光源{preset}: {item["label"]}を選択。PSD再構築前に確認が必要',
          next_action='プレビュー確認→lighting.reviewedとnotes→build-colored→post-review')
    return dict(job=str(job),preset=item,reviewed=False)
