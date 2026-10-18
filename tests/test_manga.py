import json
import threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from io import BytesIO

import numpy as np
import pytest
from PIL import Image, ImageDraw
from psd_tools import PSDImage

from anime_layer_agent import manga, manga_tone as tone
from anime_layer_agent.__main__ import parser
from anime_layer_agent.storage import read_json, write_json, digest

W, H = 360, 240


def scene(mode):
    """Synthetic fixture: color illustration, its manga redraw, and its lineart."""
    background = {'color': (225, 235, 245)}.get(mode, (255, 255, 255))
    picture = Image.new('RGB', (W, H), background)
    draw = ImageDraw.Draw(picture)
    if mode == 'color':
        draw.rectangle((100, 120, 260, 235), fill=(200, 40, 50)); draw.rectangle((210, 120, 260, 235), fill=(130, 20, 30))
        draw.ellipse((130, 30, 230, 130), fill=(250, 215, 190)); draw.pieslice((125, 20, 235, 110), 180, 360, fill=(60, 40, 30))
    elif mode == 'redraw':
        draw.rectangle((100, 120, 260, 235), fill=(170, 170, 170)); draw.rectangle((210, 120, 260, 235), fill=(95, 95, 95))
        draw.ellipse((130, 30, 230, 130), fill='white'); draw.pieslice((125, 20, 235, 110), 180, 360, fill='black')
    draw.rectangle((100, 120, 260, 235), outline='black', width=2)
    draw.ellipse((130, 30, 230, 130), outline='black', width=2); draw.arc((125, 20, 235, 110), 180, 360, fill='black', width=2)
    draw.line((125, 65, 235, 65), fill='black', width=2); draw.line((210, 120, 210, 235), fill='black', width=2)
    return picture


@pytest.fixture
def inputs(tmp_path):
    folder = tmp_path / 'in'
    folder.mkdir()
    scene('color').save(folder / 'color.png')
    redraw = scene('redraw')
    # imagegen-like output: square canvas, letterboxed, shifted by 2 px
    small = redraw.resize((300, 200), Image.Resampling.LANCZOS)
    canvas = Image.new('RGB', (300, 300), 'white'); canvas.paste(small, (2, 51))
    canvas.save(folder / 'redraw.png')
    scene('lineart').resize((180, 120), Image.Resampling.LANCZOS).save(folder / 'lineart.png')
    return folder


def registered(tmp_path, inputs, **kwargs):
    job = tmp_path / 'manga'
    manga.prepare_manga(inputs / 'color.png', job, max_side=300)
    result = manga.register_manga(job, redraw=inputs / 'redraw.png', lineart=inputs / 'lineart.png', **kwargs)
    return job, result


def region_at(job, x, y):
    return int(np.load(job / 'manga_arrays.npz')['regions'][y, x])


def reviewed_plan(job, tone_format='dots'):
    """Stand-in for Codex: name each region from the color illustration."""
    plan = read_json(job / 'semantic_plan.json')
    arrays = np.load(job / 'manga_arrays.npz')
    regions, rgb = arrays['regions'], arrays['rgb'] * 255
    groups = {name: set() for name in ('background', 'hair', 'face', 'dress')}
    for n in np.unique(regions):
        r, g, b = np.median(rgb[regions == n], axis=0)
        name = 'dress' if r > 110 and g < 80 else 'hair' if r < 90 else 'face' if r > 240 and g > 190 and b < 210 else 'background'
        groups[name].add(int(n))
    hair, face, dress, background = groups['hair'], groups['face'], groups['dress'], groups['background']
    plan['classification_source'] = 'astra_reviewed'
    plan['tone_settings'].update(tone_format=tone_format, screen_lines=60)
    plan['parts'] = [dict(semantic_id='background', display_name='背景', regions=sorted(background), tone=dict(base='white')),
                     dict(semantic_id='hair', display_name='髪', group_path=['キャラクター', '頭'], regions=sorted(hair), tone=dict(base='black')),
                     dict(semantic_id='face', display_name='顔', group_path=['キャラクター', '頭'], regions=sorted(face), tone=dict(base='white')),
                     dict(semantic_id='dress', display_name='服', group_path=['キャラクター', '衣装'], regions=sorted(dress))]
    write_json(job / 'semantic_plan.json', plan)
    return plan


def reference_print_screen(cover, pitch):
    """print_screen of ComfyUI-MangaScreen (MIT), kept verbatim as the oracle."""
    import cv2
    h, w = cover.shape
    s = 3
    y, x = np.mgrid[0:h * s, 0:w * s].astype(np.float32)
    a = np.deg2rad(45.0)
    u = (x * np.cos(a) + y * np.sin(a)) / (pitch * s)
    v = (-x * np.sin(a) + y * np.cos(a)) / (pitch * s)
    spot = 1 - (np.cos(2 * np.pi * u) + np.cos(2 * np.pi * v) + 2) / 4
    c = cv2.resize(cover, (w * s, h * s), interpolation=cv2.INTER_LINEAR)
    return cv2.resize((spot < c).astype(np.float32), (w, h), interpolation=cv2.INTER_AREA) > 0.5


def test_screen_matches_reference_and_layers_stack():
    thresholds = tone.screen_thresholds(96, 128, 128 / 20)
    for coverage in (.18, .42, .7):
        flat = np.full((96, 128), coverage, np.float32)
        assert np.array_equal(tone.dots(flat, thresholds), reference_print_screen(flat, 128 / 20))
    light, dark = tone.dots(np.full((96, 128), .18), thresholds), tone.dots(np.full((96, 128), .42), thresholds)
    assert not np.any(light & ~dark)  # a darker tone always contains the lighter dots: layers stack cleanly
    assert not tone.dots(np.zeros((96, 128)), thresholds).any() and tone.dots(np.ones((96, 128)), thresholds).mean() > .99


def test_strokes_masses_and_screen_dots_are_separated():
    L = np.ones((512, 1024), np.float32)                   # k = 1, the scale the thresholds are tuned for
    L[40:43, 40:900] = 0                                   # long thin line
    L[200:400, 60:300] = 0                                 # solid mass
    yy, xx = np.mgrid[:512, :1024]
    L[(yy > 200) & (xx > 500) & (yy % 6 < 4) & (xx % 6 < 4)] = 0   # existing dot screen (44%)
    page = tone.analyze_page(L, dict(white_lines=0))
    assert page['strokes'][41, 400] and not page['mass'][41, 400]
    assert page['mass'][300, 180] and not page['strokes'][300, 180]
    assert page['strokes'][220:500, 520:1000].mean() < .02  # dots are values, not lines
    assert page['level'][300, 750] in (1, 2)                # descreened into a mid tone


def test_generated_image_is_mapped_back_by_scale(tmp_path, inputs):
    job, result = registered(tmp_path, inputs)
    alignment = read_json(job / 'manga_alignment.json')
    assert alignment['redraw']['fit'] == 'contain' and alignment['lineart']['fit'] == 'stretch'
    assert alignment['redraw']['original_px_per_generated_px'] == [pytest.approx(1.2), pytest.approx(1.2)]
    assert alignment['lineart']['original_px_per_generated_px'] == [2.0, 2.0]
    assert alignment['redraw']['edge_distance_after_flow_px'] <= 1.5
    assert result['region_source'] == 'lineart' and result['regions'] >= 6
    record = read_json(job / 'manga_job.json')
    assert record['generation_input']['size'] == [300, 200] and record['generation_input']['scale_to_original'] == [1.2, 1.2]
    assert (job / 'manga_alignment.png').exists() and (job / 'manga_regions_overview.png').exists()
    plan = read_json(job / 'semantic_plan.json')
    assert plan['classification_source'] == 'heuristic' and plan['parts'] == []


def test_build_layers_readback_and_review(tmp_path, inputs):
    job, _ = registered(tmp_path, inputs)
    with pytest.raises(ValueError, match='astra_reviewed'):
        manga.build_manga(job)
    reviewed_plan(job)
    result = manga.build_manga(job, tmp_path / 'out' / 'output.psd')
    assert result['passed'] and result['readback']['max_channel_error'] == 0
    manifest = read_json(job / 'layers.json')
    names = {layer['name']: layer for layer in manifest['layers']}
    assert {'紙', '髪_ベタ', '線画', '服_ベース', '服_影'} <= set(names)
    assert not any(name.endswith('_ベタ') and not name.startswith('髪') for name in names)  # white or noise-only parts get no beta
    assert names['服_ベース']['group_path'] == ['ベタ・トーン', 'キャラクター', '衣装', '服 [dress]']
    assert not any(name.startswith('背景_') for name in names)  # a white part needs no tone layer
    solvers = {s['semantic_id']: s for s in manifest['solvers']}
    assert solvers['face']['shadow_pixels'] < .05 * solvers['face']['pixels']  # only edge slivers, no halo from the black hair
    roles = [layer['group'] for layer in manifest['layers']]
    assert roles.index('Paper') < roles.index('Base') < roles.index('Beta') < roles.index('Lineart')
    # Actual saved beta pixels must not duplicate the separate lineart layer.
    line_mask = np.zeros((H, W), bool)
    line_item = names['線画']
    lx0, ly0, lx1, ly1 = line_item['bbox']
    line_mask[ly0:ly1, lx0:lx1] = np.asarray(Image.open(job / line_item['image_path']))[..., 3] > 0
    for layer in manifest['layers']:
        if layer['group'] == 'Beta':
            bx0, by0, bx1, by1 = layer['bbox']
            fill = np.asarray(Image.open(job / layer['image_path']))[..., 3] > 0
            assert not np.any(fill & line_mask[by0:by1, bx0:bx1])
    psd = PSDImage.open(tmp_path / 'out' / 'output.psd')
    assert {layer.name for layer in psd.descendants() if not layer.is_group()} == set(names)
    assert names['髪_ベタ']['group_path'] == names['髪_ベース']['group_path'] == ['ベタ・トーン', 'キャラクター', '頭', '髪 [hair]']
    beta = np.asarray(Image.open(job / names['髪_ベタ']['image_path']))[..., 3] > 0
    x0, y0, x1, y1 = names['髪_ベタ']['bbox']
    assert beta[40 - y0, 180 - x0] and y1 <= 70  # the hair is beta; outlines and the dress are not
    # A black-base part keeps a dark tone under its beta, so erasing the beta leaves tone, not paper.
    hair_tone = full_alpha(job, names['髪_ベース'])[y0:y1, x0:x1][beta]
    assert .2 < hair_tone.mean() < .6
    assert solvers['dress']['base_coverage'] == pytest.approx(.18) and solvers['dress']['shadow_pixels'] > 1000
    review = manga.post_review(job)
    assessment = read_json(review['assessment'])
    with pytest.raises(ValueError, match='Unreviewed'):
        manga.finish_review(job)
    for key, check in assessment['checks'].items():
        check.update(status='pass', notes='synthetic fixture checked', evidence=['post_review/overview.png'])
    write_json(review['assessment'], assessment)
    assert manga.finish_review(job)['passed']
    assert (tmp_path / 'out' / 'post_review_result.json').exists() and (job / 'time_log.md').exists()


def full_alpha(job, item, size=(H, W)):
    alpha = np.zeros(size, bool)
    x0, y0, x1, y1 = item['bbox']
    alpha[y0:y1, x0:x1] = np.asarray(Image.open(job / item['image_path']))[..., 3] > 0
    return alpha


@pytest.mark.parametrize('mode', ['node', 'posterize'])
def test_tones_continue_under_beta(mode):
    h, w = 160, 320
    L = np.full((h, w), .6, np.float32)
    L[40:120, 160:280] = 0
    arrays = dict(rgb=np.zeros((h, w, 3), np.float32), L=L, pen=np.zeros((h, w), np.float32), has_pen=np.bool_(False),
                  redraw_valid=np.ones((h, w), bool), regions=np.ones((h, w), np.int32))
    settings = dict(screen_lines=40, min_region=0) if mode == 'node' else dict(mode='posterize', dpi=600, lpi=60, min_region=0)
    plan = dict(parts=[dict(semantic_id='coat', display_name='コート', group_path=['キャラクター'], regions=[1])], tone_settings=settings)
    images = {}
    result = manga.render_manga(arrays, plan, lambda file, image: images.__setitem__(file, np.asarray(image)))
    layers = {layer['name']: layer for layer in result['manifest']['layers']}
    assert [layer['name'] for layer in result['manifest']['layers']][1:4] == ['コート_ベース', 'コート_影', 'コート_ベタ']
    assert all(layers[n]['group_path'] == ['ベタ・トーン', 'キャラクター', 'コート [coat]'] for n in ('コート_ベース', 'コート_影', 'コート_ベタ'))

    def alpha(name):
        out = np.zeros((h, w), bool)
        x0, y0, x1, y1 = layers[name]['bbox']
        out[y0:y1, x0:x1] = images[layers[name]['image_path']][..., 3] > 0
        return out[60:100, 180:260]

    assert alpha('コート_ベタ').all()
    solver = result['manifest']['solvers'][0]
    assert solver['beta_fill_coverage'] > solver['base_coverage'] > 0
    # Base and shadow screens continue below the beta; the shadow uses the darker tone.
    assert 0 < alpha('コート_ベース').mean() < alpha('コート_影').mean() < .7
    assert (result['target'][60:100, 180:260] == 0).all()  # still black while the beta is shown


def beta_page(L):
    page = tone.analyze_page(L, {})
    page['ink'] = np.zeros(L.shape, bool)
    return page


@pytest.mark.parametrize('blocks, options, expected', [
    ([(10, 10)] * 6, dict(beta_min_area=200, beta_min_ratio=0), 'specks'),  # components under beta_min_area
    ([(10, 10)], {}, 'negligible'),      # one real component, but 0.05% of the part
    ([(60, 60)], {}, 'kept'),            # 1.8% of the part: a real black area
])
def test_beta_noise_filter(blocks, options, expected):
    L = np.full((200, 1024), .95, np.float32)
    for i, (bh, bw) in enumerate(blocks):
        L[60:60 + bh, 100 + i * 120:100 + i * 120 + bw] = 0
    mask = np.ones(L.shape, bool)
    settings = tone.validate_settings(options)
    item = manga.part_target(mask, dict(semantic_id='skin'), beta_page(L), settings)
    if expected == 'kept':
        assert item['part_beta'].sum() >= 3000 and item['beta_specks_removed'] == 0
    else:
        assert not item['part_beta'].any()
        assert (item['beta_specks_removed'] if expected == 'specks' else item['beta_dropped_as_negligible']) > 0
        assert item['t'][62, 102] == pytest.approx(item['base'])  # the noise takes the surrounding tone
    forced = manga.part_target(mask, dict(semantic_id='skin', tone=dict(beta='force')), beta_page(L), settings)
    assert forced['part_beta'].any()  # Codex can keep small black details explicitly


def test_gray_format_and_part_overrides(tmp_path, inputs):
    job, _ = registered(tmp_path, inputs)
    plan = reviewed_plan(job, tone_format='gray')
    plan['parts'][3]['tone'] = dict(base='dark', shadow='none')
    write_json(job / 'semantic_plan.json', plan)
    assert manga.build_manga(job)['passed']
    manifest = read_json(job / 'layers.json')
    names = {layer['name']: layer for layer in manifest['layers']}
    assert '服_影' not in names
    base = np.asarray(Image.open(job / names['服_ベース']['image_path']))
    opaque = base[..., 3] > 0
    assert np.all(np.abs(base[opaque, 0].astype(int) - round((1 - .42) * 255)) <= 1)
    # The shared post-review command routes manga jobs to the manga checklist.
    import sys
    from anime_layer_agent.__main__ import main
    monkey_argv = sys.argv
    try:
        sys.argv = ['anime_layer_agent', 'post-review', '--job', str(job)]
        assert main() == 0
    finally:
        sys.argv = monkey_argv
    assert set(read_json(job / 'post_review_assessment.json')['checks']) == set(manga.CHECKS)


@pytest.mark.parametrize('change, message', [
    (lambda plan: plan['parts'][0]['regions'].pop(), 'Unassigned'),
    (lambda plan: plan['parts'][1]['regions'].extend(plan['parts'][2]['regions']), 'Duplicate'),
    (lambda plan: plan['parts'][3].update(tone=dict(base='purple')), 'tone.base'),
    (lambda plan: plan['tone_settings'].update(black_point=.9), 'black_point'),
    (lambda plan: plan['parts'][2].update(display_name='髪'), 'unique'),
])
def test_plan_validation(tmp_path, inputs, change, message):
    job, _ = registered(tmp_path, inputs)
    plan = reviewed_plan(job)
    change(plan)
    write_json(job / 'semantic_plan.json', plan)
    with pytest.raises(ValueError, match=message):
        manga.build_manga(job)


def test_reregistration_invalidates_region_assignments(tmp_path, inputs):
    job, _ = registered(tmp_path, inputs)
    reviewed_plan(job)
    manga.register_manga(job, redraw=inputs / 'redraw.png', lineart=inputs / 'lineart.png', min_area=400)
    plan = read_json(job / 'semantic_plan.json')
    assert plan['classification_source'] == 'partial_review' and all(p['regions'] == [] for p in plan['parts'])
    assert list((job / 'history').glob('semantic_plan_*.json'))


def test_changed_generated_image_blocks_build(tmp_path, inputs):
    job, _ = registered(tmp_path, inputs)
    reviewed_plan(job)
    Image.new('RGB', (300, 300), 'white').save(inputs / 'redraw.png')
    with pytest.raises(ValueError, match='redraw image changed'):
        manga.build_manga(job)


def test_levels_path_without_redraw_and_cli(tmp_path, inputs):
    job = tmp_path / 'lite'
    manga.prepare_manga(inputs / 'color.png', job, redraw_engine='none', line_engine='provided')
    result = manga.register_manga(job, lineart=inputs / 'lineart.png')
    assert result['value_source'] == 'levels'
    args = vars(parser().parse_args(['register-manga', '--job', str(job), '--fit', 'stretch']))
    assert args['fit'] == 'stretch'
    assert vars(parser().parse_args(['run-comfy-manga', '--job', 'x', '--task', 'lineart']))['task'] == 'lineart'


def test_source_job_parts_are_reused(tmp_path):
    from anime_layer_agent.compact import prepare_compact, build_compact
    from anime_layer_agent.artist import configure_editing
    from anime_layer_agent.pipeline import demo
    source = tmp_path / 'source'
    files = demo(tmp_path / 'demo')
    prepare_compact(**files, job=source)
    write_json(source / 'semantic_plan.json', dict(classification_source='astra_reviewed', parts=[
        dict(semantic_id='background', regions=[1]), dict(semantic_id='cloth', display_name='布', palette_id='cloth', regions=[2])]))
    configure_editing(source, base_mode='dominant'); build_compact(source)
    job = tmp_path / 'from_source'
    assert manga.prepare_manga(files['reference'], job, redraw_engine='none', source_job=source)['imported_parts'] == 2
    result = manga.register_manga(job)
    assert result['region_source'] == 'source_job'
    plan = read_json(job / 'semantic_plan.json')
    assert [p['semantic_id'] for p in plan['parts']] == ['background', 'cloth'] and plan['parts'][1]['regions'] == [2]
    plan['classification_source'] = 'astra_reviewed'
    write_json(job / 'semantic_plan.json', plan)
    assert manga.build_manga(job)['passed']
    other = tmp_path / 'other.png'
    Image.new('RGB', (256, 256), 'white').save(other)
    with pytest.raises(ValueError, match='differs'):
        manga.prepare_manga(other, tmp_path / 'mismatch', source_job=source)


class FakeComfy(BaseHTTPRequestHandler):
    nodes = {}
    prompts = []

    def log_message(self, *args):
        pass

    def reply(self, value, kind='application/json'):
        body = value if isinstance(value, bytes) else json.dumps(value).encode()
        self.send_response(200); self.send_header('Content-Type', kind); self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith('/object_info/'):
            name = self.path.rsplit('/', 1)[1]
            return self.reply({name: self.nodes[name]} if name in self.nodes else {})
        if self.path.startswith('/history/'):
            return self.reply({'p1': {'status': {'completed': True}, 'outputs': {'16': {'images': [{'filename': 'out.png', 'subfolder': '', 'type': 'output'}]}}}})
        if self.path.startswith('/view'):
            buffer = BytesIO(); scene('redraw').resize((300, 200)).save(buffer, 'PNG')
            return self.reply(buffer.getvalue(), 'image/png')
        if self.path == '/system_stats':
            return self.reply({'system': {'comfyui_version': 'test'}})
        self.send_response(404); self.end_headers()

    def do_POST(self):
        body = self.rfile.read(int(self.headers['Content-Length']))
        if self.path == '/upload/image':
            return self.reply({'name': 'uploaded.png', 'subfolder': '', 'type': 'input'})
        if self.path == '/prompt':
            self.prompts.append(json.loads(body))
            return self.reply({'prompt_id': 'p1', 'node_errors': {}})


@pytest.fixture
def comfy():
    from anime_layer_agent.comfy_client import REDRAW_NODES
    combo = lambda *names: {'input': {'required': {key: [list(values)] for key, values in names}}}
    FakeComfy.nodes = {name: {'input': {'required': {}}} for name in REDRAW_NODES}
    FakeComfy.nodes['UNETLoader'] = combo(('unet_name', ['other.safetensors', 'qwen_image_2.1_turbo_int8_convrot.safetensors']))
    FakeComfy.nodes['CLIPLoader'] = combo(('clip_name', ['qwen3vl_8b_int8_convrot.safetensors']))
    FakeComfy.nodes['VAELoader'] = combo(('vae_name', ['qwen_image_2.1_vae_bf16.safetensors']))
    FakeComfy.prompts = []
    server = ThreadingHTTPServer(('127.0.0.1', 0), FakeComfy)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    yield f'http://127.0.0.1:{server.server_address[1]}'
    server.shutdown()


def test_comfy_redraw_records_models_and_maps_back(tmp_path, inputs, comfy):
    job = tmp_path / 'qwen'
    manga.prepare_manga(inputs / 'color.png', job, redraw_engine='qwen', max_side=300)
    result = manga.run_comfy_manga(job, url=comfy, seed=7)
    graph = FakeComfy.prompts[-1]['prompt']
    assert graph['3']['inputs']['unet_name'] == 'qwen_image_2.1_turbo_int8_convrot.safetensors'
    assert graph['7']['inputs']['images.image_1'] == ['2', 0] and graph['10']['inputs']['noise_seed'] == 7
    assert '<image1>' in graph['7']['inputs']['prompt']
    record = read_json(job / 'manga_job.json')['generated']['redraw']
    assert record['engine'] == 'qwen' and record['hash'] == digest(result['image']) and record['comfyui_version'] == 'test'
    with pytest.raises(RuntimeError, match='Manga2Anime_LineArt_Preprocessor'):
        manga.run_comfy_manga(job, task='lineart', url=comfy)
    registered_result = manga.register_manga(job, lineart=inputs / 'lineart.png')
    assert registered_result['value_source'] == 'redraw'


def test_comfy_unreachable_message(tmp_path, inputs):
    job = tmp_path / 'offline'
    manga.prepare_manga(inputs / 'color.png', job, redraw_engine='qwen')
    with pytest.raises(RuntimeError, match='not reachable'):
        manga.run_comfy_manga(job, url='http://127.0.0.1:9')
