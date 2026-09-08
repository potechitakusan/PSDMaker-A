import numpy as np

from anime_layer_agent.tone_review import summarize


def test_bright_tones_exclude_black_outline_and_detect_darkening():
    source = np.full((64,64,3), [240,220,225], np.uint8)
    source[:3] = 0
    source[-3:] = 0
    source[:,:3] = 0
    source[:,-3:] = 0
    mask = np.ones((64,64), bool)
    old = summarize(source,mask)
    new = summarize(np.clip(source.astype(int)-20,0,255).astype(np.uint8),mask)
    assert old['bands']['light']['rgb'] == [240,220,225]
    assert old['bands']['light']['L'] - new['bands']['light']['L'] > 5


def test_small_material_is_not_reported_as_measured():
    mask = np.zeros((16,16),bool)
    mask[7:9,7:9] = True
    assert summarize(np.zeros((16,16,3),np.uint8),mask)['status'] == 'insufficient_interior'
