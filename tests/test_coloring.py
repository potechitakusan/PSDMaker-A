from pathlib import Path
import numpy as np
import pytest
from PIL import Image
from psd_tools import PSDImage

from anime_layer_agent.__main__ import parser
from anime_layer_agent import coloring
from anime_layer_agent.compact import prepare_compact,build_compact
from anime_layer_agent.artist import configure_editing
from anime_layer_agent.pipeline import demo
from anime_layer_agent.storage import read_json,write_json,digest
from anime_layer_agent.post_review import post_review,finish_review


@pytest.fixture
def source(tmp_path):
    job=tmp_path/'source'
    prepare_compact(**demo(tmp_path/'input'),job=job)
    write_json(job/'semantic_plan.json',dict(classification_source='astra_reviewed',parts=[
        dict(semantic_id='background',regions=[1]),
        dict(semantic_id='cloth',display_name='Cloth',palette_id='cloth',regions=[2])]))
    configure_editing(job);build_compact(job)
    return job


def drawing(path,gap=0):
    ink=np.zeros((48,48),bool)
    ink[10:38,10]=True;ink[10:38,37]=True;ink[10,10:38]=True;ink[37,10:38]=True
    ink[10,22:22+gap]=False
    Image.fromarray(np.uint8(~ink)*255).save(path)
    return ink


def assigned_job(tmp_path,source):
    line=tmp_path/'line.png';drawing(line)
    job=tmp_path/'colored'
    coloring.prepare_coloring(line,source,job,gap_close=0)
    coloring.fill_region(job,0,0,'background')
    coloring.fill_region(job,20,20,'cloth','cloth','Cloth')
    plan=read_json(job/'semantic_plan.json');plan['classification_source']='astra_reviewed'
    write_json(job/'semantic_plan.json',plan)
    coloring.paint_flats(job)
    return job


def test_no_implicit_coloring_and_separate_output_required(tmp_path):
    with pytest.raises(SystemExit):parser().parse_args(['prepare-coloring','--source-job','x','--job','y'])
    assert parser().parse_args(['run','--reference','a','--lineart','b','--output','c']).command=='run'
    path=tmp_path/'ink.png';drawing(path)
    with pytest.raises(ValueError,match='separate'):coloring.binarize_lineart(path,path)


def test_binarization_white_to_alpha_and_gap_barrier_does_not_redraw_ink(tmp_path):
    path=tmp_path/'ink.png';ink=drawing(path,gap=3)
    open_labels,_=coloring.enclosed_regions(ink,0,1)
    labels,barriers=coloring.enclosed_regions(ink,6,1)
    assert open_labels[20,20]==open_labels[0,0]
    assert labels[20,20]!=labels[0,0]
    assert barriers[10,23] and not ink[10,23]
    output=tmp_path/'transparent.png'
    coloring.binarize_lineart(path,output,transparent=True)
    rgba=np.asarray(Image.open(output))
    np.testing.assert_array_equal(rgba[...,3],ink*255)
    # Transparent black pixels must be treated as white when read back.
    np.testing.assert_array_equal(coloring.binary_ink(output),ink)
    with pytest.raises(ValueError):coloring.enclosed_regions(ink,65)


def test_coloring_preserves_actual_source_palette_ink_and_old_job(tmp_path,source):
    before=digest(source/'output.psd')
    job=assigned_job(tmp_path,source)
    result=coloring.build_colored(job,tmp_path/'output/output.psd')
    assert result['passed'] and result['input_ink_preserved']
    assert digest(source/'output.psd')==before
    psd=PSDImage.open(result['psd']);pixels={p.name:p for p in psd.descendants() if not p.is_group()}
    base=np.asarray(pixels['Cloth_Base'].topil())
    palette=read_json(job/'source_palette.json')['palettes']['cloth']['rgb']
    assert np.all(base[base[...,3]>0,:3]==palette)
    assert read_json(job/'status.json')['phase']=='post_review_required'
    assert post_review(job)['reviewed'] is False
    assessment=read_json(job/'post_review_assessment.json')
    for check in assessment['checks'].values():
        check.update(status='pass',notes='Synthetic fixture validation only',evidence=['post_review/overview.png'])
    write_json(job/'post_review_assessment.json',assessment)
    assert finish_review(job)['passed']
    assert (job/'time_log.md').exists() and (tmp_path/'output/time_log.md').exists()
    with pytest.raises(ValueError,match='source inputs'):coloring.build_colored(job,source/'output.psd')
    with pytest.raises(ValueError,match='fresh'):coloring.prepare_coloring(tmp_path/'line.png',source,job)


def test_seeds_coverage_duplicates_and_stale_inputs_rejected(tmp_path,source):
    path=tmp_path/'line.png';drawing(path)
    job=tmp_path/'job';coloring.prepare_coloring(path,source,job,gap_close=0)
    hints=read_json(job/'source_palette.json')['parts']
    assert hints[0]['semantic_id']=='cloth' and hints[0]['display_name']=='Cloth'
    with pytest.raises(ValueError,match='barrier'):coloring.fill_region(job,10,15,'cloth','cloth')
    with pytest.raises(ValueError,match='outside'):coloring.fill_region(job,-1,0,'cloth','cloth')
    coloring.fill_region(job,0,0,'background')
    with pytest.raises(ValueError,match='Unassigned'):coloring.paint_flats(job)
    coloring.fill_region(job,20,20,'cloth','cloth')
    before=digest(job/'semantic_plan.json')
    with pytest.raises(ValueError,match='two parts'):coloring.fill_region(job,20,20,'other','cloth')
    assert digest(job/'semantic_plan.json')==before
    path.write_bytes(path.read_bytes()+b'changed')
    with pytest.raises(ValueError,match='lineart changed'):coloring.build_colored(job)


def test_ai_lighting_is_reviewed_scalar_only_and_never_replaces_lines(tmp_path,source):
    job=assigned_job(tmp_path,source)
    guide=np.asarray(Image.open(job/'flat_preview.png').convert('RGB')).copy()
    guide[12:25,12:36]=[20,10,60]
    guide[25:36,12:36]=[240,220,160]
    path=tmp_path/'light.png';Image.fromarray(guide).save(path)
    coloring.prepare_lighting(job,path,max_shift=0)
    with pytest.raises(ValueError,match='Review lighting'):coloring.build_colored(job)
    plan=read_json(job/'semantic_plan.json');plan['lighting'].update(reviewed=True,notes='Synthetic fixture: fixed geometry, two lighting bands')
    write_json(job/'semantic_plan.json',plan)
    result=coloring.build_colored(job)
    assert result['passed']
    manifest=read_json(job/'layers.json')
    psd=PSDImage.open(result['psd']);layers={p.name:p for p in psd.descendants() if not p.is_group()}
    roles={item['group'] for item in manifest['layers']}
    assert {'Shadows','Highlights'}<=roles
    for item in manifest['layers']:
        if item['group'] in ('Shadows','Highlights'):
            rgba=np.asarray(layers[item['name']].topil().convert('RGBA'));rgb=rgba[rgba[...,3]>0,:3]
            assert np.all(rgb[:,0]==rgb[:,1]) and np.all(rgb[:,1]==rgb[:,2])
    assert result['metrics']['max_channel_error']<=3
    with np.load(job/'coloring_arrays.npz') as arrays:
        ink=arrays['ink']
    line=next(v for k,v in layers.items() if k.startswith('Lineart_'))
    actual=np.zeros_like(ink);actual[line.top:line.bottom,line.left:line.right]=np.asarray(line.topil().convert('RGBA'))[...,3]>0
    np.testing.assert_array_equal(actual,ink)
    path.write_bytes(path.read_bytes()+b'changed')
    with pytest.raises(ValueError,match='guide changed'):coloring.build_colored(job)


def test_local_gap_split_keeps_other_region_ids_and_invalidates_plan(tmp_path,source):
    path=tmp_path/'line.png';ink=drawing(path)
    ink[11:37,23]=True;ink[22:27,23]=False
    Image.fromarray(np.uint8(~ink)*255).save(path)
    job=tmp_path/'job';coloring.prepare_coloring(path,source,job,gap_close=0,min_area=4)
    with np.load(job/'coloring_arrays.npz') as a:before=a['regions'].copy()
    region=int(before[20,20]);outside=before!=region
    result=coloring.split_color_region(job,region,gap_close=12)
    assert len(result['new_regions'])==2
    with np.load(job/'coloring_arrays.npz') as a:
        np.testing.assert_array_equal(a['regions'][outside],before[outside])
        np.testing.assert_array_equal(a['ink'],ink)
        assert a['regions'][20,20]!=a['regions'][20,30]
        for r in result['new_regions']:
            x,y=r['seed_xy'];assert not a['barriers'][y,x] and a['regions'][y,x]==r['number']
    assert read_json(job/'semantic_plan.json')['classification_source']=='partial_review'
