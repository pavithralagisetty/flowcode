"""Shared fixtures. Tests never touch the network."""

from __future__ import annotations

import os
import socket
from datetime import date
from pathlib import Path
from typing import Callable

import pytest
import yaml
from pydantic import BaseModel

from flowcode_agent.config import Config
from flowcode_agent.context import RunContext
from flowcode_agent.db import TABLES, Database
from flowcode_agent.fetch import FetchedPage, extract_page
from flowcode_agent.providers.llm_base import BaseLLM
from flowcode_agent.providers.search_fake import FakeSearchProvider

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
TODAY = date(2026, 11, 16)

# Set this to a postgres:// URL (an empty, throwaway database) to run every test against PostgreSQL.
PG_TEST_ENV = "FLOWCODE_TEST_DATABASE_URL"


def _reset_postgres(url: str) -> None:
    """Drop every table so each test starts from an empty database."""
    import psycopg

    with psycopg.connect(url, autocommit=True) as conn:
        for table in TABLES:
            conn.execute(f"DROP TABLE IF EXISTS {table} CASCADE")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def guard(*args, **kwargs):
        raise RuntimeError("Network access is not allowed in tests")

    monkeypatch.setattr(socket.socket, "connect", guard)
    monkeypatch.setattr(socket, "create_connection", guard)


class FakeLLM(BaseLLM):
    """Returns scripted replies. `handlers` maps a schema class to a function(user) -> model or raw str."""

    name = "fake"

    def __init__(self, handlers: dict[type[BaseModel], Callable[[str], BaseModel | str]]):
        super().__init__()
        self.handlers = handlers
        self.calls: list[tuple[str, str]] = []
        self._schema: type[BaseModel] | None = None

    def complete_json(self, system, user, schema, model):
        self._schema = schema
        return super().complete_json(system, user, schema, model)

    def _raw(self, system, messages, model):
        self.calls.append((self._schema.__name__, messages[-1]["content"]))
        reply = self.handlers[self._schema](messages[-1]["content"])
        text = reply if isinstance(reply, str) else reply.model_dump_json()
        return text, 100, 50


class FakeFetcher:
    """Serves saved HTML fixtures by URL."""

    def __init__(self, pages: dict[str, str]):
        self.pages = pages  # url -> fixture filename
        self.fetched: list[str] = []

    def __call__(self, url: str) -> FetchedPage | None:
        self.fetched.append(url)
        name = self.pages.get(url)
        if not name:
            return None
        return extract_page((FIXTURES / name).read_text(), url, url)

    def check_alive(self, url: str) -> bool:
        return url in self.pages


@pytest.fixture
def config(tmp_path) -> Config:
    data = yaml.safe_load((ROOT / "config.yaml").read_text())
    data["search"]["provider"] = "fake"
    data["paths"].update(db=str(tmp_path / "test.db"), outputs=str(tmp_path / "outputs"))
    if os.environ.get(PG_TEST_ENV):
        _reset_postgres(os.environ[PG_TEST_ENV])
        data["storage"] = {"backend": "postgres", "url_env": PG_TEST_ENV}
    else:
        data["storage"] = {"backend": "sqlite"}
    return Config(**data, base_dir=ROOT)


@pytest.fixture
def db(config) -> Database:
    database = Database(config.db_target())
    yield database
    database.close()


def make_ctx(config, db, search=None, llm=None, fetcher=None) -> RunContext:
    return RunContext(config=config, db=db, search=search or FakeSearchProvider({}), llm=llm or FakeLLM({}),
                      fetcher=fetcher or FakeFetcher({}), today=TODAY)
