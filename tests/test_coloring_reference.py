"""Portable handoff must work without the source job or its masks."""
import shutil

import numpy as np
import pytest
from PIL import Image, ImageDraw
from psd_tools import PSDImage

from anime_layer_agent import coloring
from anime_layer_agent.__main__ import parser
from anime_layer_agent.artist import configure_editing
from anime_layer_agent.coloring_reference import load_coloring_reference
from anime_layer_agent.compact import prepare_compact, build_compact
from anime_layer_agent.pipeline import demo
from anime_layer_agent.storage import read_json, write_json, digest


@pytest.fixture
def portable(tmp_path):
    source = tmp_path / 'source'
    prepare_compact(**demo(tmp_path / 'inputs'), job=source)
    write_json(source / 'semantic_plan.json', dict(classification_source='astra_reviewed', parts=[
        dict(semantic_id='background', regions=[1]),
        dict(semantic_id='cloth', display_name='衣装', palette_id='fabric', regions=[2],
             group_path=['人物', '衣装'], coloring_notes='左右で同じ色を使う')]))
    configure_editing(source)
    build_compact(source)
    exported = source / 'coloring_reference.json'
    assert exported.exists()  # artist builds produce the handoff automatically
    destination = tmp_path / 'portable'
    destination.mkdir()
    reference = read_json(exported)
    for path in (exported, source / reference['psd_file']):
        shutil.copy2(path, destination / path.name)
    # Remove source metadata, inputs and masks from the available fixture.
    shutil.rmtree(source)
    shutil.rmtree(tmp_path / 'inputs')
    return destination / exported.name


def new_drawing(tmp_path):
    path = tmp_path / 'new_line.png'
    image = Image.new('L', (48, 48), 255)
    ImageDraw.Draw(image).rectangle((10, 10, 37, 37), outline=0)
    image.save(path)
    return path


def test_psd_and_json_alone_preserve_hints_and_build_new_drawing(tmp_path, portable):
    reference = read_json(portable)
    part = reference['parts'][0]
    assert part['display_name'] == '衣装'
    assert part['group_path'] == ['人物', '衣装']
    assert part['coloring_notes'] == '左右で同じ色を使う'
    assert part['base_layer_path'][-1] == '衣装_Base'
    assert 'regions' not in part
    assert str(tmp_path) not in portable.read_text(encoding='utf-8')
    psd_path = portable.parent / reference['psd_file']
    original_hash = digest(psd_path)
    job = tmp_path / 'colored'
    coloring.prepare_coloring(new_drawing(tmp_path), job=job, source_reference=portable, gap_close=0)
    loaded = read_json(job / 'source_palette.json')
    assert loaded['parts'] == reference['parts']
    assert 'source_job' not in loaded
    coloring.fill_region(job, 0, 0, 'background')
    coloring.fill_region(job, 20, 20, 'cloth', 'fabric', '衣装')
    plan = read_json(job / 'semantic_plan.json')
    plan['classification_source'] = 'astra_reviewed'  # synthetic test assignments
    write_json(job / 'semantic_plan.json', plan)
    result = coloring.build_colored(job)
    assert result['passed'] and result['input_ink_preserved']
    psd = PSDImage.open(result['psd'])
    base = next(p for p in psd.descendants() if p.name == '衣装_Base')
    rgba = np.asarray(base.topil().convert('RGBA'))
    assert np.all(rgba[rgba[..., 3] > 0, :3] == reference['palettes']['fabric']['rgb'])
    assert digest(psd_path) == original_hash
    reference_hash = digest(portable)
    with pytest.raises(ValueError, match='source inputs'):
        coloring.build_colored(job, output=portable)
    assert digest(portable) == reference_hash


def test_palette_rgb_tamper_rejected_against_real_psd(portable):
    reference = read_json(portable)
    reference['palettes']['fabric']['rgb'][0] ^= 255
    write_json(portable, reference)
    with pytest.raises(ValueError, match='actual PSD Base'):
        load_coloring_reference(portable)


@pytest.mark.parametrize('changed', ['psd', 'json'])
def test_source_changes_after_prepare_are_rejected(tmp_path, portable, changed):
    job = tmp_path / 'colored'
    coloring.prepare_coloring(new_drawing(tmp_path), job=job, source_reference=portable)
    path = portable if changed == 'json' else portable.parent / read_json(portable)['psd_file']
    path.write_bytes(path.read_bytes() + b' ')
    with pytest.raises(ValueError, match='Source (PSD|reference) changed'):
        coloring.paint_flats(job)


def test_cli_requires_exactly_one_source_and_retains_legacy_option():
    common = ['prepare-coloring', '--lineart', 'line.png', '--job', 'new']
    for option in ['--source-job', '--source-reference']:
        parsed = parser().parse_args(common + [option, 'source'])
        assert getattr(parsed, option[2:].replace('-', '_')) == 'source'
    for extra in [[], ['--source-job', 'old', '--source-reference', 'portable']]:
        with pytest.raises(SystemExit):
            parser().parse_args(common + extra)
    assert parser().parse_args(['export-coloring-reference', '--job', 'old']).job == 'old'


def test_api_rejects_both_or_neither_source(tmp_path):
    for options in [{}, dict(source_job='old', source_reference='portable')]:
        with pytest.raises(ValueError, match='exactly one'):
            coloring.prepare_coloring('unused.png', job=tmp_path / 'new', **options)
