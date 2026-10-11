from pathlib import Path
import numpy as np
import pytest
from PIL import Image
from psd_tools import PSDImage
from anime_layer_agent.storage import read_json,write_json,digest
from anime_layer_agent.artist import configure_editing,resolve_settings
from anime_layer_agent.base_colors import base_candidates,review_base_colors,select_base_color
from anime_layer_agent.compact import prepare_compact,build_compact
from anime_layer_agent.pipeline import demo
from anime_layer_agent.light_presets import light_presets,light_preset,matching_preset,select_normal_light
from anime_layer_agent.__main__ import parser


def test_shadow_dominant_is_a_candidate_not_a_material_decision():
    rgb=np.full((20,20,3),[.55,.5,.6],np.float32)
    rgb[12:]=[.93,.91,.94]
    labels=np.full((20,20),2,np.int32);alpha=np.zeros((20,20))
    plan=dict(parts=[dict(semantic_id='background'),dict(semantic_id='cloth',palette_id='white')],
              editing=dict(profile='artist',base_mode='review_candidates'))
    report=base_candidates(plan,labels,rgb,alpha)
    candidates=report['palettes']['white']['candidates']
    assert candidates['light'][0]>candidates['dominant'][0]+60
    with pytest.raises(ValueError,match='select Base'):resolve_settings(plan,labels,rgb,alpha)
    plan['editing']['base_colors']={'white':dict(candidate='light',rgb=candidates['light'],
        notes='Synthetic light cloth band selected',input_hash=report['input_hash'],reviewed=True)}
    colors,settings=resolve_settings(plan,labels,rgb,alpha)
    np.testing.assert_allclose(colors['cloth'],np.array(candidates['light'])/255)
    changed=rgb.copy();changed[0,0]=[.2,.3,.4]
    with pytest.raises(ValueError,match='Stale'):resolve_settings(plan,labels,changed,alpha)


def test_selected_base_reaches_psd_and_portable_reference(tmp_path):
    job=tmp_path/'source';prepare_compact(**demo(tmp_path/'inputs'),job=job)
    write_json(job/'semantic_plan.json',dict(classification_source='astra_reviewed',parts=[
        dict(semantic_id='background',regions=[1]),dict(semantic_id='cloth',palette_id='cloth',regions=[2])]))
    configure_editing(job,color_tolerance='0')
    with pytest.raises(ValueError,match='select Base'):build_compact(job)
    before=digest(job/'semantic_plan.json')
    review_base_colors(job)
    assert digest(job/'semantic_plan.json')==before
    select_base_color(job,'cloth','light','Synthetic fixture: ordinary lit cloth, not a specular highlight')
    result=build_compact(job)
    assert result['editing_readback']['passed']
    choice=read_json(job/'semantic_plan.json')['editing']['base_colors']['cloth']
    ref=read_json(job/'coloring_reference.json')
    assert ref['palettes']['cloth']['rgb']==choice['rgb']
    assert ref['palettes']['cloth']['base_selection']['candidate']=='light'
    from anime_layer_agent.coloring import source_palette
    palette=source_palette(job)
    assert palette['palettes']['cloth']['base_selection']==ref['palettes']['cloth']['base_selection']
    psd=PSDImage.open(result['psd'])
    base=next(l for l in psd.descendants() if l.name=='cloth_Base')
    a=np.asarray(base.topil().convert('RGBA'))
    assert np.unique(a[a[...,3]>0,:3],axis=0).tolist()==[choice['rgb']]
    # Reconfiguring budgets retains the reviewed Base, with a fresh build still needed.
    configure_editing(job,color_tolerance='0')
    assert read_json(job/'semantic_plan.json')['editing']['base_colors']['cloth']==choice
    with pytest.raises(ValueError,match='notes'):select_base_color(job,'cloth','light','')


def test_light_catalog_has_exactly_50_unique_directions_and_default():
    choices=light_presets()
    assert [p['id'] for p in choices]==list(range(1,51))
    assert light_preset()['light']==[-.65,.65,.55]
    directions=np.array([p['light'] for p in choices[:-1]])
    directions/=np.linalg.norm(directions,axis=1,keepdims=True)
    assert len(np.unique(directions.round(8),axis=0))==49
    for start,sx,sy in [(0,-1,1),(9,1,1),(18,-1,-1),(27,1,-1)]:
        quadrant=np.array([p['light'] for p in choices[start:start+9]])
        assert np.all(quadrant[:,0]*sx>0) and np.all(quadrant[:,1]*sy>0) and np.all(quadrant[:,2]>0)
    for start in (36,39,42,45):
        directions=np.array([p['light'] for p in choices[start:start+3]])
        assert np.all(np.diff(np.linalg.norm(directions[:,:2],axis=1))<0)
    assert choices[-2]['light']==[0,0,1] and not choices[-1]['enabled']
    assert matching_preset([-.65,.65,.55])==1 and matching_preset([1,1,1],False)==50
    for bad in (0,51,True,1.5):
        with pytest.raises(ValueError):light_preset(bad)
    args=parser().parse_args(['prepare-normal-lighting','--job','j','--normals','n.npy'])
    assert args.light is None and args.preset is None
