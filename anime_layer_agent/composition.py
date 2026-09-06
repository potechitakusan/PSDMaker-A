import uuid
from pathlib import Path
import numpy as np
from PIL import Image
from psd_tools import PSDImage
from psd_tools.api.layers import Group, PixelLayer
from psd_tools.constants import BlendMode

from .regions import bbox_of
from .storage import replace_file, save_image, write_json

BLENDS = {"normal": lambda b, s: np.broadcast_to(s, b.shape), "multiply": lambda b, s: b * s, "screen": lambda b, s: 1 - (1 - b) * (1 - s)}
PSD_MODES = {"normal": BlendMode.NORMAL, "multiply": BlendMode.MULTIPLY, "screen": BlendMode.SCREEN}


def composite_rgb(base, source, mode, opacity):
    return base * (1 - opacity) + BLENDS[mode](base, source) * opacity


def solve_color(base, target, modes=("multiply",)):
    """Search quantized opacity and least-squares color for each blend mode."""
    base = np.asarray(base, dtype=np.float64).reshape(-1, 3)
    target = np.asarray(target, dtype=np.float64).reshape(-1, 3)
    step = max(1, len(base) // 4096)
    base, target = base[::step], target[::step]
    best = None
    candidates = []
    for mode in modes:
        mode_best = None
        for alpha_byte in range(13, 256, 3):
            opacity = alpha_byte / 255
            if mode == "multiply":
                slope, intercept = base * opacity, base * (1 - opacity)
            elif mode == "screen":
                slope, intercept = (1 - base) * opacity, base
            elif mode == "normal":
                slope, intercept = np.full_like(base, opacity), base * (1 - opacity)
            else:
                raise ValueError(f"Unsupported solver: {mode}")
            color = np.sum(slope * (target - intercept), axis=0) / np.maximum(np.sum(slope * slope, axis=0), 1e-12)
            color = np.round(np.clip(color, 0, 1) * 255) / 255
            error = float(np.mean((composite_rgb(base, color, mode, opacity) - target) ** 2))
            result = {"color": color.tolist(), "opacity": opacity, "blend_mode": mode, "error": error, "mae": float(np.mean(np.abs(composite_rgb(base, color, mode, opacity) - target)) * 255)}
            if mode_best is None or error < mode_best["error"] - 1e-14:
                mode_best = result
        # Include fully opaque mode exactly, independently of the grid stride.
        opacity = 1.0
        slope = base if mode == "multiply" else 1 - base if mode == "screen" else np.ones_like(base)
        intercept = base if mode == "screen" else np.zeros_like(base)
        color = np.round(np.clip(np.sum(slope * (target - intercept), axis=0) / np.maximum(np.sum(slope * slope, axis=0), 1e-12), 0, 1) * 255) / 255
        err = float(np.mean((composite_rgb(base, color, mode, opacity) - target) ** 2))
        if err < mode_best["error"]:
            mode_best = {"color": color.tolist(), "opacity": opacity, "blend_mode": mode, "error": err, "mae": float(np.mean(np.abs(composite_rgb(base, color, mode, opacity) - target)) * 255)}
        candidates.append(mode_best)
        if best is None or mode_best["error"] < best["error"] - 1e-12:
            best = mode_best
    return dict(best, candidates=candidates)


def make_layers(job, plan, records):
    arrays = np.load(job / "preprocessed.npz")
    underlying, ink, alpha = arrays["underlying"], arrays["ink"], arrays["alpha"]
    labels = np.load(job / "labels.npz")
    region_data = __import__("json").loads((job / "regions.json").read_text(encoding="utf-8"))
    gmap = {v: int(k) for k, v in region_data["geometry_names"].items()}
    cmap = {v: int(k) for k, v in region_data["color_names"].items()}
    layers, solvers = [], []
    def add(name, group, rgb, mask, mode="normal", opacity=1.0):
        if not np.any(mask):
            if group != "Lineart":
                return
            bounds = [0, 0, 1, 1]
        else:
            bounds = bbox_of(mask > 0)
        x0, y0, x1, y1 = bounds
        full = np.broadcast_to(rgb, underlying.shape)
        rgba = np.concatenate([full[y0:y1, x0:x1], mask[y0:y1, x0:x1, None]], axis=2)
        image = Image.fromarray(np.round(np.clip(rgba, 0, 1) * 255).astype(np.uint8))
        path = f"layers/{len(layers):04d}.png"
        save_image(image, job / path)
        layers.append({"name": name, "group": group, "image_path": path, "bbox": bounds, "blend_mode": mode, "opacity": round(opacity * 255)})
    for part in plan["parts"]:
        part_mask = np.isin(labels["geometry"], [gmap[g] for g in part["geometry_regions"]])
        base_mask = np.isin(labels["color"], [cmap[c] for c in part.get("base_regions", [])])
        # Median of actual base pixels, excluding shadows/highlights and line edge.
        samples = underlying[base_mask & (alpha < 0.2)]
        if not len(samples):
            samples = underlying[part_mask & (alpha < 0.2)]
        if not len(samples):
            samples = underlying[part_mask]
        base_color = np.median(samples, axis=0)
        base_rgb = underlying.copy()
        overlays = part.get("shadow_regions", []) + part.get("highlight_regions", []) + part.get("rim_light_regions", []) + part.get("ambient_light_regions", [])
        overlay_mask = np.isin(labels["color"], [cmap[c] for c in overlays])
        base_rgb[overlay_mask] = base_color
        name = part["semantic_id"]
        group = "Background" if part.get("kind") == "background" or name == "background" else "Base"
        add(f"{name}_Base", group, base_rgb, part_mask.astype(np.float32))
        for role, target_group, default_modes in [("shadow_regions", "Shadows", (part.get("shadow_blend_mode", "multiply"),)), ("highlight_regions", "Highlights", ("screen", "normal")), ("rim_light_regions", "Highlights", ("screen", "normal")), ("ambient_light_regions", "Highlights", ("screen", "normal"))]:
            for i, rid in enumerate(part.get(role, []), 1):
                mask = labels["color"] == cmap[rid]
                modes = default_modes
                if target_group == "Highlights" and part.get("highlight_blend_mode", "auto") != "auto":
                    modes = (part["highlight_blend_mode"],)
                fit_mask = mask & (alpha < 0.2)
                if not fit_mask.any():
                    fit_mask = mask
                solution = solve_color(base_rgb[fit_mask], underlying[fit_mask], modes)
                solvers.append({"semantic_id": name, "region_id": rid, "base_color": base_color.tolist(), "shadow_color" if target_group == "Shadows" else "highlight_color": solution["color"], **solution})
                add(f"{name}_{'Shadow' if target_group == 'Shadows' else 'Highlight'}_{i:02d}_{rid}", target_group, np.array(solution["color"]), mask.astype(np.float32), solution["blend_mode"], solution["opacity"])
    add("Lineart", "Lineart", ink, alpha)
    order = {name: i for i, name in enumerate(("Background", "Base", "Shadows", "Highlights", "Lineart"))}
    layers.sort(key=lambda layer: order[layer["group"]])
    manifest = {"size": [underlying.shape[1], underlying.shape[0]], "layers": layers, "solvers": solvers}
    write_json(job / "layers.json", manifest)
    return manifest


def export_psd(job, manifest, output):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    psd = PSDImage.new("RGBA", tuple(manifest["size"]), color=(0, 0, 0, 0))
    character = Group.new(psd, "Character")
    character.blend_mode = BlendMode.PASS_THROUGH
    groups = {}
    for name in ("Background", "Base", "Shadows", "Highlights"):
        groups[name] = Group.new(character, name)
        groups[name].blend_mode = BlendMode.PASS_THROUGH
    for item in manifest["layers"]:
        parent = character if item["group"] == "Lineart" else groups[item["group"]]
        with Image.open(job / item["image_path"]) as source:
            layer = PixelLayer.frompil(source.convert("RGBA"), parent, name="Layer", left=item["bbox"][0], top=item["bbox"][1])
        # The public setter also writes the Unicode name and a MacRoman fallback.
        layer.name = item["name"]
        layer.blend_mode = PSD_MODES[item["blend_mode"]]
        layer.opacity = item["opacity"]
    temp = output.with_name(f".{output.name}.{uuid.uuid4().hex}.tmp")
    try:
        psd.save(temp)
        # Validate the file before replacing a previously valid PSD.
        check = PSDImage.open(temp)
        if check.size != tuple(manifest["size"]):
            raise ValueError("Saved PSD size mismatch")
        replace_file(temp, output)
    finally:
        temp.unlink(missing_ok=True)
    return output


def render_generated_psd(psd):
    """ROI compositor for the exact basic layer subset emitted by this tool.

    Call only for a generated PSD whose identity was checked by the caller.
    The general viewer falls back to psd-tools for unrecognized/edited PSDs.
    """
    canvas = np.zeros((psd.height, psd.width, 4), dtype=np.float32)
    reverse_modes = {value: key for key, value in PSD_MODES.items()}
    def visit(parent):
        for layer in parent:
            if not layer.visible:
                continue
            if layer.has_mask() or layer.has_vector_mask() or layer.has_effects() or layer.clipping:
                raise ValueError("Unsupported generated PSD feature")
            if layer.is_group():
                if layer.blend_mode != BlendMode.PASS_THROUGH or layer.opacity != 255:
                    raise ValueError("Unsupported group blending")
                visit(layer)
                continue
            if layer.kind != "pixel" or layer.blend_mode not in reverse_modes or layer.fill_opacity != 255:
                raise ValueError("Unsupported pixel layer")
            x0, y0 = max(0, layer.left), max(0, layer.top)
            x1, y1 = min(psd.width, layer.right), min(psd.height, layer.bottom)
            if x1 <= x0 or y1 <= y0:
                continue
            source = layer.topil(apply_icc=False).convert("RGBA")
            source = np.asarray(source.crop((x0 - layer.left, y0 - layer.top, x1 - layer.left, y1 - layer.top)), dtype=np.float32) / 255
            dest = canvas[y0:y1, x0:x1]
            a_src = source[..., 3:4] * (layer.opacity / 255)
            a_dst = dest[..., 3:4]
            alpha = a_src + a_dst * (1 - a_src)
            blend = BLENDS[reverse_modes[layer.blend_mode]](dest[..., :3], source[..., :3])
            premult = (1 - a_src) * a_dst * dest[..., :3] + (1 - a_dst) * a_src * source[..., :3] + a_src * a_dst * blend
            dest[..., :3] = premult / np.maximum(alpha, 1e-8)
            dest[..., 3:4] = alpha
    visit(psd)
    return Image.fromarray(np.round(np.clip(canvas, 0, 1) * 255).astype(np.uint8))


def render_psd(path, generated=False):
    psd = PSDImage.open(path)
    if generated:
        image = render_generated_psd(psd)
        background = Image.new("RGBA", image.size, "white")
        return Image.alpha_composite(background, image).convert("RGB")
    return psd.composite(force=True, color=1.0, alpha=1.0).convert("RGB")
