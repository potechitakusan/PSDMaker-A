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
    colored = commands.add_parser('build-colored', help='Build the opt-in coloring PSD; preserve source PSD and input ink')
    colored.add_argument('--job', required=True)
    colored.add_argument('--output')
    reference = commands.add_parser('export-coloring-reference', help='Export portable palette and part hints from a verified source PSD')
    reference.add_argument('--job', required=True)
    return main


def main():
    args = vars(parser().parse_args())
    command = args.pop("command")
    try:
        if command == 'export-coloring-reference':
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
