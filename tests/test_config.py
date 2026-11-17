import pytest
import yaml

from flowcode_agent.config import Config, load_config
from tests.conftest import ROOT


def test_real_config_loads_and_blocks_flowcode():
    cfg = load_config(ROOT / "config.yaml")
    assert "flowcode.com" in cfg.research.blocked_domains
    assert cfg.search.gemini_model != "SET_ME"


def test_weights_must_sum_to_one():
    data = yaml.safe_load((ROOT / "config.yaml").read_text())
    data["scoring"]["opportunity_weights"]["timing"] = 0.5
    with pytest.raises(ValueError, match="opportunity_weights"):
        Config(**data)


def test_flowcode_blocked_even_if_removed():
    data = yaml.safe_load((ROOT / "config.yaml").read_text())
    data["research"]["blocked_domains"] = []
    assert "flowcode.com" in Config(**data).research.blocked_domains
