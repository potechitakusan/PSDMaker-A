import copy
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw
from psd_tools import PSDImage

from anime_layer_agent import manga, manga_adjust as adjust, tone_transfer as transfer
from anime_layer_agent.storage import read_json, write_json, digest
from anime_layer_agent.__main__ import parser
from test_manga import inputs, registered, reviewed_plan


@pytest.fixture
def source(tmp_path, inputs):
    job, _ = registered(tmp_path, inputs)
    plan = reviewed_plan(job)
    plan['tone_settings'].update(dpi=600, min_region=0)
    write_json(job / 'semantic_plan.json', plan)
    manga.build_manga(job, tmp_path / 'source_output' / 'source.psd')
    return job


def new_lineart(path):
    """Different size/placement from the source; explicit test fixture, not a user image."""
    image = Image.new('RGB', (480, 320), 'white')
    draw = ImageDraw.Draw(image)
    draw.ellipse((30, 30, 130, 130), outline='black', width=3)
    draw.rectangle((170, 30, 300, 200), outline='black', width=3)
    draw.line((170, 110, 300, 110), fill='black', width=3)
    draw.rectangle((335, 40, 450, 240), outline='black', width=3)
    image.save(path)
    return path


def prepare_target(tmp_path, source, portable=False):
    path = new_lineart(tmp_path / 'new_lineart.png')
    job = tmp_path / 'transfer'
    kw = {'source_reference': Path(read_json(source / 'layers.json')['psd_path']).parent / 'tone_reference.json'} if portable else {'source_job': source}
    transfer.prepare_tone_transfer(path, job, **kw)
    _, _, plan, arrays = transfer.load_inputs(job, require_review=False)
    ref = read_json(job / 'source_tones.json')
    for x, y, material, role in ((10, 10, 'background', 'base'), (75, 75, 'face', 'base'),
            (200, 60, 'dress', 'base'), (200, 150, 'dress', 'shadow'), (380, 90, 'hair', 'base')):
        plan = transfer.assign_region(plan, ref, transfer.seed_region(arrays, x, y), material, role)
    plan['classification_source'] = 'astra_reviewed'
    # The fixture's material/role ground truth is defined by the generator above.
    write_json(job / 'semantic_plan.json', plan)
    return job, plan, arrays


@pytest.mark.parametrize('portable', [False, True])
@pytest.mark.parametrize('format', ['dots', 'gray'])
def test_transfer_new_geometry_png_psd_and_fixed_ink(tmp_path, source, portable, format):
    source_psd = Path(read_json(source / 'layers.json')['psd_path'])
    old_hashes = [digest(source_psd), digest(source / 'semantic_plan.json')]
    job, plan, arrays = prepare_target(tmp_path, source, portable)
    before = digest(job / 'semantic_plan.json')
    original_line = digest(tmp_path / 'new_lineart.png')
    settings = dict(plan['tone_settings'], tone_format=format)
    preview = adjust.preview_manga(job, settings)
    assert digest(job / 'semantic_plan.json') == before
    output = tmp_path / 'delivery' / 'new.psd'
    result = adjust.export_manga(job, output, settings=settings, expected_context=preview['context'])
    assert result['passed'] and result['readback']['max_channel_error'] == 0
    assert result['review_status'] == 'post_review_required'
    assert np.array_equal(np.asarray(preview['image']), np.asarray(Image.open(job / 'reconstructed.png')))
    png = tmp_path / 'delivery' / 'new.png'
    result = adjust.export_manga_png(job, png, settings=settings)
    assert result['passed'] and Image.open(png).size == (480, 320)
    assert np.array_equal(np.asarray(Image.open(png)), np.asarray(Image.open(job / 'reconstructed.png')))
    assert Image.open(png).info['dpi'][0] == pytest.approx(600, abs=.02)
    line = np.asarray(Image.open(job / 'post_review' / 'lineart.png').convert('L')) < 128
    assert np.array_equal(line, arrays['fixed_ink'])
    assert old_hashes == [digest(source_psd), digest(source / 'semantic_plan.json')]
    assert original_line == digest(tmp_path / 'new_lineart.png')
    data = read_json(output.parent / 'tone_reference.json')
    assert not any('regions' in p or 'bbox' in p for p in data['parts'])
    assert {p['semantic_id']: p['tone'] for p in data['parts']} == {p['semantic_id']: p['transfer_tone'] for p in plan['parts']}
    psd = PSDImage.open(output)
    assert any(p.name == '髪_ベタ' for p in psd.descendants())


def test_profiles_resolve_auto_and_legacy_job(tmp_path, source):
    data = transfer.reference_from_job(source)
    dress = next(p for p in data['parts'] if p['semantic_id'] == 'dress')
    assert 0 < dress['tone']['base'] < 1
    assert all(type(dress['tone'][key]) is float for key in transfer.DENSITIES)
    manifest = read_json(source / 'layers.json')
    for s in manifest['solvers']: s.pop('transfer_tone')
    write_json(source / 'layers.json', manifest)
    before = digest(source / 'semantic_plan.json')
    assert transfer.reference_from_job(source) == data
    assert digest(source / 'semantic_plan.json') == before


def test_unreviewed_unassigned_duplicate_unknown_and_role_mismatch(tmp_path, source):
    job, plan, arrays = prepare_target(tmp_path, source)
    ref = read_json(job / 'source_tones.json')
    for change in ('review', 'missing', 'duplicate', 'unknown', 'mismatch', 'profile'):
        invalid = copy.deepcopy(plan)
        if change == 'review': invalid['classification_source'] = 'unreviewed'
        if change == 'missing': invalid['parts'].pop()
        if change == 'duplicate': invalid['parts'].append(copy.deepcopy(invalid['parts'][0]))
        if change == 'unknown': invalid['parts'][0]['source_material'] = 'no_such_material'
        if change == 'mismatch': invalid['parts'][0]['role_regions']['base'] = [999]
        if change == 'profile': invalid['parts'][0]['transfer_tone']['base'] = float('nan')
        with pytest.raises(ValueError): transfer.validate_plan(invalid, arrays, ref)


def test_explicit_reassignment_and_material_density_edit(tmp_path, source):
    job, plan, arrays = prepare_target(tmp_path, source)
    ref = read_json(job / 'source_tones.json')
    region = transfer.seed_region(arrays, 200, 60)
    old = adjust.preview_manga(job, plan['tone_settings'], persist=False)
    changed = transfer.assign_region(plan, ref, region, 'dress', 'highlight')
    changed['classification_source'] = 'user_reviewed'
    dress = next(p for p in changed['parts'] if p['semantic_id'] == 'dress')
    dress['transfer_tone'] = dict(base=.3, shadow=.6, highlight=.1, beta=True, beta_fill=.6)
    preview = adjust.preview_manga(job, plan['tone_settings'], transfer_plan=changed)
    assert not np.array_equal(np.asarray(old['image']), np.asarray(preview['image']))
    assert read_json(job / 'semantic_plan.json') == plan
    # A CLI export and reopening the editor use the applied draft.
    assert transfer.draft_for_job(job)['parts'] == changed['parts']
    result = adjust.export_manga(job, tmp_path / 'edited.psd', reuse_last=True, expected_context=preview['context'], review=False)
    assert result['passed']
    assert np.array_equal(np.asarray(preview['image']), np.asarray(Image.open(job / 'reconstructed.png')))


def test_stale_inputs_and_output_protection(tmp_path, source):
    job, plan, _ = prepare_target(tmp_path, source)
    preview = adjust.preview_manga(job, plan['tone_settings'])
    source_psd = Path(read_json(source / 'layers.json')['psd_path'])
    before = digest(job / 'semantic_plan.json')
    for output in (source_psd, source_psd.with_name('another.psd')):
        with pytest.raises(ValueError): adjust.export_manga(job, output, review=False)
    for output in (tmp_path / 'new_lineart.png', job / 'reference.png', job / 'manga_target.png'):
        with pytest.raises(ValueError): adjust.export_manga_png(job, output)
    assert digest(job / 'semantic_plan.json') == before
    draft = read_json(job / 'tone_transfer_adjustments.json')
    draft['plan']['parts'][0]['transfer_tone']['beta_fill'] = .2
    write_json(job / 'tone_transfer_adjustments.json', draft)
    with pytest.raises(ValueError, match='プレビュー後'):
        adjust.export_manga_png(job, tmp_path / 'stale.png', expected_context=preview['context'])
    Image.new('RGB', (480, 320), 'black').save(tmp_path / 'new_lineart.png')
    with pytest.raises(ValueError, match='入力が変わり'):
        adjust.preview_manga(job, plan['tone_settings'])


def test_source_change_and_fresh_job_required(tmp_path, source):
    job, plan, _ = prepare_target(tmp_path, source)
    with pytest.raises(ValueError, match='既存job'):
        transfer.prepare_tone_transfer(tmp_path / 'new_lineart.png', job, source_job=source)
    with pytest.raises(ValueError): manga.prepare_manga(tmp_path / 'new_lineart.png', job)
    with pytest.raises(ValueError): manga.register_manga(job)
    old = read_json(source / 'semantic_plan.json'); old['parts'][0]['tone']['base'] = 'black'
    write_json(source / 'semantic_plan.json', old)
    with pytest.raises(ValueError, match='入力が変わり'):
        adjust.preview_manga(job, plan['tone_settings'])


def test_portable_reference_requires_matching_psd(tmp_path, source):
    path = Path(transfer.export_tone_reference(source)['reference'])
    data, _ = transfer.load_reference(path)
    data['psd_hash'] = 'changed'; write_json(path, data)
    with pytest.raises(ValueError, match='ハッシュ'):
        transfer.load_reference(path)


def test_cli_tone_transfer_and_png():
    args = parser().parse_args(['prepare-tone-transfer', '--lineart', 'new.png', '--source-job', 'source', '--job', 'new'])
    assert args.gap_close == 3 and args.threshold == 192
    args = parser().parse_args(['export-manga-png', '--job', 'new', '--output', 'new.png', '--reuse-last'])
    assert args.reuse_last


def test_transparent_ink_and_repeated_first_feature_png(tmp_path, source):
    path = new_lineart(tmp_path / 'transparent.png')
    gray = np.asarray(Image.open(path).convert('L'))
    rgba = np.zeros((*gray.shape, 4), np.uint8)
    rgba[..., 3] = 255 - gray
    Image.fromarray(rgba).save(path)
    transfer.prepare_tone_transfer(path, tmp_path / 'transparent_job', source_job=source)
    _, _, _, arrays = transfer.load_inputs(tmp_path / 'transparent_job', require_review=False)
    assert np.array_equal(arrays['fixed_ink'], gray < 192)
    target = source / 'output_adjusted.png'
    adjust.export_manga_png(source, target)
    before = digest(target)
    adjust.export_manga_png(source, target)
    assert digest(target) == before
    alignment = read_json(source / 'manga_alignment.json')
    with pytest.raises(ValueError):
        adjust.export_manga_png(source, alignment['lineart']['path'])


def test_black_material_highlight_off_reveals_beta(tmp_path, source):
    job, plan, arrays = prepare_target(tmp_path, source)
    region = transfer.seed_region(arrays, 75, 75)
    plan = transfer.assign_region(plan, read_json(job / 'source_tones.json'), region, 'hair', 'highlight')
    plan['classification_source'] = 'astra_reviewed'
    write_json(job / 'semantic_plan.json', plan)
    manga.build_manga(job)
    manga.post_review(job)
    assert Image.open(job / 'post_review/current.png').getpixel((75, 75)) == (255, 255, 255)
    assert Image.open(job / 'post_review/no_highlight.png').getpixel((75, 75)) == (0, 0, 0)
    # The input stroke is still independent of beta.
    assert np.array_equal(np.asarray(Image.open(job / 'post_review/lineart.png'))[..., 0] < 128, arrays['fixed_ink'])
