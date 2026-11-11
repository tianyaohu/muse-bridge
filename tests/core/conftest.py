"""Fixtures for the core auth and isolation tests."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from muse_bridge.app import create_app
from muse_bridge.config import Settings


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(data_dir=tmp_path / "data", legacy_single_token=False, retention_days=90)


@pytest.fixture
def bridge(settings):
    return create_app(settings)


@pytest.fixture
def client(bridge):
    with TestClient(bridge) as http:
        yield http
