from copy import deepcopy
import numpy as np

ROLES = ("base_regions", "shadow_regions", "highlight_regions", "unknown_regions", "line_related_regions", "rim_light_regions", "ambient_light_regions")


def initial_plan(records, width, height):
    parts = []
    for geometry in (r for r in records if r["parent_id"] is None):
        children = [r for r in records if r["parent_id"] == geometry["id"]]
        base = max(children, key=lambda r: r["pixel_area"])
        x0, y0, x1, y1 = geometry["bbox"]
        border = x0 == 0 and y0 == 0 and x1 == width and y1 == height
        part = {"semantic_id": f"background_{geometry['id']}" if border else f"other_{geometry['id']}", "kind": "background" if border else "character", "geometry_regions": [geometry["id"]], **{role: [] for role in ROLES}, "notes": "Heuristic draft; Astra must review the contact sheets for semantics."}
        for region in children:
            difference = region["mean_lab"][0] - base["mean_lab"][0]
            role = "shadow_regions" if difference < -6 else "highlight_regions" if difference > 6 else "base_regions"
            part[role].append(region["id"])
        parts.append(part)
    return {"schema_version": 1, "classification_source": "heuristic", "parts": parts}


def validate_plan(plan, records):
    if plan.get("schema_version", 1) != 1 or not isinstance(plan.get("parts"), list) or not plan["parts"]:
        raise ValueError("Plan requires schema_version 1 and a nonempty parts array")
    geometries = {r["id"] for r in records if r["parent_id"] is None}
    children = {r["id"]: r["parent_id"] for r in records if r["parent_id"]}
    seen_g, seen_c, names = set(), set(), set()
    for part in plan["parts"]:
        name = part.get("semantic_id")
        if not isinstance(name, str) or not name.strip() or name in names:
            raise ValueError("semantic_id must be a unique nonempty string")
        names.add(name)
        gs = part.get("geometry_regions", [])
        if not gs or len(set(gs)) != len(gs) or not set(gs) <= geometries or seen_g & set(gs):
            raise ValueError(f"Invalid or duplicated geometry assignment: {name}")
        seen_g.update(gs)
        if part.get("shadow_blend_mode", "multiply") not in ("multiply", "normal"):
            raise ValueError("shadow_blend_mode supports multiply or normal")
        if part.get("highlight_blend_mode", "auto") not in ("auto", "normal", "screen"):
            raise ValueError("highlight_blend_mode supports auto, normal or screen")
        for role in ROLES:
            ids = part.get(role, [])
            if not isinstance(ids, list):
                raise ValueError(f"{role} must be an array")
            for rid in ids:
                if rid not in children or children[rid] not in gs or rid in seen_c:
                    raise ValueError(f"Unknown, duplicate or wrong-parent color region: {rid}")
                seen_c.add(rid)
    if seen_g != geometries or seen_c != set(children):
        raise ValueError("Every geometry and color region must be assigned exactly once; use unknown_regions when unsure")
    return plan


def apply_patch_plan(plan, patch, allowed_geometry, records):
    result = deepcopy(plan)
    updates = patch.get("assignments", [])
    if not updates:
        raise ValueError("Repair patch requires assignments")
    color_parent = {r["id"]: r["parent_id"] for r in records if r["parent_id"]}
    seen = set()
    for update in updates:
        rid, role = update.get("region_id"), update.get("role")
        if rid in seen or color_parent.get(rid) not in allowed_geometry or role not in ROLES:
            raise ValueError(f"Repair must reference a requested color region and valid role: {rid}")
        seen.add(rid)
        part = next(p for p in result["parts"] if color_parent[rid] in p["geometry_regions"])
        for old_role in ROLES:
            if rid in part.get(old_role, []):
                part[old_role].remove(rid)
        part.setdefault(role, []).append(rid)
        if "semantic_id" in update:
            part["semantic_id"] = update["semantic_id"]
    result["classification_source"] = "astra_reviewed" if patch.get("reviewed_by") == "astra" else plan.get("classification_source", "heuristic")
    return validate_plan(result, records)
