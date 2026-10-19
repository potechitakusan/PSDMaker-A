import numpy as np
import pytest
from PIL import Image, ImageDraw

from anime_layer_agent import manga
from anime_layer_agent.__main__ import parser
from anime_layer_agent.storage import read_json, write_json, digest


def test_foreground_alpha_splits_regions_without_replacing_colors_or_ink(tmp_path):
    reference = tmp_path / 'color.png'
    cutout = tmp_path / 'foreground.png'
    image = Image.new('RGB', (160, 240), '#8aadda')
    ImageDraw.Draw(image).rectangle((35, 40, 125, 239), fill='#553329')
    image.save(reference)
    alpha = Image.new('L', image.size, 0)
    draw = ImageDraw.Draw(alpha)
    draw.rectangle((35, 40, 125, 239), fill=255)
    draw.rectangle((60, 80, 80, 110), fill=0)  # a genuine background hole
    rgba = Image.new('RGBA', image.size, 'red')  # generated RGB must never replace input RGB
    rgba.putalpha(alpha)
    rgba.save(cutout)
    job = tmp_path / 'job'
    manga.prepare_manga(reference, job, redraw_engine='none')
    manga.register_manga(job, max_shift=0)
    before = dict(np.load(job / 'manga_arrays.npz'))
    reference_hash = digest(reference)
    manga.register_manga(job, foreground=cutout, max_shift=0)
    after = dict(np.load(job / 'manga_arrays.npz'))
    for key in ('rgb', 'L', 'pen', 'has_pen'):
        assert np.array_equal(before[key], after[key])
    assert digest(reference) == reference_hash
    assert np.array_equal(after['foreground_mask'], np.asarray(alpha) >= 128)
    bg = read_json(job / 'manga_alignment.json')['foreground']['background_region']
    assert np.all(after['regions'][~after['foreground_mask']] == bg)
    assert not np.any(after['regions'][after['foreground_mask']] == bg)
    assert after['regions'][90, 70] == bg
    assert (job / 'foreground_alignment.png').exists()
    # The resulting plan must still await visual review; changed source alpha invalidates it.
    plan = read_json(job / 'semantic_plan.json')
    assert plan['classification_source'] != 'astra_reviewed'
    plan['classification_source'] = 'astra_reviewed'
    write_json(job / 'semantic_plan.json', plan)
    Image.new('RGBA', image.size, (0, 0, 0, 0)).save(cutout)
    with pytest.raises(ValueError, match='foreground image changed'):
        manga.load_manga_inputs(job)


def test_foreground_rejects_flat_background_and_cli_exposes_input(tmp_path):
    path = tmp_path / 'opaque.png'
    Image.new('RGB', (100, 100), 'white').save(path)
    with pytest.raises(ValueError, match='actual transparent'):
        manga.register_image(path, np.ones((100, 100), np.float32), (100, 100), kind='foreground')
    args = parser().parse_args(['register-manga', '--job', 'work/demo', '--foreground', str(path)])
    assert args.foreground == str(path)
