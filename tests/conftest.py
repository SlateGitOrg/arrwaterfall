from __future__ import annotations

import json
from pathlib import Path

import pytest

from arrwaterfall.pipeline import run_pipeline
from arrwaterfall.synth import generate

FIXTURES = Path(__file__).parent / "fixtures"
PROJECT = Path(__file__).parents[1] / "powerbi"


@pytest.fixture(scope="session")
def data_dir(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("book")
    generate(path, seed=11)
    return path


@pytest.fixture(scope="session")
def truth(data_dir) -> dict:
    return json.loads((data_dir / "truth.json").read_text())


@pytest.fixture(scope="session")
def pipe_factory(data_dir):
    cache: dict[int, object] = {}

    def get(grace_days: int = 60):
        if grace_days not in cache:
            cache[grace_days] = run_pipeline(data_dir, grace_days=grace_days)
        return cache[grace_days]

    yield get
    for p in cache.values():
        p.close()


@pytest.fixture(scope="session")
def pipe(pipe_factory):
    return pipe_factory(60)


@pytest.fixture(scope="session")
def fixture_pipe_factory():
    cache: dict[int, object] = {}

    def get(grace_days: int = 60):
        if grace_days not in cache:
            cache[grace_days] = run_pipeline(FIXTURES, grace_days=grace_days)
        return cache[grace_days]

    yield get
    for p in cache.values():
        p.close()
