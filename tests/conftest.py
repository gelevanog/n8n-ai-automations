"""Shared fixtures: an offline gateway (the rules model), an in-memory ops store and API, the mock systems."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from relay.api.app import create_app
from relay.config import Settings
from relay.gateway.service import Gateway
from relay.mocks.app import create_app as create_mocks
from relay.ops.db import make_sessionmaker
from relay.ops.store import OpsStore

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)  # configs/ and data/ are read relative to the repository root


@pytest.fixture
def settings() -> Settings:
    return Settings(database_url="sqlite://", mocks_database_url="sqlite://", llm_ledger=None, llm_cache=False,
                    api_token="", llm_provider="fake")


@pytest.fixture
def gateway(settings: Settings) -> Gateway:
    return Gateway(settings)


class Poster:
    """Records what the store posts to n8n (resume URLs, replays, the Mailpit bridge) and answers 200."""

    def __init__(self, status: int = 200, body: dict[str, Any] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any], dict[str, str]]] = []
        self.status = status
        self.body = body or {"ok": True}

    def __call__(self, url: str, body: dict[str, Any], headers: dict[str, str]) -> httpx.Response:
        self.calls.append((url, body, headers))
        return httpx.Response(self.status, json=self.body, request=httpx.Request("POST", url))


@pytest.fixture
def poster() -> Poster:
    return Poster()


@pytest.fixture
def store(poster: Poster) -> OpsStore:
    return OpsStore(make_sessionmaker("sqlite://"), n8n_url="http://n8n:5678", webhook_token="hook-secret",
                    poster=poster)


@pytest.fixture
def api(settings: Settings, store: OpsStore) -> Iterator[TestClient]:
    app = create_app(settings, store=store)
    with TestClient(app) as client:
        yield client


@pytest.fixture
def mocks(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_mocks(settings)) as client:
        yield client
