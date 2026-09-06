"""All pixel operations stay here, not in the language model."""
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage as ndi
from skimage.color import rgb2lab

from .storage import save_image, write_json


def load_inputs(reference, lineart, max_shift=8):
    with Image.open(reference) as source:
        ref_meta = {"width": source.width, "height": source.height, "mode": source.mode, "alpha": "A" in source.getbands() or "transparency" in source.info}
        ref = np.asarray(source.convert("RGBA"))
    if min(ref.shape[:2]) < 8:
        raise ValueError("Images must be at least 8 x 8 pixels")
    if np.any(ref[..., 3] != 255):
        raise ValueError("v1 requires an opaque reference image; transparent reference is a v2 feature")
    with Image.open(lineart) as source:
        line_meta = {"width": source.width, "height": source.height, "mode": source.mode, "alpha": "A" in source.getbands() or "transparency" in source.info}
        line = np.asarray(source.convert("RGBA").resize((ref.shape[1], ref.shape[0]), Image.Resampling.LANCZOS)).astype(np.float32) / 255
    if np.any(line[..., 3] < 1):
        alpha, ink = line[..., 3], line[..., :3]
    else:
        # Recover colored ink over white while retaining antialiasing.
        alpha = 1 - np.min(line[..., :3], axis=2)
        ink = np.clip((line[..., :3] - (1 - alpha[..., None])) / np.maximum(alpha[..., None], 1e-6), 0, 1)
    rgb = ref[..., :3].astype(np.float32) / 255
    # Translation is accepted only when reference dark-pixel support improves.
    darkness = 1 - np.mean(rgb, axis=2)
    scale = min(1.0, 512 / max(alpha.shape))
    small = cv2.resize(alpha, None, fx=scale, fy=scale)
    dark = cv2.resize(darkness, (small.shape[1], small.shape[0]))
    def score(dx, dy):
        shifted = cv2.warpAffine(small, np.float32([[1, 0, dx * scale], [0, 1, dy * scale]]), (small.shape[1], small.shape[0]))
        return float(np.sum(shifted * dark) / max(np.sum(small), 1e-6))
    initial = score(0, 0)
    best = (initial, 0, 0)
    for dy in range(-max_shift, max_shift + 1):
        for dx in range(-max_shift, max_shift + 1):
            candidate = score(dx, dy)
            if candidate > best[0] + 1e-6:
                best = (candidate, dx, dy)
    dx, dy = (best[1], best[2]) if best[0] > initial + 0.015 else (0, 0)
    if dx or dy:
        matrix = np.float32([[1, 0, dx], [0, 1, dy]])
        alpha = cv2.warpAffine(alpha, matrix, (ref.shape[1], ref.shape[0]))
        ink = cv2.warpAffine(ink, matrix, (ref.shape[1], ref.shape[0]))
    usable = alpha < 0.85
    if not usable.any():
        raise ValueError("Lineart covers the entire canvas; provide a white or transparent background line drawing")
    underlying = np.clip((rgb - ink * alpha[..., None]) / np.maximum(1 - alpha[..., None], 0.01), 0, 1)
    nearest = ndi.distance_transform_edt(~usable, return_distances=False, return_indices=True)
    underlying[~usable] = underlying[tuple(nearest[:, ~usable])]
    return rgb, ink, alpha, underlying, {"reference": ref_meta, "lineart": line_meta, "alignment": {"dx": dx, "dy": dy, "score_before": initial, "score_after": score(dx, dy), "resized": (line_meta['width'], line_meta['height']) != (ref_meta['width'], ref_meta['height'])}}


def geometry_labels(alpha, min_area=12):
    barrier = ndi.binary_closing(alpha > 0.35, structure=np.ones((3, 3)), border_value=0)
    barrier |= alpha > 0.35
    labels, count = ndi.label(~barrier)
    sizes = np.bincount(labels.ravel())
    keep = np.flatnonzero(sizes >= min_area)
    keep = keep[keep != 0]
    if not len(keep):
        return np.ones(alpha.shape, dtype=np.int32)
    lut = np.zeros(count + 1, dtype=np.int32)
    lut[keep] = np.arange(1, len(keep) + 1)
    labels = lut[labels]
    nearest = ndi.distance_transform_edt(labels == 0, return_distances=False, return_indices=True)
    return labels[tuple(nearest)]


def cluster_region(rgb, mask, max_colors=6):
    pixels = rgb[mask]
    lab = rgb2lab(pixels.reshape(-1, 1, 3)).reshape(-1, 3).astype(np.float32)
    sample = lab[::max(1, len(lab) // 12000)][:12000]
    unique = np.unique(np.round(sample / 4).astype(np.int32), axis=0)
    k = min(max_colors, len(unique), len(sample))
    if k <= 1:
        return np.zeros(len(pixels), dtype=np.int32)
    cv2.setRNGSeed(42)
    _, _, centers = cv2.kmeans(sample, k, None, (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 40, 0.1), 3, cv2.KMEANS_PP_CENTERS)
    centers = centers[np.argsort(centers[:, 0], kind="stable")]
    merged = []
    for center in centers:
        if not any(np.linalg.norm(center - existing) < 6 for existing in merged):
            merged.append(center)
    centers = np.array(merged)
    # Chunk distances so large canvases do not allocate N x K x 3 at once.
    result = np.empty(len(lab), dtype=np.int32)
    for start in range(0, len(lab), 100000):
        distance = np.sum((lab[start:start + 100000, None] - centers[None]) ** 2, axis=2)
        result[start:start + 100000] = np.argmin(distance, axis=1)
    return result


def bbox_of(mask):
    ys, xs = np.nonzero(mask)
    return [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]


def metadata(rid, parent, mask, rgb, job):
    ys, xs = np.nonzero(mask)
    pixels = rgb[mask]
    quant = np.round(pixels * 255 / 8).astype(np.int32) * 8
    values, counts = np.unique(quant, axis=0, return_counts=True)
    dominant = values[np.argsort(-counts, kind="stable")[:3]].clip(0, 255)
    path = f"masks/{rid}.png"
    save_image(Image.fromarray(mask.astype(np.uint8) * 255), job / path)
    return {"id": rid, "parent_id": parent, "bbox": bbox_of(mask), "centroid": [float(xs.mean()), float(ys.mean())], "pixel_area": len(xs), "mean_rgb": (pixels.mean(0) * 255).tolist(), "median_rgb": (np.median(pixels, axis=0) * 255).tolist(), "mean_lab": rgb2lab(pixels.reshape(-1, 1, 3)).mean(axis=(0, 1)).tolist(), "dominant_colors": dominant.tolist(), "neighbor_ids": [], "mask_path": path}


def adjacency(labels, names):
    edges = set()
    for a, b in [(labels[:, :-1], labels[:, 1:]), (labels[:-1, :], labels[1:, :])]:
        different = a != b
        for x, y in np.unique(np.stack([a[different], b[different]], axis=1), axis=0):
            if x and y:
                edges.add(tuple(sorted((int(x), int(y)))))
    neighbors = {name: set() for name in names.values()}
    for a, b in edges:
        neighbors[names[a]].add(names[b])
        neighbors[names[b]].add(names[a])
    return neighbors


def extract(rgb, alpha, job, max_colors=6, min_area=12):
    geometry = geometry_labels(alpha, min_area)
    if geometry.max() > 1500:
        raise ValueError("More than 1500 geometry regions; simplify lineart or raise --min-area")
    colors = np.zeros(geometry.shape, np.int32)
    records, geometry_names, color_names = [], {}, {}
    color_index = 0
    for n in range(1, int(geometry.max()) + 1):
        gid = f"G{n:04d}"
        geometry_names[n] = gid
        mask = geometry == n
        records.append(metadata(gid, None, mask, rgb, job))
        assigned = cluster_region(rgb, mask, max_colors)
        local = np.full(mask.shape, -1, dtype=np.int32)
        local[mask] = assigned
        cn = 0
        # Keep large disconnected components separate; absorb tiny components.
        candidates = []
        for c in np.unique(assigned):
            cc, count = ndi.label(local == c)
            sizes = np.bincount(cc.ravel())
            for component in range(1, count + 1):
                if sizes[component] >= min_area:
                    candidates.append(cc == component)
        if not candidates:
            candidates = [mask]
        owned = np.zeros(mask.shape, np.int32)
        for i, part_mask in enumerate(candidates, 1):
            owned[part_mask] = i
        nearest = ndi.distance_transform_edt(owned == 0, return_distances=False, return_indices=True)
        owned[mask] = owned[tuple(nearest[:, mask])]
        for i in range(1, len(candidates) + 1):
            cn += 1
            color_index += 1
            if color_index > 6000:
                raise ValueError("More than 6000 color regions; raise --min-area or lower --max-colors")
            cid = f"{gid}-C{cn:02d}"
            color_names[color_index] = cid
            cmask = (owned == i) & mask
            colors[cmask] = color_index
            records.append(metadata(cid, gid, cmask, rgb, job))
    neighbors = adjacency(geometry, geometry_names) | adjacency(colors, color_names)
    for record in records:
        record["neighbor_ids"] = sorted(neighbors[record["id"]])
    np.savez_compressed(job / "labels.npz", geometry=geometry, color=colors)
    write_json(job / "regions.json", {"regions": records, "geometry_names": geometry_names, "color_names": color_names})
    return records, geometry, colors


def contact_sheets(rgb, records, geometry, colors, job):
    original = Image.fromarray(np.round(rgb * 255).astype(np.uint8))
    overview = original.copy()
    overview.thumbnail((1200, 1200))
    save_image(overview, job / "overview.webp")
    font = ImageFont.load_default(size=14)
    for filename, labels, subset in [("geometry_regions.webp", geometry, [r for r in records if r["parent_id"] is None]), ("color_regions.webp", colors, [r for r in records if r["parent_id"]])]:
        rng = np.random.default_rng(42)
        palette = rng.integers(30, 240, (int(labels.max()) + 1, 3), dtype=np.uint8)
        overlay = Image.blend(original, Image.fromarray(palette[labels]), 0.38)
        overlay.thumbnail((1400, 1400))
        scale = overlay.width / original.width
        draw = ImageDraw.Draw(overlay)
        for region in subset:
            x, y = region["centroid"]
            draw.text((x * scale, y * scale), region["id"], font=font, fill="white", stroke_width=2, stroke_fill="black", anchor="mm")
        save_image(overlay, job / filename)
    # Paginate individual labeled crops so tiny regions can still be reviewed.
    pages = []
    for page_start in range(0, len(records), 48):
        page_records = records[page_start:page_start + 48]
        sheet = Image.new("RGB", (960, ((len(page_records) + 5) // 6) * 142), "#202630")
        draw = ImageDraw.Draw(sheet)
        for i, r in enumerate(page_records):
            crop = original.crop(r["bbox"])
            with Image.open(job / r["mask_path"]) as mask_image:
                cut_mask = mask_image.crop(r["bbox"])
            backdrop = Image.new("RGB", crop.size, "#535b69")
            backdrop.paste(crop, mask=cut_mask)
            backdrop.thumbnail((150, 112))
            x, y = (i % 6) * 160, (i // 6) * 142
            sheet.paste(backdrop, (x + (160 - backdrop.width) // 2, y))
            draw.text((x + 3, y + 116), r["id"], fill="white", font=font)
        name = "contact_sheet.webp" if page_start == 0 else f"contact_sheet_{page_start // 48 + 1:03d}.webp"
        save_image(sheet, job / name)
        pages.append(name)
    return pages
