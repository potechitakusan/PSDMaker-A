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
    with pytest.raises(ValueError,match='tones'):
        finish_review(job)
    assessment['checks']['motifs_lighting']['evidence'] += ['post_review/tones_overview.png','post_review/tones_swatches.png']
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
    plan=read_json(job/'semantic_plan.json')
    assert plan['lighting']['color_model']=='source_tones'  # new jobs default to measured tones
    # The legacy gray model stays available and byte-for-byte neutral.
    plan['lighting'].update(reviewed=True,color_model='neutral',notes='Synthetic fixture: fixed geometry, two lighting bands')
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


def test_source_tone_lighting_reaches_material_ramp_without_guide_colors(tmp_path,source):
    job=assigned_job(tmp_path,source)
    palette=read_json(job/'source_palette.json')
    base=np.array(palette['palettes']['cloth']['rgb'],float)
    # A shaded, grayish Base like a dominant-color estimate of white cloth.
    shadow=[round(base[0]*.55),round(base[1]*.45),round(base[2]*.6)];peak=[252,250,248]
    palette['palettes']['cloth']['tones']=dict(source='test',status='measured',bands=dict(
        shadow=dict(rgb=shadow,L=40.0,C=20.0),bright=dict(rgb=peak,L=98.5,C=1.0),peak=dict(rgb=peak,L=98.5,C=1.0)))
    write_json(job/'source_palette.json',palette)
    config=read_json(job/'coloring_job.json');config['palette_hash']=digest(job/'source_palette.json')
    write_json(job/'coloring_job.json',config)
    guide=np.asarray(Image.open(job/'flat_preview.png').convert('RGB')).copy()
    guide[12:25,12:36]=[0,40,0]      # dark, strongly green: hue must not be copied
    guide[25:36,12:36]=[255,255,255]
    path=tmp_path/'light.png';Image.fromarray(guide).save(path)
    coloring.prepare_lighting(job,path,max_shift=0)
    plan=read_json(job/'semantic_plan.json');plan['lighting'].update(reviewed=True,notes='Synthetic ramp endpoints')
    write_json(job/'semantic_plan.json',plan)
    result=coloring.build_colored(job)
    assert result['passed'] and result['lighting_color_model']=='source_tones'
    assert result['tone_reach']['cloth']['reachable'] and not result['tone_reach_warnings']
    out=np.asarray(Image.open(job/'reconstruction.png').convert('RGB'),float)
    np.testing.assert_allclose(out[18,24],shadow,atol=3)
    np.testing.assert_allclose(out[30,24],peak,atol=3)
    # Base stays the inherited Base; only Shadow/Highlight layers carry the ramp.
    psd=PSDImage.open(result['psd'])
    layers={p.name:p for p in psd.descendants() if not p.is_group()}
    rgba=np.asarray(layers['Cloth_Base'].topil().convert('RGBA'))
    assert np.unique(rgba[rgba[...,3]>0,:3],axis=0).tolist()==[base.astype(int).tolist()]


def test_material_lighting_falls_back_to_neutral_without_tones():
    neutral=([.3,.3,.3],[.7,.7,.7])
    base=np.array([.75,.72,.79],np.float32)
    shadow,light,model=coloring.material_lighting(base,None,neutral)
    assert model=='neutral' and shadow==neutral[0]
    tones=dict(bands=dict(shadow=dict(rgb=[96,92,101]),peak=dict(rgb=[255,255,255])))
    shadow,light,model=coloring.material_lighting(base,tones,neutral)
    assert model=='source_tones'
    np.testing.assert_allclose(base*shadow,np.array([96,92,101])/255,atol=1e-3)
    np.testing.assert_allclose(base+(1-base)*light,1,atol=1e-6)


def test_levels_tone_mapping_matches_source_exposure_without_stretching_noise():
    tones=dict(bands=dict(midtone=dict(rgb=[200,200,200]),bright=dict(rgb=[250,250,250])))
    mid,bright=200/255,250/255
    # A dim guide: median .5, 87.5th percentile .6 -> gain clipped at 2.
    sample=np.linspace(.3,.7,801)
    out=coloring.levels_luma(np.array([.5,.6,.3]),sample,tones)
    low,high=np.percentile(sample,[50,87.5])
    gain=min((bright-mid)/(high-low),2)
    np.testing.assert_allclose(out[0],mid,atol=1e-6)
    np.testing.assert_allclose(out[1],mid+(.6-low)*gain,atol=1e-6)
    assert out[2]<out[0]<out[1]<=1  # light/shadow order of the guide is kept
    # Flat guide: tiny noise must not be stretched beyond the 2x gain cap.
    noisy=.5+np.random.RandomState(0).normal(0,.002,1000)
    mapped=coloring.levels_luma(noisy,noisy,tones)
    assert mapped.std()<=2.05*noisy.std()
