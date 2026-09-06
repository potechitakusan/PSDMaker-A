from copy import deepcopy
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw
import pytest
from psd_tools import PSDImage
from psd_tools.constants import BlendMode
from skimage.color import rgb2lab, lab2rgb

from anime_layer_agent.composition import composite_rgb, solve_color, render_psd
from anime_layer_agent.pipeline import analyze, build, evaluate, repair, apply_repair, demo
from anime_layer_agent.plan import validate_plan
from anime_layer_agent.regions import geometry_labels, load_inputs
from anime_layer_agent.storage import read_json, write_json, digest


def test_rgb_lab_roundtrip():
    rgb = np.array([[[0, 0, 0], [1, 1, 1], [1, 0, 0], [0.2, 0.7, 0.4]]], dtype=float)
    lab = rgb2lab(rgb)
    assert lab[0, 0, 0] == pytest.approx(0, abs=1e-5)
    assert lab[0, 1, 0] == pytest.approx(100, abs=1e-3)
    np.testing.assert_allclose(lab2rgb(lab), rgb, atol=1e-5)


def test_multiply_and_opacity_solver():
    base = np.array([[0.8, 0.6, 0.4], [0.7, 0.5, 0.3]])
    source = np.array([0.5, 0.4, 0.3])
    expected = base * (0.25 + 0.75 * source)
    np.testing.assert_allclose(composite_rgb(base, source, "multiply", 0.75), expected)
    result = solve_color(base, expected)
    actual = composite_rgb(base, np.array(result["color"]), result["blend_mode"], result["opacity"])
    assert result["blend_mode"] == "multiply"
    assert np.abs(actual - expected).max() < 0.004
    assert result["color"] != expected.mean(0).tolist()


def test_highlight_candidates_and_black_base():
    result = solve_color(np.array([[0.2, 0.4, 0.6]]), np.array([[0.5, 0.6, 0.8]]), ("screen", "normal"))
    assert {c["blend_mode"] for c in result["candidates"]} == {"screen", "normal"}
    assert result["mae"] < 1
    impossible = solve_color(np.zeros((2, 3)), np.full((2, 3), 0.3))
    assert impossible["mae"] > 70


def test_closed_geometry_and_stability():
    alpha = np.zeros((32, 32), dtype=float)
    alpha[8:25, 8] = alpha[8:25, 24] = 1
    alpha[8, 8:25] = alpha[24, 8:25] = 1
    labels = geometry_labels(alpha)
    assert labels.max() == 2
    assert labels.min() == 1
    assert labels[0, 0] != labels[16, 16]
    np.testing.assert_array_equal(labels, geometry_labels(alpha))


def test_masks_metadata_contacts_cache(job):
    data = read_json(job / "regions.json")
    for parent in (None, "color"):
        masks = [np.array(Image.open(job / r["mask_path"])) > 0 for r in data["regions"] if (r["parent_id"] is None) == (parent is None)]
        assert np.all(np.stack(masks).sum(0) == 1)
    for r in data["regions"]:
        assert set(("id", "parent_id", "bbox", "centroid", "pixel_area", "mean_rgb", "median_rgb", "mean_lab", "dominant_colors", "neighbor_ids", "mask_path")) <= set(r)
        mask = np.array(Image.open(job / r["mask_path"])) > 0
        assert int(mask.sum()) == r["pixel_area"]
    assert (job / "contact_sheet.webp").exists()
    before = (job / "regions.json").stat().st_mtime_ns
    info = read_json(job / "job.json")
    cached = analyze(info["reference_source"], info["lineart_source"], job, max_shift=0)
    assert cached["cached"] is True
    assert (job / "regions.json").stat().st_mtime_ns == before
    with pytest.raises(ValueError, match="different inputs"):
        analyze(info["reference_source"], info["lineart_source"], job, max_colors=3)


def test_plan_duplicate_missing_unknown_custom(job):
    plan = read_json(job / "layer_plan.json")
    records = read_json(job / "regions.json")["regions"]
    validate_plan(plan, records)
    custom = deepcopy(plan)
    custom["parts"][0]["semantic_id"] = "ribbon_custom"
    validate_plan(custom, records)
    part = custom["parts"][0]
    rid = part["base_regions"][0]
    part["unknown_regions"].append(rid)
    with pytest.raises(ValueError, match="duplicate"):
        validate_plan(custom, records)
    part["base_regions"].remove(rid)
    validate_plan(custom, records)
    part["unknown_regions"].remove(rid)
    with pytest.raises(ValueError, match="exactly once"):
        validate_plan(custom, records)


def test_psd_hierarchy_modes_reconstruction_and_overwrite(job):
    result = build(job)
    path = Path(result["psd"])
    psd = PSDImage.open(path)
    assert psd[0].name == "Character"
    assert [g.name for g in psd[0]] == ["Background", "Base", "Shadows", "Highlights", "Lineart"]
    assert any(layer.blend_mode == BlendMode.MULTIPLY for layer in psd.descendants())
    assert any(layer.blend_mode in (BlendMode.SCREEN, BlendMode.NORMAL) for layer in psd[0][3])
    evaluation = evaluate(job)
    assert evaluation["passed"]
    assert evaluation["metrics"]["mae"] < 1
    assert (job / "diff.png").exists()
    before = path.stat().st_mtime_ns
    build(job)
    assert path.stat().st_mtime_ns != before
    assert PSDImage.open(path).size == (256, 256)


def test_stale_plan_cannot_be_evaluated(job):
    build(job)
    plan = read_json(job / "layer_plan.json")
    plan["parts"][0]["semantic_id"] = "changed"
    write_json(job / "layer_plan.json", plan)
    with pytest.raises(ValueError, match="Plan changed"):
        evaluate(job)


def test_alignment_and_soft_line_alpha(tmp_path):
    size = 128
    high = Image.new("RGB", (size * 4, size * 4), "white")
    ImageDraw.Draw(high).ellipse((80, 80, 420, 420), outline="black", width=12)
    line = high.resize((size, size), Image.Resampling.LANCZOS)
    reference = line.copy()
    displaced = Image.new("RGB", line.size, "white")
    displaced.paste(line, (3, -2))
    reference.save(tmp_path / "ref.png")
    displaced.save(tmp_path / "line.png")
    _, _, alpha, _, metadata = load_inputs(tmp_path / "ref.png", tmp_path / "line.png")
    assert (metadata["alignment"]["dx"], metadata["alignment"]["dy"]) == (-3, 2)
    assert np.any((alpha > 0) & (alpha < 1))


def test_transparent_line_and_reference_rejection(tmp_path):
    reference = Image.new("RGB", (32, 32), "white")
    line = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    ImageDraw.Draw(line).line((4, 4, 25, 25), fill=(40, 20, 10, 128), width=2)
    reference.save(tmp_path / "r.png")
    line.save(tmp_path / "l.png")
    _, ink, alpha, _, _ = load_inputs(tmp_path / "r.png", tmp_path / "l.png", 0)
    drawn = np.asarray(line)[..., 3] > 0
    assert np.allclose(alpha[drawn], 128 / 255)
    assert np.allclose(ink[drawn, 0], 40 / 255)
    line.save(tmp_path / "r.png")
    with pytest.raises(ValueError, match="opaque reference"):
        load_inputs(tmp_path / "r.png", tmp_path / "l.png")


def test_repair_roundtrip_scope_and_limit(job):
    plan = read_json(job / "layer_plan.json")
    part = next(p for p in plan["parts"] if p["highlight_regions"])
    rid = part["highlight_regions"].pop()
    part["shadow_regions"].append(rid)
    write_json(job / "layer_plan.json", plan)
    build(job)
    # Force failure with explicit stricter thresholds; avoid relying on area size.
    result = evaluate(job, {"mae": 0, "ssim": 1, "mean_delta_e": 0, "edge_mismatch": 0})
    assert not result["passed"]
    before_labels = digest(job / "labels.npz")
    repair(job)
    patch = {"reviewed_by": "test", "assignments": [{"region_id": rid, "role": "highlight_regions"}]}
    write_json(job / "patch.json", patch)
    fixed = apply_repair(job, job / "patch.json")
    assert fixed["passed"]
    assert digest(job / "labels.npz") == before_labels
    assert read_json(job / "status.json")["repair_count"] == 1
    assert (job / "history/plan_before_repair_1.json").exists()
    state = read_json(job / "status.json")
    state["repair_count"] = 3
    write_json(job / "status.json", state)
    with pytest.raises(ValueError, match="Maximum repair"):
        repair(job)


def test_markdown_checkpoint_written(job, tmp_path):
    docs = list((tmp_path / "docs/jobs").glob("*.md"))
    assert len(docs) == 1
    assert "semantic_review" in docs[0].read_text(encoding="utf-8")
    build(job)
    assert "built" in docs[0].read_text(encoding="utf-8")


def test_blank_lineart_keeps_required_psd_layer(tmp_path):
    Image.new("RGB", (24, 24), (160, 120, 80)).save(tmp_path / "reference.png")
    Image.new("RGB", (24, 24), "white").save(tmp_path / "lineart.png")
    job = tmp_path / "blank_job"
    analyze(tmp_path / "reference.png", tmp_path / "lineart.png", job, max_shift=0)
    result = build(job)
    psd = PSDImage.open(result["psd"])
    assert psd[0][-1].name == "Lineart"
    assert evaluate(job)["metrics"]["mae"] == 0


def test_antialiased_cell_roundtrip(tmp_path):
    line_high = Image.new("RGB", (256, 256), "white")
    ImageDraw.Draw(line_high).ellipse((32, 32, 224, 224), outline="black", width=6)
    line = line_high.resize((64, 64), Image.Resampling.LANCZOS)
    # The reference is a known color viewed through exactly this soft line mask.
    alpha = 1 - np.asarray(line, dtype=float)[..., 0] / 255
    rgb = np.array([0.7, 0.5, 0.8])[None, None, :] * (1 - alpha[..., None])
    Image.fromarray(np.round(rgb * 255).astype(np.uint8)).save(tmp_path / "r.png")
    line.save(tmp_path / "l.png")
    job = tmp_path / "antialias_job"
    analyze(tmp_path / "r.png", tmp_path / "l.png", job, max_shift=0)
    build(job)
    result = evaluate(job)
    assert result["passed"]
    assert result["metrics"]["mae"] < 0.6
    slow = np.asarray(render_psd(job / "output.psd"), dtype=float)
    fast = np.asarray(render_psd(job / "output.psd", generated=True), dtype=float)
    assert np.abs(slow - fast).max() <= 1
