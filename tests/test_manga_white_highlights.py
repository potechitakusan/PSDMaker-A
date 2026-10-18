import copy

import numpy as np
import pytest

from anime_layer_agent import manga, manga_tone as tone


@pytest.mark.parametrize('transfer', [False, True])
def test_explicit_white_highlights_keep_composite_and_material_values(transfer):
    h, w = 192, 384
    values = np.ones((h, w), np.float32)
    values[:, 240:320] = .6
    values[:, 320:] = .1
    regions = np.ones((h, w), np.int32)
    regions[:, 240:320] = 2
    regions[:, 320:] = 3
    ink = np.zeros((h, w), bool)
    ink[90:92, 20:140] = True
    arrays = dict(L=values, rgb=np.repeat(values[..., None], 3, axis=2),
                  regions=regions, pen=ink.astype(np.float32), has_pen=True, fixed_ink=ink)
    part = dict(semantic_id='background', display_name='Background', regions=[1, 2, 3],
                tone=dict(base='auto', shadow='auto', highlight='white', beta=True))
    plan = dict(parts=[part], tone_settings=dict(tone.DEFAULT_SETTINGS))
    if transfer:
        plan['pipeline'] = 'tone_transfer'
        part.update(source_material='background', role_regions=dict(highlight=[1], shadow=[2], beta=[3], base=[]),
                    transfer_tone=dict(base=0., shadow=.42, highlight=0., beta=True, beta_fill=.42))
    before = manga.render_manga(arrays, plan)
    changed = copy.deepcopy(plan)
    changed['parts'][0]['separate_white_highlights'] = True
    images = {}
    after = manga.render_manga(arrays, changed, layer_sink=lambda path, im: images.setdefault(path, im))
    assert np.array_equal(before['target'], after['target'])
    assert before['manifest']['solvers'] == after['manifest']['solvers']
    assert before['manifest']['settings'] == after['manifest']['settings']
    added = [x for x in after['manifest']['layers'] if x['name'] == 'Background_ハイライト']
    assert len(added) == 1 and added[0]['group'] == 'Highlights'
    rgba = np.asarray(images[added[0]['image_path']])
    assert np.all(rgba[rgba[..., 3] > 0, :3] == 255)
    assert any(x['group'] == 'Shadows' for x in after['manifest']['layers'])
