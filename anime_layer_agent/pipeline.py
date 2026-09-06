import hashlib
import json
from pathlib import Path
import shutil

import cv2
import numpy as np
from PIL import Image, ImageDraw
from skimage.color import rgb2lab, deltaE_ciede2000
from skimage.metrics import structural_similarity

from .composition import make_layers, export_psd, render_psd
from .plan import initial_plan, validate_plan, apply_patch_plan, ROLES
from .regions import load_inputs, extract, contact_sheets
from .storage import ROOT, digest, write_json, read_json, save_image, progress, monitor, atomic_write

PIPELINE_VERSION = 1
DEFAULT_THRESHOLDS = {"mae": 4.0, "ssim": 0.95, "mean_delta_e": 4.0, "edge_mismatch": 0.03}


def fingerprint(reference, lineart, max_colors=6, min_area=12, max_shift=8):
    config = {"reference_hash": digest(reference), "lineart_hash": digest(lineart), "max_colors": max_colors, "min_area": min_area, "max_shift": max_shift, "pipeline_version": PIPELINE_VERSION}
    key = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()[:20]
    return key, config


def analyze(reference, lineart, output=None, max_colors=6, min_area=12, max_shift=8):
    if not 1 <= max_colors <= 16 or min_area < 1 or not 0 <= max_shift <= 32:
        raise ValueError("max-colors must be 1..16, min-area >= 1, max-shift 0..32")
    key, config = fingerprint(reference, lineart, max_colors, min_area, max_shift)
    job = Path(output).resolve() if output else ROOT / "work" / key
    job.mkdir(parents=True, exist_ok=True)
    checkpoint = job / "job.json"
    if checkpoint.exists():
        old = read_json(checkpoint)
        if old["config"] != config:
            raise ValueError("Existing job belongs to different inputs/settings. Choose a new output directory.")
        if old.get("analyzed"):
            required = old["cache_files"]
            if all((job / path).exists() for path in required):
                return {"job": str(job), "cached": True, "contact_sheets": old["contact_sheets"], "layer_plan": str(job / "layer_plan.json")}
    progress(job, "preprocessing", "入力検証と線画位置合わせ", next_action="analyzeを同じ入力で再実行すると再開できます")
    info = {"job_id": key, "config": config, "reference_source": str(Path(reference).resolve()), "lineart_source": str(Path(lineart).resolve()), "analyzed": False}
    write_json(checkpoint, info)
    rgb, ink, alpha, underlying, preprocessing = load_inputs(reference, lineart, max_shift)
    save_image(Image.fromarray(np.round(rgb * 255).astype(np.uint8)), job / "reference.png")
    save_image(Image.fromarray(np.round(alpha * 255).astype(np.uint8)), job / "line_mask.png")
    np.savez_compressed(job / "preprocessed.npz", rgb=rgb, ink=ink, alpha=alpha, underlying=underlying)
    progress(job, "region_extraction", "Geometry / Color領域とマスクを生成中")
    records, geometry, colors = extract(underlying, alpha, job, max_colors, min_area)
    pages = contact_sheets(rgb, records, geometry, colors, job)
    if not (job / "layer_plan.json").exists():
        write_json(job / "layer_plan.json", initial_plan(records, rgb.shape[1], rgb.shape[0]))
    cache_files = ["preprocessed.npz", "labels.npz", "regions.json", "reference.png", "line_mask.png", "layer_plan.json", "overview.webp", "geometry_regions.webp", "color_regions.webp", *pages, *[r["mask_path"] for r in records]]
    info.update(preprocessing=preprocessing, analyzed=True, contact_sheets=pages, cache_files=cache_files)
    write_json(checkpoint, info)
    progress(job, "semantic_review", "領域候補を生成済み。Astraの意味分類待ち", classification_source="heuristic", next_action="overview.webpとcontact_sheet.webpを開きlayer_plan.jsonを編集後、build --job を実行")
    return {"job": str(job), "cached": False, "geometry_count": int(geometry.max()), "color_count": int(colors.max()), "contact_sheets": pages, "layer_plan": str(job / "layer_plan.json")}


def build(job, output=None):
    job = Path(job).resolve()
    info = read_json(job / "job.json")
    if not info.get("analyzed"):
        raise ValueError("analyze must complete before build")
    records = read_json(job / "regions.json")["regions"]
    plan = validate_plan(read_json(job / "layer_plan.json"), records)
    status = read_json(job / "status.json")
    output = Path(output or status.get("psd", job / "output.psd")).resolve()
    if output.suffix.lower() != ".psd":
        raise ValueError("Output must have the .psd extension")
    if output in (Path(info["reference_source"]), Path(info["lineart_source"])):
        raise ValueError("Output cannot overwrite an input image")
    monitor(output, job)
    progress(job, "layer_decomposition", "Base / Shadow / Highlightを構築中", psd=str(output), next_action="中断時は build --job を再実行")
    manifest = make_layers(job, plan, records)
    manifest["plan_hash"] = digest(job / "layer_plan.json")
    write_json(job / "layers.json", manifest)
    progress(job, "psd_composition", "レイヤーPSDを保存中")
    export_psd(job, manifest, output)
    manifest["psd_hash"] = digest(output)
    manifest["psd_path"] = str(output)
    write_json(job / "layers.json", manifest)
    save_image(render_psd(output, generated=True), job / "reconstructed.png")
    write_json(job / "analysis.json", {"solvers": manifest["solvers"], "classification_source": plan.get("classification_source", "unspecified"), "evaluated": False})
    progress(job, "built", "PSD保存済み。再構成評価待ち", plan_hash=manifest["plan_hash"], psd_hash=manifest["psd_hash"], classification_source=plan.get("classification_source", "unspecified"), next_action="evaluate --job を実行")
    return {"job": str(job), "psd": str(output), "layers": len(manifest["layers"]), "classification_source": plan.get("classification_source", "unspecified")}


def evaluate(job, thresholds=None):
    job = Path(job).resolve()
    manifest = read_json(job / "layers.json")
    if manifest.get('pipeline')=='compact' and manifest.get('semantic_plan_hash')!=digest(job/'semantic_plan.json'):
        raise ValueError('Semantic plan changed since build. Run build-compact before evaluate.')
    if manifest["plan_hash"] != digest(job / "layer_plan.json"):
        raise ValueError("Plan changed since build. Run build before evaluate.")
    psd = Path(manifest["psd_path"])
    if digest(psd) != manifest["psd_hash"]:
        raise ValueError("PSD changed outside build. Rebuild before evaluating this plan.")
    progress(job, "validation", "保存済みPSDの再合成と誤差計算中", next_action="中断時は evaluate --job を再実行")
    reconstructed = render_psd(psd, generated=True)
    save_image(reconstructed, job / "reconstructed.png")
    ref = np.asarray(Image.open(job / "reference.png").convert("RGB"), dtype=np.float32) / 255
    actual = np.asarray(reconstructed, dtype=np.float32) / 255
    error = np.abs(ref - actual)
    de = deltaE_ciede2000(rgb2lab(ref), rgb2lab(actual))
    ssim, ssim_map = structural_similarity(ref, actual, channel_axis=2, data_range=1, full=True)
    ssim_map = ssim_map.mean(axis=2)
    ref_edges = cv2.Canny(np.round(ref * 255).astype(np.uint8), 70, 150) > 0
    out_edges = cv2.Canny(np.round(actual * 255).astype(np.uint8), 70, 150) > 0
    edge_diff = ref_edges != out_edges
    def metrics(mask):
        return {"mae": float(error[mask].mean() * 255), "ssim": float(ssim_map[mask].mean()), "mean_delta_e": float(de[mask].mean()), "edge_mismatch": float(edge_diff[mask].mean())}
    global_metrics = metrics(np.ones(ref.shape[:2], bool))
    global_metrics["ssim"] = float(ssim)
    regions = read_json(job / "regions.json")
    labels = np.load(job / "labels.npz")["geometry"]
    geometry_scores = [{"id": rid, **metrics(labels == int(n))} for n, rid in regions["geometry_names"].items()]
    plan = read_json(job / "layer_plan.json")
    lookup = {v: int(k) for k, v in regions["geometry_names"].items()}
    semantic_scores = [{"semantic_id": p["semantic_id"], **metrics(np.isin(labels, [lookup[g] for g in p["geometry_regions"]]))} for p in plan["parts"]]
    thresholds = thresholds or DEFAULT_THRESHOLDS
    passed = global_metrics["mae"] <= thresholds["mae"] and global_metrics["ssim"] >= thresholds["ssim"] and global_metrics["mean_delta_e"] <= thresholds["mean_delta_e"] and global_metrics["edge_mismatch"] <= thresholds["edge_mismatch"]
    result = {"evaluated": True, "passed": bool(passed), "thresholds": thresholds, "global": global_metrics, "global_ssim": float(ssim), "mean_delta_e": global_metrics["mean_delta_e"], "geometry_regions": geometry_scores, "semantic_parts": semantic_scores, "worst_regions": sorted(geometry_scores, key=lambda r: r["mean_delta_e"], reverse=True)[:8], "solvers": manifest["solvers"], "classification_source": plan.get("classification_source", "unspecified"), "plan_hash": manifest["plan_hash"], "psd_hash": manifest["psd_hash"]}
    result['layer_counts']={'pixel':len(manifest['layers']),'groups':5,'total':len(manifest['layers'])+5}
    if manifest.get('pipeline')=='compact':
        result['pipeline']='compact'
        result['line_alignment']=read_json(job/'alignment.json')
    save_image(Image.fromarray(np.round(np.clip(error * 4, 0, 1) * 255).astype(np.uint8)), job / "diff.png")
    write_json(job / "analysis.json", result)
    count = read_json(job / "status.json").get("repair_count", 0)
    phase = ("complete" if plan.get("classification_source") == "astra_reviewed" else "quality_passed") if passed else "repair_limit" if count >= 3 else "needs_repair"
    progress(job, phase, "数値品質基準を達成" if passed else "数値品質基準未達", metrics=global_metrics, passed=bool(passed), next_action="Astraで意味分類とPSDを最終確認" if passed else "repair --job で局所画像を生成" if count < 3 else "最大3回に到達。未達領域をユーザーへ報告")
    # Deliver companion artifacts alongside an explicitly requested external PSD.
    if psd.parent != job:
        for name in ("reconstructed.png", "diff.png", "analysis.json", "layer_plan.json"):
            atomic_write(psd.parent / name, (job / name).read_bytes())
    return {"job": str(job), "passed": bool(passed), "metrics": global_metrics, "classification_source": result["classification_source"], "analysis": str(job / "analysis.json")}


def repair(job, limit=5):
    job = Path(job).resolve()
    state = read_json(job / "status.json")
    if state.get("repair_count", 0) >= 3:
        raise ValueError("Maximum repair count (3) reached")
    analysis = read_json(job / "analysis.json")
    if not analysis.get("evaluated") or analysis["plan_hash"] != digest(job / "layer_plan.json"):
        raise ValueError("Run build and evaluate on the current plan first")
    if analysis["passed"]:
        return {"job": str(job), "message": "Quality thresholds already passed", "repair_required": False}
    records = read_json(job / "regions.json")["regions"]
    plan = read_json(job / "layer_plan.json")
    images = [Image.open(job / name).convert("RGB") for name in ("reference.png", "reconstructed.png", "diff.png")]
    requests = []
    for score in analysis["worst_regions"][:max(1, min(limit, 8))]:
        region = next(r for r in records if r["id"] == score["id"])
        part = next(p for p in plan["parts"] if region["id"] in p["geometry_regions"])
        x0, y0, x1, y1 = region["bbox"]
        bounds = (max(0, x0 - 8), max(0, y0 - 8), min(images[0].width, x1 + 8), min(images[0].height, y1 + 8))
        sheet = Image.new("RGB", (960, 348), "#202630")
        draw = ImageDraw.Draw(sheet)
        for i, (source, title) in enumerate(zip(images, ("Reference", "Reconstruction", "Difference x4"))):
            crop = source.crop(bounds)
            crop.thumbnail((316, 320))
            sheet.paste(crop, (i * 320, 25))
            draw.text((i * 320 + 5, 5), title, fill="white")
        path = f"repair_{region['id']}.webp"
        save_image(sheet, job / path)
        child_ids = {r["id"] for r in records if r["parent_id"] == region["id"]}
        requests.append({"geometry_id": region["id"], "crop_path": path, "semantic_id": part["semantic_id"], "assignments": {role: [rid for rid in part.get(role, []) if rid in child_ids] for role in ROLES}, "metrics": score})
    request = {"iteration": state.get("repair_count", 0) + 1, "max_repairs": 3, "plan_hash": digest(job / "layer_plan.json"), "requests": requests}
    write_json(job / "repair_request.json", request)
    progress(job, "repair_review", "局所修正のAstra判断待ち", next_action="repair_request.jsonのcropを開き修正割当JSONを作成、apply-repair --job ... --patch ...")
    return {"job": str(job), "request": str(job / "repair_request.json"), "crops": [r["crop_path"] for r in requests]}


def apply_repair(job, patch_path):
    job = Path(job).resolve()
    state = read_json(job / "status.json")
    request = read_json(job / "repair_request.json")
    if state.get("repair_count", 0) >= 3 or request["iteration"] != state.get("repair_count", 0) + 1:
        raise ValueError("Repair request expired or limit reached")
    if request["plan_hash"] != digest(job / "layer_plan.json"):
        raise ValueError("Plan changed after repair request. Rebuild/evaluate and request a fresh repair.")
    records = read_json(job / "regions.json")["regions"]
    plan = read_json(job / "layer_plan.json")
    revised = apply_patch_plan(plan, read_json(patch_path), {r["geometry_id"] for r in request["requests"]}, records)
    history = job / "history"
    history.mkdir(exist_ok=True)
    write_json(history / f"plan_before_repair_{request['iteration']}.json", plan)
    write_json(history / f"patch_{request['iteration']}.json", read_json(patch_path))
    # Count first: an interrupted repair must not allow an unbounded retry loop.
    progress(job, "repair_build", "局所割当を適用して再構成", repair_count=request["iteration"], next_action="中断時は保存計画を確認しbuild/evaluateを再実行")
    write_json(job / "layer_plan.json", revised)
    build(job)
    return evaluate(job)


def summary(job, offset=0, limit=20):
    job = Path(job).resolve()
    records = read_json(job / "regions.json")["regions"]
    return {"job": str(job), "total_regions": len(records), "offset": offset, "regions": [{k: r[k] for k in ("id", "parent_id", "pixel_area", "median_rgb", "neighbor_ids")} for r in records[max(0, offset):max(0, offset) + min(50, max(1, limit))]], "contact_sheet": str(job / "contact_sheet.webp"), "plan": str(job / "layer_plan.json")}


def run(reference, lineart, output, plan_path=None):
    key, _ = fingerprint(reference, lineart)
    job = ROOT / "work" / key
    monitor(output, job)
    analyze(reference, lineart, job)
    if plan_path and Path(plan_path).resolve() != job / "layer_plan.json":
        proposed = validate_plan(read_json(plan_path), read_json(job / "regions.json")["regions"])
        write_json(job / "layer_plan.json", proposed)
    build(job, output)
    result = evaluate(job)
    if not result["passed"] and read_json(job / "status.json").get("repair_count", 0) < 3:
        result["repair"] = repair(job)
    return result


def demo(output):
    """Synthetic fixture, never an AI-generated replacement for user artwork."""
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    reference = Image.new("RGB", (256, 256), (242, 240, 232))
    draw = ImageDraw.Draw(reference)
    draw.rectangle((45, 32, 205, 226), fill=(180, 140, 220))
    draw.rectangle((149, 32, 205, 226), fill=(108, 84, 154))
    draw.rectangle((65, 50, 79, 201), fill=(222, 204, 242))
    lineart = Image.new("RGB", reference.size, "white")
    ink = ImageDraw.Draw(lineart)
    ink.rectangle((45, 32, 205, 226), outline="black", width=3)
    draw.rectangle((45, 32, 205, 226), outline="black", width=3)
    save_image(reference, output / "reference.png")
    save_image(lineart, output / "lineart.png")
    return {"reference": str(output / "reference.png"), "lineart": str(output / "lineart.png")}
