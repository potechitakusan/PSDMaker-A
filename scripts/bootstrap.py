"""Install missing dependencies into the project environment only."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    if sys.version_info < (3, 12):
        raise RuntimeError("Python 3.12 or newer is required")
    temp = ROOT / ".tmp"
    temp.mkdir(exist_ok=True)
    env = dict(os.environ, TEMP=str(temp), TMP=str(temp), PIP_DISABLE_PIP_VERSION_CHECK="1")
    python = ROOT / ".venv" / "Scripts" / "python.exe"
    if not python.exists():
        subprocess.run([sys.executable, "-m", "venv", str(ROOT / ".venv")], env=env, check=True)
    if subprocess.run([str(python), "-m", "pip", "--version"], capture_output=True).returncode:
        subprocess.run([str(python), "-m", "ensurepip", "--upgrade"], env=env, check=True)
    requirements = ROOT / "requirements.txt"
    digest = hashlib.sha256(requirements.read_bytes()).hexdigest()
    marker = ROOT / ".venv" / "requirements.sha256"
    check = subprocess.run([str(python), "-c", "import numpy,PIL,scipy,skimage,cv2,psd_tools,pytest,tkinter"], capture_output=True)
    if check.returncode or not marker.exists() or marker.read_text() != digest:
        subprocess.run([str(python), "-m", "pip", "install", "--no-cache-dir", "-r", str(requirements)], env=env, check=True)
        marker.write_text(digest)
    print("Ready: .venv/Scripts/python.exe")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Setup failed: {exc}", file=sys.stderr)
        sys.exit(1)
