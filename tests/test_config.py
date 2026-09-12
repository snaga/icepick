"""Unit tests for icepick configuration management."""

import pytest

from icepick.config import Config, OptimizerConfig


def test_default_config() -> None:
    """Test default values of Config."""
    cfg = Config()
    assert cfg.dialect == "snowflake"
    assert cfg.enabled_rules == []
    assert cfg.disabled_rules == []
    assert not cfg.interactive
    assert cfg.show_diff is True
    assert not cfg.write_in_place
    assert cfg.output_patch is None
    assert not cfg.llm_enabled
    assert cfg.llm_provider == "gemini"
    assert cfg.llm_model == "gemini-2.5-flash"


def test_optimizer_config_alias() -> None:
    """Test OptimizerConfig is an alias of Config."""
    assert OptimizerConfig is Config
    cfg = OptimizerConfig(dialect="snowflake")
    assert isinstance(cfg, Config)


def test_rule_enablement_logic() -> None:
    """Test is_rule_enabled method behavior."""
    # When no explicit lists, all rules are enabled
    cfg_all = Config()
    assert cfg_all.is_rule_enabled("SNOW-001")
    assert cfg_all.is_rule_enabled("SNOW-002")

    # Disabled rules take precedence
    cfg_disabled = Config(disabled_rules=["SNOW-002"])
    assert cfg_disabled.is_rule_enabled("SNOW-001")
    assert not cfg_disabled.is_rule_enabled("SNOW-002")
    assert not cfg_disabled.is_rule_enabled("snow-002")  # case-insensitive

    # Whitelist behavior
    cfg_enabled = Config(enabled_rules=["SNOW-001", "snow-003"])
    assert cfg_enabled.is_rule_enabled("SNOW-001")
    assert cfg_enabled.is_rule_enabled("SNOW-003")
    assert not cfg_enabled.is_rule_enabled("SNOW-002")


def test_validation_empty_dialect() -> None:
    """Test validation raises error on empty dialect."""
    with pytest.raises(ValueError, match="Dialect cannot be empty"):
        Config(dialect="")


def test_validation_conflicting_rules() -> None:
    """Test validation raises error when a rule is both enabled and disabled."""
    with pytest.raises(ValueError, match="Rules cannot be both enabled and disabled"):
        Config(enabled_rules=["SNOW-001"], disabled_rules=["snow-001"])


def test_validation_invalid_provider() -> None:
    """Test validation raises error on unsupported LLM provider."""
    with pytest.raises(ValueError, match="Unsupported llm_provider 'openai'"):
        Config(llm_provider="openai")


def test_from_dict() -> None:
    """Test creating Config from dictionary."""
    data = {
        "dialect": "snowflake",
        "interactive": True,
        "enabled_rules": ["SNOW-001"],
        "unknown_key": "ignored",
    }
    cfg = Config.from_dict(data)
    assert cfg.dialect == "snowflake"
    assert cfg.interactive is True
    assert cfg.enabled_rules == ["SNOW-001"]


def test_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test creating Config from environment variables."""
    monkeypatch.setenv("ICEPICK_DIALECT", "snowflake")
    monkeypatch.setenv("ICEPICK_INTERACTIVE", "true")
    monkeypatch.setenv("ICEPICK_ENABLED_RULES", "SNOW-001, SNOW-003")
    monkeypatch.setenv("ICEPICK_DISABLED_RULES", "SNOW-002")
    monkeypatch.setenv("ICEPICK_LLM_ENABLED", "1")
    monkeypatch.setenv("ICEPICK_LLM_PROVIDER", "vertex")
    monkeypatch.setenv("ICEPICK_LLM_MODEL", "gemini-1.5-pro")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("GCP_PROJECT", "my-project")
    monkeypatch.setenv("GCP_LOCATION", "us-central1")

    cfg = Config.from_env()
    assert cfg.dialect == "snowflake"
    assert cfg.interactive is True
    assert cfg.enabled_rules == ["SNOW-001", "SNOW-003"]
    assert cfg.disabled_rules == ["SNOW-002"]
    assert cfg.llm_enabled is True
    assert cfg.llm_provider == "vertex"
    assert cfg.llm_model == "gemini-1.5-pro"
    assert cfg.gemini_api_key == "test-key"
    assert cfg.gcp_project == "my-project"
    assert cfg.gcp_location == "us-central1"


def test_from_env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test creating Config from environment when no env vars are set."""
    # Ensure relevant env vars are absent
    for var in [
        "ICEPICK_DIALECT",
        "ICEPICK_INTERACTIVE",
        "ICEPICK_ENABLED_RULES",
        "ICEPICK_DISABLED_RULES",
        "ICEPICK_LLM_ENABLED",
        "ICEPICK_LLM_PROVIDER",
        "ICEPICK_LLM_MODEL",
        "GEMINI_API_KEY",
        "GCP_PROJECT",
        "GOOGLE_CLOUD_PROJECT",
        "GCP_LOCATION",
        "GOOGLE_CLOUD_REGION",
    ]:
        monkeypatch.delenv(var, raising=False)

    cfg = Config.from_env()
    assert cfg.dialect == "snowflake"
    assert cfg.enabled_rules == []
    assert cfg.disabled_rules == []
    assert not cfg.interactive
    assert not cfg.llm_enabled
    assert cfg.llm_provider == "gemini"
    assert cfg.llm_model == "gemini-2.5-flash"
    assert cfg.gemini_api_key is None
    assert cfg.gcp_project is None
    assert cfg.gcp_location is None
