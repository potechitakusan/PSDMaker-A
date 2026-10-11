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
from anime_layer_agent.normal_lighting import prepare_normal_lighting, normal_alphas, validate_normals


@pytest.fixture
def source(tmp_path):
    job=tmp_path/'source'
    prepare_compact(**demo(tmp_path/'input'),job=job)
    write_json(job/'semantic_plan.json',dict(classification_source='astra_reviewed',parts=[
        dict(semantic_id='background',regions=[1]),
        dict(semantic_id='cloth',display_name='Cloth',palette_id='cloth',regions=[2])]))
    configure_editing(job,base_mode='dominant');build_compact(job)
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


def test_normal_direction_and_invalid_vectors():
    n=validate_normals(np.array([[[-.8,0,.6],[.8,0,.6]]]),(1,2))
    left=normal_alphas(n,[-1,0,1])
    right=normal_alphas(n,[1,0,1])
    assert left[1][0,0]>left[1][0,1] and left[0][0,1]>left[0][0,0]
    np.testing.assert_allclose(left[0],right[0][:,::-1])
    for value in (np.zeros((1,2,3)),np.full((1,2,3),np.nan),np.ones((2,2,3))):
        with pytest.raises(ValueError):validate_normals(value,(1,2))
    with pytest.raises(ValueError):normal_alphas(n,[0,0,0])


def test_normal_lighting_preserves_masks_lines_and_rejects_stale_map(tmp_path,source):
    job=assigned_job(tmp_path,source)
    baseline=coloring.build_colored(job)
    old=PSDImage.open(baseline['psd'])
    before={l.name:np.asarray(l.topil().convert('RGBA')).copy() for l in old.descendants() if not l.is_group()}
    source_hash=digest(source/'output.psd')
    n=np.zeros((48,48,3),np.float32);n[...,:]=[-.8,0,.6];n[:,24:]=[.8,0,.6]
    path=tmp_path/'normals.npy';np.save(path,n)
    prepare_normal_lighting(job,path,[-1,1,1])
    with pytest.raises(ValueError,match='Review lighting'):coloring.build_colored(job)
    plan=read_json(job/'semantic_plan.json')
    plan['lighting'].update(reviewed=True,notes='Synthetic vectors; no geometry resampling')
    write_json(job/'semantic_plan.json',plan)
    result=coloring.build_colored(job)
    assert result['passed'] and result['lighting_method']=='normals'
    psd=PSDImage.open(result['psd'])
    layers={l.name:l for l in psd.descendants() if not l.is_group()}
    for name,rgba in before.items():np.testing.assert_array_equal(np.asarray(layers[name].topil().convert('RGBA')),rgba)
    assert digest(source/'output.psd')==source_hash
    mask=np.zeros((48,48),bool)
    l=layers['Cloth_Base'];x,y,r,b=l.bbox;mask[y:b,x:r]=np.asarray(l.topil())[...,3]>0
    for name in ('Cloth_Shadow','Cloth_Highlight'):
        l=layers[name];x,y,r,b=l.bbox
        assert np.all(mask[y:b,x:r][np.asarray(l.topil())[...,3]>0])
    np.save(path,-n)
    with pytest.raises(ValueError,match='changed'):coloring.build_colored(job)


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


def test_normal_source_ramp_matches_rgb_and_keeps_flat_normals_bounded():
    from anime_layer_agent.normal_lighting import normal_source_ramp, rgb_lighting_layers
    base=np.array([.7,.6,.8],np.float32)
    # Include a tone with both brighter and darker channels than Base.
    colors=np.array([[.4,.3,.5],[.75,.65,.7],[.85,.8,.8],[.95,.9,.88],[1,.98,.95]])
    tones=dict(bands={k:dict(rgb=(rgb*255).tolist()) for k,rgb in
        zip(['shadow','midtone','light','bright','peak'],colors)})
    sample=np.linspace(0,1,1001)
    positions=np.array([.09,.5,.7,.875,.9725])
    target,diag=normal_source_ramp(positions,sample,tones,base)
    np.testing.assert_allclose(target,colors,atol=1e-6)
    s,sa,h,ha=rgb_lighting_layers(base,target)
    out=base*(1-sa[:,None]+s*sa[:,None])
    out=out+(1-out)*h*ha[:,None]
    np.testing.assert_allclose(out,colors,atol=1e-6)
    target,diag=normal_source_ramp(positions,np.full(100,.5),tones,base)
    assert target is None and diag['status']=='flat_normals'
    target,diag=normal_source_ramp(positions,sample,tones,base,0)
    np.testing.assert_allclose(target,np.broadcast_to(base,target.shape),atol=1e-6)
    with pytest.raises(ValueError):normal_alphas(np.zeros((1,1,3)),[0,0,1],curve={'gamma':0})


def test_normal_source_ramp_psd_retains_base_and_color_endpoints(tmp_path,source):
    job=assigned_job(tmp_path,source)
    palette=read_json(job/'source_palette.json')
    tones=palette['palettes']['cloth']['tones']
    tones['bands']={k:dict(rgb=rgb,L=L,C=10) for k,rgb,L in [
        ('shadow',[60,50,80],25),('midtone',[150,160,140],65),
        ('light',[200,210,190],80),('bright',[240,235,230],94),('peak',[250,250,248],98)]}
    write_json(job/'source_palette.json',palette)
    config=read_json(job/'coloring_job.json');config['palette_hash']=digest(job/'source_palette.json')
    write_json(job/'coloring_job.json',config)
    with np.load(job/'coloring_arrays.npz') as arrays:shape=arrays['ink'].shape
    z=np.broadcast_to(np.linspace(0,1,shape[0])[:,None],shape)
    n=np.stack([np.sqrt(1-z*z),np.zeros(shape),z],-1)
    path=tmp_path/'normals.npy';np.save(path,n)
    prepare_normal_lighting(job,path,[0,0,1])
    plan=read_json(job/'semantic_plan.json')
    plan['lighting'].update(reviewed=True,notes='Synthetic material ramp',tone_mapping='source_ramp')
    write_json(job/'semantic_plan.json',plan)
    result=coloring.build_colored(job)
    assert result['passed'] and result['normal_mapping']['cloth']['status']=='mapped'
    psd=PSDImage.open(result['psd'])
    base=next(p for p in psd.descendants() if p.name=='Cloth_Base')
    rgba=np.asarray(base.topil().convert('RGBA'))
    assert np.unique(rgba[rgba[...,3]>0,:3],axis=0).tolist()==[palette['palettes']['cloth']['rgb']]


def test_50_light_picker_preview_matches_export_and_off_is_flat(tmp_path,source):
    from anime_layer_agent.normal_preview import NormalPreviewScene
    from anime_layer_agent.light_presets import select_normal_light
    job=assigned_job(tmp_path,source)
    with np.load(job/'coloring_arrays.npz') as a:shape=a['ink'].shape
    y,x=np.mgrid[-.8:.8:complex(shape[0]),-.8:.8:complex(shape[1])]
    n=np.stack([x,y,np.ones(shape)],-1);n/=np.linalg.norm(n,axis=-1,keepdims=True)
    path=tmp_path/'normal.npy';np.save(path,n)
    prepare_normal_lighting(job,path)
    plan=read_json(job/'semantic_plan.json')
    assert plan['lighting']['preset']==1
    plan['lighting'].update(reviewed=True,notes='Synthetic shape, default front upper-left')
    write_json(job/'semantic_plan.json',plan)
    result=coloring.build_colored(job)
    snapshot={p.name:(digest(p),p.stat().st_mtime_ns) for p in job.iterdir() if p.is_file()}
    scene=NormalPreviewScene(job)
    np.testing.assert_array_equal(np.asarray(scene.render(1)),np.asarray(Image.open(job/'coloring_target.png')))
    np.testing.assert_array_equal(np.asarray(scene.render(50)),np.asarray(Image.open(job/'flat_preview.png')))
    plan_hash=digest(job/'semantic_plan.json');psd_hash=digest(result['psd'])
    for i in range(1,51):assert scene.render(i).size==(shape[1],shape[0])
    assert digest(job/'semantic_plan.json')==plan_hash and digest(result['psd'])==psd_hash
    assert snapshot=={p.name:(digest(p),p.stat().st_mtime_ns) for p in job.iterdir() if p.is_file()}
    select_normal_light(job,50)
    with pytest.raises(ValueError,match='Review lighting'):coloring.build_colored(job)
    plan=read_json(job/'semantic_plan.json');plan['lighting'].update(reviewed=True,notes='Base only selected')
    write_json(job/'semantic_plan.json',plan)
    result=coloring.build_colored(job)
    assert result['lighting_method']=='none'
    np.testing.assert_array_equal(np.asarray(Image.open(job/'coloring_target.png')),np.asarray(scene.render(50)))
    assert not any(l['group'] in ('Shadows','Highlights') for l in read_json(job/'layers.json')['layers'])
