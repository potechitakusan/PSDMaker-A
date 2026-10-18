"""Optional ComfyUI HTTP client for the Qwen-Image 2.1 manga redraw and pen-line extraction.

Only the standard library is used. The user starts ComfyUI; this module never installs
nodes or models. The graph mirrors the QWEN-Manga workflow of ComfyUI-MangaScreen
(https://github.com/ashukae/ComfyUI-MangaScreen), Copyright (c) 2026 ashukae, MIT License,
up to the decoded image; toning happens locally. Full notice: THIRD_PARTY_NOTICES.md.
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

TURBO_SIGMAS = '1.0, 0.978453, 0.95418, 0.926626, 0.89508, 0.845148, 0.704534, 0.414568, 0.0'
REDRAW_NODES = ('LoadImage', 'ImageScaleToTotalPixels', 'UNETLoader', 'CLIPLoader', 'VAELoader', 'TextEncodeQwenImage21',
                'QwenImage21Cache', 'BasicGuider', 'RandomNoise', 'KSamplerSelect', 'ManualSigmas',
                'SamplerCustomAdvanced', 'VAEDecode', 'SaveImage')
MANGA_LINE_NODES = ('LoadImage', 'Manga2Anime_LineArt_Preprocessor', 'SaveImage')


class ComfyClient:
    def __init__(self, url='http://127.0.0.1:8188', timeout=30):
        self.url = url.rstrip('/')
        self.timeout = timeout
        self.client_id = uuid.uuid4().hex

    def _request(self, path, data=None, headers=None):
        request = urllib.request.Request(self.url + path, data=data, headers=headers or {})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode('utf-8', 'replace')[:2000]
            raise RuntimeError(f'ComfyUI {path} failed with HTTP {exc.code}: {detail}') from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f'ComfyUI is not reachable at {self.url}. Start ComfyUI first ({exc.reason}).') from exc

    def get_json(self, path):
        return json.loads(self._request(path).decode('utf-8'))

    def post_json(self, path, value):
        return json.loads(self._request(path, json.dumps(value).encode('utf-8'), {'Content-Type': 'application/json'}).decode('utf-8'))

    def node_info(self, names):
        info = {}
        for name in names:
            value = self.get_json('/object_info/' + urllib.parse.quote(name))
            if name in value:
                info[name] = value[name]
        return info

    def require_nodes(self, names):
        info = self.node_info(names)
        missing = [name for name in names if name not in info]
        if missing:
            raise RuntimeError('ComfyUI lacks required nodes: ' + ', '.join(missing) +
                               '. Update ComfyUI (Qwen-Image 2.1 nodes) or install comfyui_controlnet_aux (Manga Lineart).')
        return info

    def upload(self, path):
        boundary = uuid.uuid4().hex
        name = f'psdmaker_{uuid.uuid4().hex[:8]}_{Path(path).name}'
        body = b''.join([
            f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{name}"\r\nContent-Type: image/png\r\n\r\n'.encode(),
            Path(path).read_bytes(), b'\r\n',
            f'--{boundary}\r\nContent-Disposition: form-data; name="overwrite"\r\n\r\ntrue\r\n'.encode(),
            f'--{boundary}\r\nContent-Disposition: form-data; name="type"\r\n\r\ninput\r\n--{boundary}--\r\n'.encode()])
        result = json.loads(self._request('/upload/image', body, {'Content-Type': f'multipart/form-data; boundary={boundary}'}).decode('utf-8'))
        return f"{result['subfolder']}/{result['name']}" if result.get('subfolder') else result['name']

    def run(self, graph, wait_seconds=900, poll=2.0):
        queued = self.post_json('/prompt', {'prompt': graph, 'client_id': self.client_id})
        if queued.get('node_errors'):
            raise RuntimeError(f"ComfyUI rejected the graph: {json.dumps(queued['node_errors'])[:2000]}")
        prompt_id = queued['prompt_id']
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            history = self.get_json('/history/' + prompt_id).get(prompt_id)
            if history:
                status = history.get('status', {})
                if status.get('status_str') == 'error':
                    raise RuntimeError(f"ComfyUI execution failed: {json.dumps(status.get('messages', []))[:2000]}")
                images = [image for output in history.get('outputs', {}).values() for image in output.get('images', [])]
                if images:
                    return prompt_id, images
                if status.get('completed'):
                    raise RuntimeError('ComfyUI finished without an output image')
            time.sleep(poll)
        raise TimeoutError(f'ComfyUI did not finish within {wait_seconds} seconds (prompt {prompt_id})')

    def download(self, image, path):
        query = urllib.parse.urlencode({key: image.get(key, '') for key in ('filename', 'subfolder', 'type')})
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(self._request('/view?' + query))
        return Path(path)


def choose_model(info, loader, field, wanted, explicit=None):
    """Pick an installed model file by name hints; an explicit name must exist."""
    options = info[loader]['input']['required'][field][0]
    if isinstance(options, str):  # newer COMBO schema: ("COMBO", {"options": [...]})
        options = info[loader]['input']['required'][field][1].get('options', [])
    if explicit:
        if explicit not in options:
            raise RuntimeError(f'{loader} has no {explicit}. Installed: {options}')
        return explicit
    for hints in wanted:
        found = [name for name in options if all(h in name.lower() for h in hints)]
        if found:
            # Core loaders read safetensors; a gguf with a similar name needs another loader.
            return sorted(found, key=lambda name: (not name.lower().endswith('.safetensors'), name))[0]
    raise RuntimeError(f'No matching {loader} model for {wanted}; pass it explicitly. Installed: {options}')


def redraw_graph(image_name, prompt, unet, clip, vae, seed=1, megapixels=1.0, sigmas=TURBO_SIGMAS,
                 cache_device='cpu', prefix='PsdMaker/manga'):
    return {
        '1': {'class_type': 'LoadImage', 'inputs': {'image': image_name}},
        '2': {'class_type': 'ImageScaleToTotalPixels', 'inputs': {'image': ['1', 0], 'upscale_method': 'lanczos',
                                                                  'megapixels': megapixels, 'resolution_steps': 32}},
        '3': {'class_type': 'UNETLoader', 'inputs': {'unet_name': unet, 'weight_dtype': 'default'}},
        '4': {'class_type': 'CLIPLoader', 'inputs': {'clip_name': clip, 'type': 'qwen_image', 'device': 'default'}},
        '5': {'class_type': 'VAELoader', 'inputs': {'vae_name': vae}},
        '7': {'class_type': 'TextEncodeQwenImage21', 'inputs': {'clip': ['4', 0], 'images.image_1': ['2', 0], 'vae': ['5', 0],
                                                                'prompt': prompt, 'negative_prompt': '', 'resolution': 0}},
        '8': {'class_type': 'QwenImage21Cache', 'inputs': {'model': ['3', 0], 'device': cache_device, 'dtype': 'default'}},
        '9': {'class_type': 'BasicGuider', 'inputs': {'model': ['8', 0], 'conditioning': ['7', 0]}},
        '10': {'class_type': 'RandomNoise', 'inputs': {'noise_seed': int(seed)}},
        '11': {'class_type': 'KSamplerSelect', 'inputs': {'sampler_name': 'euler'}},
        '12': {'class_type': 'ManualSigmas', 'inputs': {'sigmas': sigmas}},
        '13': {'class_type': 'SamplerCustomAdvanced', 'inputs': {'noise': ['10', 0], 'guider': ['9', 0], 'sampler': ['11', 0],
                                                                 'sigmas': ['12', 0], 'latent_image': ['7', 2]}},
        '14': {'class_type': 'VAEDecode', 'inputs': {'samples': ['13', 0], 'vae': ['5', 0]}},
        '16': {'class_type': 'SaveImage', 'inputs': {'images': ['14', 0], 'filename_prefix': prefix}},
    }


def manga_line_graph(image_name, resolution, prefix='PsdMaker/manga_line'):
    return {
        '1': {'class_type': 'LoadImage', 'inputs': {'image': image_name}},
        '21': {'class_type': 'Manga2Anime_LineArt_Preprocessor', 'inputs': {'image': ['1', 0], 'resolution': int(resolution)}},
        '16': {'class_type': 'SaveImage', 'inputs': {'images': ['21', 0], 'filename_prefix': prefix}},
    }
