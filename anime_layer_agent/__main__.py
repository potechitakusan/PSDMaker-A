import argparse
import json
from pathlib import Path
import sys

from . import pipeline
from .storage import monitor, progress


def parser():
    main = argparse.ArgumentParser(description="Local PSD decomposition orchestrated by Codex Astra")
    commands = main.add_subparsers(dest="command", required=True)
    analyze = commands.add_parser("analyze")
    analyze.add_argument("--reference", required=True)
    analyze.add_argument("--lineart", required=True)
    analyze.add_argument("--output")
    analyze.add_argument("--max-colors", type=int, default=6)
    analyze.add_argument("--min-area", type=int, default=12)
    analyze.add_argument("--max-shift", type=int, default=8)
    build = commands.add_parser("build")
    build.add_argument("--job", required=True)
    build.add_argument("--output")
    evaluate = commands.add_parser("evaluate")
    evaluate.add_argument("--job", required=True)
    run = commands.add_parser("run")
    run.add_argument("--reference", required=True)
    run.add_argument("--lineart", required=True)
    run.add_argument("--output", required=True)
    run.add_argument("--plan", dest="plan_path")
    watch = commands.add_parser("monitor")
    watch.add_argument("--psd", required=True)
    watch.add_argument("--job")
    summary = commands.add_parser("summary")
    summary.add_argument("--job", required=True)
    summary.add_argument("--offset", type=int, default=0)
    summary.add_argument("--limit", type=int, default=20)
    repair = commands.add_parser("repair")
    repair.add_argument("--job", required=True)
    repair.add_argument("--limit", type=int, default=5)
    apply = commands.add_parser("apply-repair")
    apply.add_argument("--job", required=True)
    apply.add_argument("--patch", dest="patch_path", required=True)
    demo = commands.add_parser("demo")
    demo.add_argument("--output", default="work/demo_input")
    compact = commands.add_parser("prepare-compact")
    compact.add_argument("--reference", required=True)
    compact.add_argument("--lineart", required=True)
    compact.add_argument("--job", required=True)
    compact_build = commands.add_parser("build-compact")
    compact_build.add_argument("--job", required=True)
    compact_build.add_argument("--output")
    material = commands.add_parser("review-materials")
    material.add_argument("--job", required=True)
    material.add_argument("--geometry", type=int, required=True)
    material.add_argument("--clusters", type=int, default=6)
    editing = commands.add_parser('configure-editing', help='Set artist preferences; auto or explicit values')
    editing.add_argument('--job', required=True)
    editing.add_argument('--base-mode', choices=['review_candidates','dominant'],default='review_candidates')
    editing.add_argument('--layer-range', default='auto', help='Drawing layers excluding folders: auto or 100-200')
    editing.add_argument('--color-tolerance', default='auto', help='auto or 0..100; paint Delta E76 cap = value / 5')
    editing.add_argument('--lighting', choices=['neutral', 'cool', 'warm'], default='neutral')
    editing.add_argument('--lineart', choices=['monochrome', 'source'], default='monochrome')
    editing.add_argument('--detail-mode', choices=['relative','pigment'], default='relative')
    review = commands.add_parser('review-editing', help='Inspect masks, background and preferences before PSD build')
    review.add_argument('--job', required=True)
    selection = commands.add_parser('select-color', help='Preview a seed color selection without modifying the plan')
    selection.add_argument('--job', required=True)
    selection.add_argument('--x', type=int, required=True)
    selection.add_argument('--y', type=int, required=True)
    selection.add_argument('--name', default='selection')
    selection.add_argument('--tolerance', type=float, default=15)
    selection.add_argument('--metric', choices=['delta-e-2000', 'delta-e-76', 'rgb'], default='delta-e-2000')
    selection.add_argument('--global-match', action='store_true')
    selection.add_argument('--connectivity', type=int, choices=[4, 8], default=4)
    selection.add_argument('--families', nargs='+')
    selection.add_argument('--hue-range', nargs=2, type=float)
    selection.add_argument('--source-part')
    assign = commands.add_parser('assign-selection', help='Apply a reviewed selection proposal to semantic parts')
    assign.add_argument('--job', required=True)
    assign.add_argument('--selection', required=True)
    assign.add_argument('--to', required=True)
    assign.add_argument('--source-part', required=True)
    components = commands.add_parser('review-components',help='Inspect disconnected components by ID for local reassignment')
    components.add_argument('--job',required=True)
    component_kind=components.add_mutually_exclusive_group(required=True)
    component_kind.add_argument('--geometry',type=int)
    component_kind.add_argument('--material',type=int)
    post = commands.add_parser('post-review',help='Render actual PSD recolors and create the Astra checklist')
    post.add_argument('--job',required=True)
    finish = commands.add_parser('finish-review',help='Validate and record completed visual self-review')
    finish.add_argument('--job',required=True)
    finish.add_argument('--assessment')
    binary = commands.add_parser('binarize-lineart', help='Save binary or transparent ink to a separate file')
    binary.add_argument('--input', required=True)
    binary.add_argument('--output', required=True)
    binary.add_argument('--threshold', type=int, default=192)
    binary.add_argument('--transparent', action='store_true')
    color = commands.add_parser('prepare-coloring', help='Opt-in coloring: explicit input lineart and source PSD job required')
    color.add_argument('--lineart', required=True)
    color_source = color.add_mutually_exclusive_group(required=True)
    color_source.add_argument('--source-job')
    color_source.add_argument('--source-reference', help='Portable coloring_reference.json beside its PSD')
    color.add_argument('--job', required=True)
    color.add_argument('--threshold', type=int, default=192)
    color.add_argument('--gap-close', type=int, default=3)
    color.add_argument('--min-area', type=int, default=12)
    fill = commands.add_parser('fill-region', help='Color the enclosed region at an explicit seed')
    fill.add_argument('--job', required=True)
    fill.add_argument('--x', type=int, required=True)
    fill.add_argument('--y', type=int, required=True)
    fill.add_argument('--to', required=True)
    fill.add_argument('--palette-id')
    fill.add_argument('--display-name')
    flats = commands.add_parser('paint-flats')
    flats.add_argument('--job', required=True)
    split = commands.add_parser('split-color-region', help='Close larger gaps inside one region, retaining other IDs')
    split.add_argument('--job', required=True)
    split.add_argument('--region', type=int, required=True)
    split.add_argument('--gap-close', type=int, default=16)
    lighting = commands.add_parser('prepare-lighting', help='Align a whole-image AI lighting guide to the fixed input lineart')
    lighting.add_argument('--job', required=True)
    lighting.add_argument('--image', required=True)
    lighting.add_argument('--max-shift', type=int, default=12)
    normal = commands.add_parser('prepare-normal-lighting', help='Optional signed NPY normals on the fixed lineart grid')
    normal.add_argument('--job', required=True)
    normal.add_argument('--normals', required=True)
    direction=normal.add_mutually_exclusive_group()
    direction.add_argument('--light', type=float, nargs=3)
    direction.add_argument('--preset',type=int,choices=range(1,51))
    commands.add_parser('list-normal-lights',help='List all 50 stable light choices')
    select_light=commands.add_parser('select-normal-light',help='Choose a light for an existing normal job')
    select_light.add_argument('--job',required=True)
    select_light.add_argument('--preset',type=int,choices=range(1,51),default=1)
    base_review=commands.add_parser('review-base-colors',help='Render dominant/midtone/light Base candidates')
    base_review.add_argument('--job',required=True)
    base_select=commands.add_parser('select-base-color',help='Record visually chosen material Base')
    base_select.add_argument('--job',required=True)
    base_select.add_argument('--palette-id',required=True)
    base_select.add_argument('--candidate',choices=['dominant','midtone','light'],required=True)
    base_select.add_argument('--notes',required=True)
    colored = commands.add_parser('build-colored', help='Build the opt-in coloring PSD; preserve source PSD and input ink')
    colored.add_argument('--job', required=True)
    colored.add_argument('--output')
    reference = commands.add_parser('export-coloring-reference', help='Export portable palette and part hints from a verified source PSD')
    reference.add_argument('--job', required=True)
    tones = commands.add_parser('review-coloring-tones', help='Compare source and colored PSD material lightness/chroma')
    tones.add_argument('--job', required=True)
    manga = commands.add_parser('prepare-manga', help='Opt-in manga toning: record sizes and the downscaled generation input')
    manga.add_argument('--reference', required=True)
    manga.add_argument('--job', required=True)
    manga.add_argument('--redraw-engine', choices=['imagegen', 'qwen', 'none'], default='imagegen')
    manga.add_argument('--line-engine', choices=['imagegen', 'qwen', 'provided', 'redraw'])
    manga.add_argument('--max-side', type=int, default=1536)
    manga.add_argument('--source-job', help='Reuse reviewed parts of a built first-feature job of the same image')
    manga.add_argument('--output')
    comfy = commands.add_parser('run-comfy-manga', help='Run Qwen-Image 2.1 redraw or Manga Lineart on a running ComfyUI')
    comfy.add_argument('--job', required=True)
    comfy.add_argument('--task', choices=['redraw', 'lineart', 'qwen_lineart'], default='redraw')
    comfy.add_argument('--url', default='http://127.0.0.1:8188')
    comfy.add_argument('--unet')
    comfy.add_argument('--clip')
    comfy.add_argument('--vae')
    comfy.add_argument('--seed', type=int, default=1)
    comfy.add_argument('--megapixels', type=float, default=1.0, help='Qwen image-edit size in megapixels (default: 1.0)')
    comfy.add_argument('--cache-device', default='cpu')
    comfy.add_argument('--wait-seconds', type=int, default=900)
    comfy.add_argument('--prompt')
    register = commands.add_parser('register-manga', help='Map generated images to the original grid and list part regions')
    register.add_argument('--job', required=True)
    register.add_argument('--redraw')
    register.add_argument('--lineart')
    register.add_argument('--fit', choices=['auto', 'stretch', 'contain', 'cover'], default='auto')
    register.add_argument('--max-shift', type=int, default=12)
    register.add_argument('--min-area', type=int)
    register.add_argument('--region-source', choices=['auto', 'lineart', 'redraw', 'source_job'], default='auto')
    manga_build = commands.add_parser('build-manga', help='Build the manga tone PSD from a reviewed part plan')
    manga_build.add_argument('--job', required=True)
    manga_build.add_argument('--output')
    manga_export = commands.add_parser('export-manga', help='Export with saved adjustment settings, without opening the GUI')
    manga_export.add_argument('--job', required=True)
    manga_export.add_argument('--output')
    manga_export.add_argument('--reuse-last', action='store_true')
    manga_export.add_argument('--settings', help='Adjustment JSON file')
    png_export = commands.add_parser('export-manga-png', help='Export the applied tone preview as a full-size PNG')
    png_export.add_argument('--job', required=True)
    png_export.add_argument('--output', required=True)
    png_export.add_argument('--reuse-last', action='store_true')
    png_export.add_argument('--settings')
    tone_ref = commands.add_parser('export-tone-reference', help='Export material tone information for feature 2 (tone)')
    tone_ref.add_argument('--job', required=True)
    transfer = commands.add_parser('prepare-tone-transfer', help='Feature 2 (tone): apply source tones to a separate lineart')
    transfer.add_argument('--lineart', required=True)
    transfer.add_argument('--job', required=True)
    transfer_source = transfer.add_mutually_exclusive_group(required=True)
    transfer_source.add_argument('--source-job')
    transfer_source.add_argument('--source-reference', help='tone_reference.json beside its PSD')
    transfer.add_argument('--output')
    transfer.add_argument('--threshold', type=int, default=192)
    transfer.add_argument('--gap-close', type=int, default=3)
    transfer.add_argument('--min-area', type=int, default=12)
    assign_tone = commands.add_parser('assign-tone-region', help='Assign/move one new-lineart region to a material and tone role')
    assign_tone.add_argument('--job', required=True)
    assign_tone.add_argument('--region', type=int, required=True)
    assign_tone.add_argument('--material', required=True)
    assign_tone.add_argument('--role', choices=['base', 'shadow', 'highlight', 'beta'], default='base')
    return main


def main():
    args = vars(parser().parse_args())
    command = args.pop("command")
    try:
        if command in ('list-normal-lights','select-normal-light'):
            from . import light_presets
            function=getattr(light_presets,command.replace('-','_'))
        elif command in ('review-base-colors','select-base-color'):
            from . import base_colors
            function=getattr(base_colors,command.replace('-','_'))
        elif command == 'prepare-normal-lighting':
            from .normal_lighting import prepare_normal_lighting
            function = prepare_normal_lighting
        elif command == 'review-coloring-tones':
            from .tone_review import review_coloring_tones
            function = review_coloring_tones
        elif command == 'export-coloring-reference':
            from .coloring_reference import export_coloring_reference
            function = export_coloring_reference
        elif command in ('binarize-lineart','prepare-coloring','fill-region','paint-flats','prepare-lighting','build-colored','split-color-region'):
            from . import coloring
            function = getattr(coloring,command.replace('-','_'))
        elif command in ("prepare-compact", "build-compact", "review-materials"):
            from . import compact
            function = getattr(compact,command.replace('-','_'))
        elif command in ('configure-editing', 'review-editing'):
            from . import artist
            function = getattr(artist, command.replace('-', '_'))
        elif command in ('select-color', 'assign-selection', 'review-components'):
            from . import selection
            function = getattr(selection, command.replace('-', '_'))
        elif command in ('export-manga', 'export-manga-png'):
            from . import manga_adjust
            function = getattr(manga_adjust, command.replace('-', '_'))
        elif command in ('prepare-tone-transfer', 'export-tone-reference', 'assign-tone-region'):
            from . import tone_transfer
            function = getattr(tone_transfer, command.replace('-', '_'))
        elif command in ('prepare-manga', 'run-comfy-manga', 'register-manga', 'build-manga'):
            from . import manga
            function = getattr(manga, command.replace('-', '_'))
        elif command in ('post-review','finish-review') and (Path(args['job']) / 'manga_job.json').exists():
            from . import manga
            function = getattr(manga, command.replace('-', '_'))
        elif command in ('post-review','finish-review'):
            from . import post_review
            function = getattr(post_review,command.replace('-','_'))
        else:
            function = monitor if command == "monitor" else getattr(pipeline, command.replace("-", "_"))
        result = function(**args)
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 2 if result.get("passed") is False else 0
    except Exception as exc:
        job = args.get("job")
        if job and Path(job).is_dir():
            progress(job, "error", str(exc), next_action=f"原因を解消して {command} を再実行")
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}, ensure_ascii=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
