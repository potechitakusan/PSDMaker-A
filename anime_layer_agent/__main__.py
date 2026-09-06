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
    return main


def main():
    args = vars(parser().parse_args())
    command = args.pop("command")
    try:
        if command in ("prepare-compact", "build-compact", "review-materials"):
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
