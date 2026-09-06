import hashlib
import json
import os
from pathlib import Path
import time
import uuid
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"


def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def replace_file(temp, path):
    for attempt in range(20):
        try:
            os.replace(temp, path)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.1)


def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temp.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
        replace_file(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def write_json(path, value):
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def save_image(image, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.stem}.{uuid.uuid4().hex}{path.suffix}")
    try:
        image.save(temp)
        replace_file(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def monitor(psd, job=None):
    value = {"psd": str(Path(psd).resolve()), "job": str(Path(job).resolve()) if job else None}
    write_json(DOCS / "active_job.json", value)
    atomic_write(DOCS / "PREVIEW.md", f"# 現在のプレビュー監視先\n\n- PSD: `{value['psd']}`\n- job: `{value['job']}`\n- 起動: リポジトリ直下の `start_preview.bat`\n- 未作成のPSDは初回保存まで待機。上書き後に自動更新。\n\n機械用の監視設定は `docs/active_job.json`。ジョブ進捗は `docs/jobs/` のmdを参照。\n")
    return value


def progress(job, phase, message, **extra):
    job = Path(job).resolve()
    path = job / "status.json"
    state = read_json(path) if path.exists() else {"job": str(job), "repair_count": 0}
    state.update(extra, phase=phase, message=message, updated_at=datetime.now(timezone.utc).isoformat())
    write_json(path, state)
    key = f"{job.name}-{hashlib.sha256(str(job).encode()).hexdigest()[:8]}"
    lines = [f"# Job: {job.name}", "", f"更新(UTC): {state['updated_at']}", "", f"- job: `{job}`", f"- phase: `{phase}`", f"- 状態: {message}", f"- PSD: `{state.get('psd', job / 'output.psd')}`", f"- 修正回数: {state.get('repair_count', 0)} / 3", f"- 計画: `{job / 'layer_plan.json'}`", f"- 次の操作: {state.get('next_action', 'docs/AGENT_WORKFLOW.mdを参照')}", "", "詳細状態:", "```json", json.dumps(state, ensure_ascii=False, indent=2), "```", ""]
    atomic_write(DOCS / "jobs" / f"{key}.md", "\n".join(lines))
    return state
