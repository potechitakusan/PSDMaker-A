from pathlib import Path
import pytest
from anime_layer_agent import storage
from anime_layer_agent.pipeline import demo, analyze


@pytest.fixture(autouse=True)
def isolate_docs(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DOCS", tmp_path / "docs")


@pytest.fixture
def job(tmp_path):
    inputs = demo(tmp_path / "inputs")
    output = tmp_path / "job"
    analyze(**inputs, output=output, max_shift=0)
    return output
