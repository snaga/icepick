"""Unit tests for icepick configuration management."""

from pathlib import Path

import pytest

from icepick.config import (
    Config,
    ConfigResolver,
    ConfigSource,
    OptimizerConfig,
    RuntimeConfigItem,
    RuntimeConfigSummary,
    mask_sensitive,
)


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
    monkeypatch.setenv("ICEPICK_DIALECT", "duckdb")
    monkeypatch.setenv("ICEPICK_INTERACTIVE", "true")
    monkeypatch.setenv("ICEPICK_ENABLED_RULES", "SNOW-001, SNOW-003")
    monkeypatch.setenv("ICEPICK_DISABLED_RULES", "SNOW-002")

    cfg = Config.from_env()
    assert cfg.dialect == "duckdb"
    assert cfg.interactive is True
    assert cfg.enabled_rules == ["SNOW-001", "SNOW-003"]
    assert cfg.disabled_rules == ["SNOW-002"]


def test_from_env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test creating Config from environment when no env vars are set."""
    for var in [
        "ICEPICK_DIALECT",
        "ICEPICK_INTERACTIVE",
        "ICEPICK_ENABLED_RULES",
        "ICEPICK_DISABLED_RULES",
        "ICEPICK_SHOW_DIFF",
        "ICEPICK_WRITE_IN_PLACE",
        "ICEPICK_OUTPUT_PATCH",
    ]:
        monkeypatch.delenv(var, raising=False)

    cfg = Config.from_env()
    assert cfg.dialect == "snowflake"
    assert cfg.enabled_rules == []
    assert cfg.disabled_rules == []
    assert not cfg.interactive


def test_mask_sensitive() -> None:
    """Test sensitive string masking with regular, short, empty, and None values."""
    # Standard secret string
    assert mask_sensitive("sk-1234567890abcdef") == "sk-...cdef"
    assert mask_sensitive("supersecretpassword123", prefix_len=4, suffix_len=3) == "supe...123"

    # Short secret strings (len <= prefix_len + suffix_len)
    assert mask_sensitive("secret") == "***"  # len 6 <= 7
    assert mask_sensitive("1234567") == "***"  # len 7 <= 7
    assert mask_sensitive("a") == "***"

    # None and empty strings
    assert mask_sensitive("") == ""
    assert mask_sensitive(None) == ""


def test_config_resolver_default_values(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Test resolution when no CLI, env, or file sources are present."""
    for var in [
        "ICEPICK_DIALECT",
        "ICEPICK_ENABLED_RULES",
        "ICEPICK_DISABLED_RULES",
    ]:
        monkeypatch.delenv(var, raising=False)

    monkeypatch.chdir(tmp_path)
    resolver = ConfigResolver(cli_args={}, env_vars={})
    config, summary = resolver.resolve()

    assert config.dialect == "snowflake"
    assert config.enabled_rules == []
    assert config.disabled_rules == []

    assert summary.get_source("dialect") == ConfigSource.DEFAULT
    assert summary.get_source("enabled_rules") == ConfigSource.DEFAULT


def test_config_resolver_file_precedence(tmp_path: Path) -> None:
    """Test configuration file values override defaults with ConfigSource.FILE."""
    config_file = tmp_path / "icepick.toml"
    config_file.write_text(
        """
        dialect = "duckdb"
        interactive = true
        enabled_rules = ["SNOW-001", "SNOW-002"]
        """,
        encoding="utf-8",
    )

    resolver = ConfigResolver(
        cli_args={},
        env_vars={},
        config_file=config_file,
    )
    config, summary = resolver.resolve()

    assert config.dialect == "duckdb"
    assert config.interactive is True
    assert config.enabled_rules == ["SNOW-001", "SNOW-002"]

    assert summary.get_source("dialect") == ConfigSource.FILE
    assert summary.get_source("interactive") == ConfigSource.FILE
    assert summary.get_source("enabled_rules") == ConfigSource.FILE


def test_config_resolver_json_file(tmp_path: Path) -> None:
    """Test loading configuration from JSON file."""
    json_file = tmp_path / "icepick.json"
    json_file.write_text(
        '{"dialect": "postgres", "interactive": true}',
        encoding="utf-8",
    )

    resolver = ConfigResolver(config_file=json_file, env_vars={})
    config, summary = resolver.resolve()

    assert config.dialect == "postgres"
    assert config.interactive is True
    assert summary.get_source("dialect") == ConfigSource.FILE


def test_config_resolver_file_not_found(tmp_path: Path) -> None:
    """Test FileNotFoundError is raised when explicit config file does not exist."""
    missing = tmp_path / "non_existent.toml"
    resolver = ConfigResolver(config_file=missing)
    with pytest.raises(FileNotFoundError, match="Configuration file not found"):
        resolver.resolve()


def test_config_resolver_env_precedence(tmp_path: Path) -> None:
    """Test environment variables override configuration file and default values."""
    config_file = tmp_path / "icepick.toml"
    config_file.write_text(
        """
        dialect = "snowflake"
        """,
        encoding="utf-8",
    )

    env = {
        "ICEPICK_DIALECT": "duckdb",
    }

    resolver = ConfigResolver(
        cli_args={},
        env_vars=env,
        config_file=config_file,
    )
    config, summary = resolver.resolve()

    # Overridden by ENV
    assert config.dialect == "duckdb"
    assert summary.get_source("dialect") == ConfigSource.ENV


def test_config_resolver_cli_precedence(tmp_path: Path) -> None:
    """Test CLI arguments override environment variables, file, and defaults."""
    config_file = tmp_path / "icepick.toml"
    config_file.write_text(
        """
        dialect = "snowflake"
        """,
        encoding="utf-8",
    )

    env = {
        "ICEPICK_DIALECT": "duckdb",
    }

    cli = {
        "dialect": "bigquery",
    }

    resolver = ConfigResolver(
        cli_args=cli,
        env_vars=env,
        config_file=config_file,
    )
    config, summary = resolver.resolve()

    # Overridden by CLI
    assert config.dialect == "bigquery"
    assert summary.get_source("dialect") == ConfigSource.CLI


def test_runtime_config_summary_serialization() -> None:
    """Test to_dict serialization with masked and unmasked output."""
    item_a = RuntimeConfigItem(
        key="dialect",
        value="snowflake",
        source=ConfigSource.DEFAULT,
        is_secret=False,
    )
    item_b = RuntimeConfigItem(
        key="output_patch",
        value=None,
        source=ConfigSource.DEFAULT,
        is_secret=False,
    )

    summary = RuntimeConfigSummary(
        items={
            "dialect": item_a,
            "output_patch": item_b,
        }
    )

    serialized = summary.to_dict(mask=True)
    assert serialized["dialect"]["value"] == "snowflake"
    assert serialized["dialect"]["source"] == "default"
    assert serialized["output_patch"]["value"] is None

    # Item accessor
    assert summary.get_item("dialect") is item_a
    assert summary.get_item("non_existent") is None
