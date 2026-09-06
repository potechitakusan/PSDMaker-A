import numpy as np
import pytest
from PIL import Image
from psd_tools import PSDImage
from skimage.color import rgb2lab

from anime_layer_agent.artist import configure_editing, decompose_artist, resolve_settings, review_editing
from anime_layer_agent.selection import color_mask, apply_selections, select_color, assign_selection, recover_background_boundary
from anime_layer_agent.compact import prepare_compact, build_compact
from anime_layer_agent.pipeline import demo, evaluate
from anime_layer_agent.composition import composite_rgb, render_generated_psd
from anime_layer_agent.storage import read_json, write_json


def test_seed_selection_local_global_family_scope_and_diagonal():
    rgb = np.ones((20, 24, 3), np.float32)
    rgb[2:5, 2:5] = [1, 0, 0]
    rgb[12:15, 12:15] = [1, 0, 0]
    assert color_mask(rgb, 3, 3, tolerance=0).sum() == 9
    assert color_mask(rgb, 3, 3, tolerance=0, contiguous=False).sum() == 18
    assert color_mask(rgb, 3, 3, tolerance=100, contiguous=False, families=['red']).sum() == 18
    assert color_mask(rgb, 3, 3, tolerance=100, contiguous=False, hue_range=[350, 10]).sum() == 18
    scope = np.zeros((20, 24), bool); scope[:10] = True
    assert color_mask(rgb, 3, 3, contiguous=False, scope=scope).sum() == 9
    rgb[5, 5] = [1, 0, 0]
    assert color_mask(rgb, 3, 3, connectivity=4).sum() == 9
    assert color_mask(rgb, 3, 3, connectivity=8).sum() == 10
    for kwargs in [dict(x=-1, y=3), dict(x=3, y=3, tolerance=float('nan')),
                   dict(x=3,y=3,families=['blue']), dict(x=3,y=3,metric='unknown')]:
        with pytest.raises(ValueError):
            color_mask(rgb, **kwargs)


def test_selection_moves_only_source_and_rejects_double_assignment():
    rgb = np.full((10, 10, 3), .5, np.float32)
    labels = np.ones((10, 10), np.int32); labels[8:] = 2
    plan = {'parts':[{'semantic_id':'background'}, {'semantic_id':'cloth'}],
            'selection_assignments':[{'from':'background','to':'cloth',
                                      'selection':{'x':3,'y':3,'tolerance':0}}]}
    assert np.all(apply_selections(rgb, labels.copy(), plan) == 2)
    plan['selection_assignments'] *= 2
    with pytest.raises(ValueError, match='Overlapping'):
        apply_selections(rgb, labels.copy(), plan)


def test_boundary_recovery_keeps_background_and_distant_floor_shadow():
    rgb = np.ones((80,100,3),np.float32)
    labels = np.ones((80,100),np.int32)
    labels[10:40,10:40] = 2
    rgb[10:45,10:40] = [.65,.25,.4]  # Cloth extends beyond the guide mask.
    rgb[65:75,60:90] = [.65,.25,.4]  # Separate matching floor shadow stays background.
    parts = [{'semantic_id':'background'}, {'semantic_id':'cloth'}]
    actual = recover_background_boundary(rgb,labels,parts,{'mode':'reviewed_light_background','max_distance':12})
    assert actual[42,20] == 2
    assert actual[70,70] == 1
    assert actual[30,43] == 1
    np.testing.assert_array_equal(recover_background_boundary(rgb,labels,parts,None),labels)


@pytest.mark.parametrize('tolerance', [0, 15, 100])
@pytest.mark.parametrize('lighting', ['neutral', 'cool', 'warm'])
def test_lighting_preserves_details_and_obeys_color_change_cap(tolerance, lighting):
    rng = np.random.default_rng(24)
    target = rng.uniform(.05, .95, (25, 10, 3)).astype(np.float32)
    base, shadow, sa, light, ha, detail, da, changed = decompose_artist(target, [.8,.45,.6], tolerance, lighting)
    shaded = composite_rgb(base, shadow, 'multiply', sa[..., None])
    lit = composite_rgb(shaded, light, 'screen', ha[..., None])
    actual = composite_rgb(lit, detail, 'normal', da[..., None])
    np.testing.assert_allclose(actual, changed, atol=1e-6)
    assert np.linalg.norm(rgb2lab(changed)-rgb2lab(target),axis=2).max() <= tolerance/5 + 1e-4
    assert np.ptp(shadow, axis=(0,1)).max() == 0
    assert np.ptp(light, axis=(0,1)).max() == 0
    if tolerance == 0:
        np.testing.assert_allclose(changed, target)
    if lighting == 'neutral':
        assert np.ptp(shadow,axis=2).max() == np.ptp(light,axis=2).max() == 0


@pytest.fixture
def artist_job(tmp_path):
    job = tmp_path/'artist'
    prepare_compact(**demo(tmp_path/'inputs'), job=job)
    write_json(job/'semantic_plan.json', {'classification_source':'astra_reviewed','parts':[
        {'semantic_id':'background','regions':[1]},
        {'semantic_id':'cloth','display_name':'着物','regions':[2],'group_path':['キャラクター','衣装']} ]})
    configure_editing(job)
    return job


def test_shared_palette_not_inferred_from_names():
    target = np.full((20,20,3), .8, np.float32)
    labels = np.ones((20,20), np.int32)
    labels[:8] = 2; labels[8:16] = 3
    target[:8] = [.8,.3,.5]; target[8:16] = [.7,.35,.55]
    plan = {'editing':{'profile':'artist'}, 'parts':[{'semantic_id':'background'},
            {'semantic_id':'left','palette_id':'cloth'}, {'semantic_id':'right','palette_id':'cloth'}]}
    colors, report = resolve_settings(plan, labels, target, np.zeros((20,20)))
    np.testing.assert_array_equal(colors['left'], colors['right'])
    assert report['resolved_layer_range'] == [6,10]
    assert 10 <= report['resolved_color_tolerance'] <= 40


def test_artist_psd_hierarchy_monochrome_and_readback(artist_job):
    result = build_compact(artist_job)
    assert result['editing_readback']['passed']
    manifest = read_json(artist_job/'layers.json')
    psd = PSDImage.open(result['psd'])
    groups = [layer for layer in psd.descendants() if layer.is_group()]
    assert len(groups) == manifest['group_count']
    group = next(layer for layer in groups if layer.name == '着物 [cloth]')
    assert group.parent.name == '衣装'
    assert any(layer.name.endswith('_Base') for layer in group)
    assert any(layer.name.endswith('_Shadow') for layer in group)
    line = next(layer for layer in psd.descendants() if layer.name.startswith('Lineart'))
    rgba = np.asarray(line.topil().convert('RGBA'))
    assert np.ptp(rgba[rgba[...,3]>0,:3],axis=1).max() == 0
    actual = np.asarray(render_generated_psd(psd), dtype=float)
    standard = np.asarray(psd.composite(force=True), dtype=float)
    assert np.abs(actual - standard).max() <= 1
    group.visible = False
    after = np.asarray(render_generated_psd(psd), dtype=float)
    labels = np.load(artist_job/'semantic_labels.npy')
    assert np.max(np.abs(actual-after)[labels==2]) > 0
    assert np.max(np.abs(actual-after)[labels==1]) == 0
    Image.new('RGB', psd.size).save(artist_job/'editing_target.png')
    with pytest.raises(ValueError, match='editing_target changed'):
        evaluate(artist_job)


def test_editing_review_before_build_does_not_certify_or_write_psd(artist_job):
    plan = read_json(artist_job/'semantic_plan.json')
    plan['classification_source'] = 'partial_review'
    write_json(artist_job/'semantic_plan.json', plan)
    result = review_editing(artist_job)
    assert (artist_job/'background_only.png').is_file()
    assert (artist_job/'semantic_review_01.png').is_file()
    assert not (artist_job/'output.psd').exists()
    assert result['background_review']['verdict'] == 'visual_review_required'
    assert read_json(artist_job/'semantic_plan.json')['classification_source'] == 'partial_review'


def test_artist_budget_rejection_preserves_psd_and_lower_bound_is_honest(artist_job):
    result = build_compact(artist_job)
    original = (artist_job/'output.psd').read_bytes()
    configure_editing(artist_job, layer_range='1-1')
    with pytest.raises(ValueError, match='exceeds budget'):
        build_compact(artist_job)
    assert (artist_job/'output.psd').read_bytes() == original
    configure_editing(artist_job, layer_range='100-200')
    result = build_compact(artist_job)
    assert result['passed'] is False
    assert read_json(artist_job/'editing_report.json')['layer_range_met'] is False
    assert result['pixel_layers'] < 100  # No dummy layers to reach the lower bound.


def test_selection_cli_flow_validates_reference_and_requires_review(artist_job):
    record = select_color(artist_job, 5, 5, tolerance=0, source_part='background')
    assign_selection(artist_job, record['selection_file'], to='cloth', source_part='background')
    assert read_json(artist_job/'semantic_plan.json')['classification_source'] == 'partial_review'
    with pytest.raises(ValueError, match='visually reviewed'):
        build_compact(artist_job)
    record['reference_hash'] = 'other'
    write_json(record['selection_file'], record)
    with pytest.raises(ValueError, match='different reference'):
        assign_selection(artist_job, record['selection_file'], to='cloth', source_part='background')


@pytest.mark.parametrize('kwargs', [dict(layer_range='200-100'), dict(layer_range='bad'),
                                  dict(color_tolerance='nan'), dict(color_tolerance='101')])
def test_invalid_preferences_do_not_change_plan(artist_job, kwargs):
    before = (artist_job/'semantic_plan.json').read_bytes()
    with pytest.raises(ValueError):
        configure_editing(artist_job, **kwargs)
    assert (artist_job/'semantic_plan.json').read_bytes() == before
