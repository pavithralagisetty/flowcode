"""Everything a step needs for one company run."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Callable

import yaml

from flowcode_agent.config import Config
from flowcode_agent.db import Database
from flowcode_agent.fetch import FetchedPage
from flowcode_agent.providers.llm_base import LLM
from flowcode_agent.providers.search_base import SearchProvider


@dataclass
class RunContext:
    config: Config
    db: Database
    search: SearchProvider
    llm: LLM
    fetcher: Callable[[str], FetchedPage | None]
    today: date = field(default_factory=date.today)

    @property
    def main_model(self) -> str:
        return self.config.llm.models()[0]

    @property
    def cheap_model(self) -> str:
        return self.config.llm.models()[1]

    def prompt(self, name: str) -> str:
        return (self.config.path("prompts") / f"{name}.md").read_text()

    def playbook(self) -> dict:
        return yaml.safe_load(self.config.path("playbook").read_text())
