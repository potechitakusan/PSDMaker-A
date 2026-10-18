import copy

import numpy as np
import pytest
from PIL import Image
from psd_tools import PSDImage
from psd_tools.constants import Resource

from anime_layer_agent import manga, manga_adjust as adjust, manga_tone as tone
from anime_layer_agent.__main__ import parser
from anime_layer_agent.storage import read_json, write_json, digest
from test_manga import inputs, registered, reviewed_plan


@pytest.mark.parametrize('values', [
    {'mode': 'invalid'}, {'mode': 'posterize', 'dpi': None}, {'dpi': float('nan')},
    {'lpi': 0}, {'posterize_thresholds': [170, 85]}, {'posterize_thresholds': [85, 85]},
    {'posterize_densities': [.5, .3]}, {'posterize_densities': [.3, .5, 0]},
    {'posterize_densities': [.5, float('nan'), 0]},
    {'mode': 'posterize', 'dpi': 600, 'posterize_densities': [.9, .5, .3, .15, 0]},  # beta end is fixed at 100%
    {'mode': 'posterize', 'dpi': 600, 'posterize_densities': [1, .5, .3, .15, .05]},  # white end is fixed at 0%
    {'mode': 'posterize', 'dpi': 600, 'tone_format': 'gray'},  # posterize outputs dots only
])
def test_reject_invalid_controls(values):
    with pytest.raises(ValueError): tone.validate_settings(values)


def test_thresholds_density_and_lpi_are_independent():
    settings = tone.validate_settings(dict(mode='posterize', dpi=600, lpi=60,
                                           posterize_thresholds=[85, 170], posterize_densities=[1, .3, 0]))
    values = np.array([0, 84 / 255, 85 / 255, 169 / 255, 170 / 255, 1], np.float64)
    assert tone.posterize_cover(values, settings) == pytest.approx([1, 1, .3, .3, 0, 0])
    # Changing the image width does not change the CSP-style pitch.
    for width in (128, 256):
        page = tone.analyze_page(np.full((64, width), .6, np.float32), settings)
        assert page['pitch'] == 10
    # Saves from before the fixed ends still load in MangaScreen mode; the old switch is dropped.
    legacy = tone.validate_settings(dict(posterize_enabled=False, posterize_thresholds=[85, 170], posterize_densities=[.5, .3, 0]))
    assert 'posterize_enabled' not in legacy
    defaults = tone.DEFAULT_SETTINGS
    assert defaults['posterize_densities'][0] == 1 and defaults['posterize_densities'][-1] == 0
    assert len(defaults['posterize_densities']) == 5  # beta, dark, middle 1, middle 2, white


@pytest.mark.parametrize('mode', ['node', 'posterize'])
def test_preview_export_saved_settings_and_dpi(tmp_path, inputs, mode):
    job, _ = registered(tmp_path, inputs)
    plan = reviewed_plan(job)
    original = digest(job / 'semantic_plan.json')
    settings = tone.validate_settings(dict(plan['tone_settings'], mode=mode, dpi=600, lpi=60,
                                           min_region=0, tone_format='dots'))
    preview = adjust.preview_manga(job, settings)
    assert digest(job / 'semantic_plan.json') == original  # Apply does not alter semantic plan/PSD.
    assert not list(tmp_path.rglob('*.psd'))
    assert adjust.settings_for_job(job, reuse_last=True) == settings
    output = tmp_path / 'out' / f'{mode}.psd'
    result = adjust.export_manga(job, output, settings=settings, expected_context=preview['context'])
    assert result['passed'] and result['review_status'] == 'post_review_required'
    assert np.array_equal(np.asarray(preview['image']), np.asarray(Image.open(job / 'reconstructed.png')))
    resolution = PSDImage.open(output).image_resources.get_data(Resource.RESOLUTION_INFO)
    assert resolution.horizontal / 65536 == 600 and resolution.vertical / 65536 == 600
    analysis = read_json(job / 'analysis.json')
    if mode == 'posterize':
        assert analysis['screen']['pitch_px'] == 10
        assert analysis['screen']['lpi'] == 60
    repeated = adjust.export_manga(job, tmp_path / 'last.psd', reuse_last=True, review=False)
    assert repeated['passed']
    assert np.array_equal(np.asarray(preview['image']), np.asarray(Image.open(job / 'reconstructed.png')))


def test_stale_preview_and_invalid_output_do_not_change_plan(tmp_path, inputs):
    job, _ = registered(tmp_path, inputs)
    plan = reviewed_plan(job)
    preview = adjust.preview_manga(job, tone.validate_settings(plan['tone_settings']))
    plan['parts'][0]['tone']['base'] = 'light'
    write_json(job / 'semantic_plan.json', plan)
    before = digest(job / 'semantic_plan.json')
    with pytest.raises(ValueError, match='プレビュー後'):
        adjust.export_manga(job, tmp_path / 'out.psd', expected_context=preview['context'])
    with pytest.raises(ValueError, match='.psd'):
        adjust.export_manga(job, tmp_path / 'out.png')
    assert digest(job / 'semantic_plan.json') == before


def test_posterization_ignores_native_part_overrides(tmp_path, inputs):
    job, _ = registered(tmp_path, inputs)
    plan = reviewed_plan(job)
    settings = tone.validate_settings(dict(plan['tone_settings'], mode='posterize', dpi=600, min_region=0))
    first = adjust.preview_manga(job, settings, persist=False)
    for part in plan['parts']:
        part['tone'] = dict(base='black', shadow='none', highlight='none', beta=False)
    write_json(job / 'semantic_plan.json', plan)
    second = adjust.preview_manga(job, settings, persist=False)
    assert np.array_equal(np.asarray(first['image']), np.asarray(second['image']))
    third = adjust.preview_manga(job, dict(settings, posterize_densities=[1, .9, .6, .3, 0]), persist=False)
    assert not np.array_equal(np.asarray(second['image']), np.asarray(third['image']))


def test_presets_round_trip(tmp_path):
    settings = tone.validate_settings(dict(mode='posterize', dpi=350, lpi=50))
    saved = adjust.save_preset(tmp_path / 'presets' / '細かい網.json', settings)
    assert saved['name'] == '細かい網' and saved['saved_at']
    loaded = adjust.load_preset(tmp_path / 'presets' / '細かい網.json')
    assert loaded['tone_settings'] == settings and loaded['saved_at'] == saved['saved_at']
    write_json(tmp_path / 'other.json', dict(tone_settings=settings))
    with pytest.raises(ValueError, match='プリセット'):
        adjust.load_preset(tmp_path / 'other.json')
    # A preset also works as the headless --settings file.
    assert adjust.settings_for_job(tmp_path, settings_file=tmp_path / 'presets' / '細かい網.json') == settings


def test_headless_parser():
    args = parser().parse_args(['export-manga', '--job', 'work/example', '--reuse-last', '--output', 'output/test.psd'])
    assert args.reuse_last and args.job == 'work/example'
