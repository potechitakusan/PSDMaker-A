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
    return main


def main():
    args = vars(parser().parse_args())
    command = args.pop("command")
    try:
        if command in ("prepare-compact", "build-compact", "review-materials"):
            from . import compact
            function = getattr(compact,command.replace('-','_'))
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
