from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw
import pytest
from psd_tools import PSDImage

from anime_layer_agent.compact import align_linework, decompose_material, prepare_compact, build_compact, semantic_masks
from anime_layer_agent.composition import composite_rgb, render_psd, render_generated_psd
from anime_layer_agent.storage import write_json, read_json
from anime_layer_agent.pipeline import demo


def test_dense_alignment_and_line_roundtrip():
    ink=Image.new('RGB',(96,96),'white')
    draw=ImageDraw.Draw(ink)
    draw.ellipse((18,16,75,78),outline=(70,45,25),width=2)
    rgb=np.asarray(ink,dtype=np.float32)/255
    displaced=Image.new('L',(96,96),255)
    displaced.paste(ink.convert('L'),(4,-3))
    guide=1-np.asarray(displaced,dtype=np.float32)/255
    under,color,alpha,warped,mx,my,stats=align_linework(rgb,guide)
    assert stats['guide_distance_after_warp_px'] < stats['guide_distance_before_px']
    np.testing.assert_allclose(under*(1-alpha[...,None])+color*alpha[...,None],rgb,atol=1e-6)
    assert 0 < np.count_nonzero(alpha) < alpha.size*.4


def test_material_inverse_is_editable_and_precise():
    rng=np.random.default_rng(3)
    target=rng.uniform(.1,.95,(20,30,3)).astype(np.float32)
    base,shadow,sa,light,ha=decompose_material(target,np.array([.7,.5,.6]))
    shaded=composite_rgb(base,shadow,'multiply',sa[...,None])
    result=composite_rgb(shaded,light,'screen',ha[...,None])
    np.testing.assert_allclose(result,target,atol=1e-6)
    assert np.ptp(base,axis=(0,1)).max()==0
    assert np.abs(result-shaded).max()>.1
    assert np.abs(result-composite_rgb(base,light,'screen',ha[...,None])).max()>.1


def test_background_shadow_can_remain_background():
    rgb=np.ones((80,100,3),np.float32)
    geometry=np.ones((80,100),np.int32)
    geometry[10:35,10:35]=2
    rgb[10:35,10:35]=.2
    rgb[50:70,60:90]=.2  # Separate floor shadow, similar color to the object.
    arrays={'rgb':rgb,'underlying':rgb,'geometry':geometry,'alpha':np.zeros((80,100),np.float32)}
    plan={'parts':[{'semantic_id':'background','regions':[1]}, {'semantic_id':'object','regions':[2]}]}
    assert semantic_masks(arrays,plan)[60,75]==2
    plan['recover_background_leaks']=False
    labels=semantic_masks(arrays,plan)
    assert labels[60,75]==1
    assert labels[20,20]==2


@pytest.fixture
def compact_job(tmp_path):
    inputs=demo(tmp_path/'input')
    job=tmp_path/'compact'
    prepare_compact(**inputs,job=job)
    data=np.load(job/'compact_arrays.npz')
    assert set(np.unique(data['geometry']))=={1,2}
    write_json(job/'semantic_plan.json',{'classification_source':'astra_reviewed','max_total_layers':100,'parts':[
        {'semantic_id':'background','display_name':'背景','regions':[1]},
        {'semantic_id':'panel','display_name':'パネル','regions':[2]}]})
    return job


def test_compact_budget_unicode_and_roundtrip(compact_job):
    result=build_compact(compact_job)
    assert result['passed']
    assert result['total_layers']<=11
    assert result['metrics']['mae']<.1
    psd=PSDImage.open(result['psd'])
    assert any(layer.name=='パネル_Base' for layer in psd.descendants())
    normal=np.asarray(render_psd(result['psd']),dtype=float)
    fast=np.asarray(render_psd(result['psd'],generated=True),dtype=float)
    assert np.abs(normal-fast).max()<=1
    before=render_generated_psd(psd)
    for layer in psd.descendants():
        if layer.name=='パネル_Shadow': layer.visible=False
    after=render_generated_psd(psd)
    diff=np.abs(np.asarray(before,dtype=float)-np.asarray(after,dtype=float)).max(2)
    labels=np.load(compact_job/'semantic_labels.npy')
    assert diff[labels==2].max()>10
    assert diff[labels==1].max()==0


def test_compact_bad_plan_does_not_overwrite(compact_job):
    result=build_compact(compact_job)
    path=Path(result['psd']); original=path.read_bytes()
    plan=read_json(compact_job/'semantic_plan.json')
    plan['max_total_layers']=5
    write_json(compact_job/'semantic_plan.json',plan)
    with pytest.raises(ValueError,match='exceeds budget'):
        build_compact(compact_job)
    assert path.read_bytes()==original


def test_compact_requires_complete_ids(compact_job):
    plan=read_json(compact_job/'semantic_plan.json')
    plan['parts'][1]['regions']=[]
    with pytest.raises(ValueError,match='Incomplete'):
        semantic_masks(np.load(compact_job/'compact_arrays.npz'),plan)


def test_semantic_plan_change_requires_rebuild(compact_job):
    from anime_layer_agent.pipeline import evaluate
    build_compact(compact_job)
    plan=read_json(compact_job/'semantic_plan.json')
    plan['parts'][1]['display_name']='New name'
    write_json(compact_job/'semantic_plan.json',plan)
    with pytest.raises(ValueError,match='Semantic plan changed'):
        evaluate(compact_job)
