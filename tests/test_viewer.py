from pathlib import Path
import time
import pytest
from anime_layer_agent.pipeline import build
from anime_layer_agent.storage import atomic_write, digest
from anime_layer_agent.viewer import StablePSDReader


def test_wait_first_save_overwrite_partial_and_recovery(job):
    path = job / "output.psd"
    reader = StablePSDReader(settle_seconds=0)
    with pytest.raises(FileNotFoundError):
        reader.read(path)
    build(job)
    assert reader.read(path) is None
    first = reader.read(path)
    assert first["size"] == (256, 256)
    assert reader.read(path) is None
    original = path.read_bytes()
    atomic_write(path, b"8BPS incomplete")
    assert reader.read(path) is None
    with pytest.raises(Exception):
        reader.read(path)
    assert reader.loaded == first["stamp"]
    atomic_write(path, original)
    assert reader.read(path) is None
    refreshed = reader.read(path)
    assert refreshed["stamp"] != first["stamp"]
    assert len(refreshed["layers"]) >= 7


def test_manual_reload(job):
    path = Path(build(job)["psd"])
    reader = StablePSDReader(settle_seconds=0)
    reader.read(path)
    assert reader.read(path)
    assert reader.read(path) is None
    assert reader.read(path, force=True)


def test_verified_merged_preview_matches_composite(job):
    import numpy as np
    path = Path(build(job)["psd"])
    reader = StablePSDReader(settle_seconds=0)
    reader.read(path)
    regular = reader.read(path)
    cached = reader.read(path, force=True, generated_hash=digest(path))
    assert np.abs(np.asarray(regular["image"], dtype=float) - np.asarray(cached["image"], dtype=float)).max() <= 1
