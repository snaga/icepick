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
    assert not cfg.llm_enabled
    assert cfg.llm_provider == "gemini"
    assert cfg.llm_model == "gemini-3.8-flash"


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
    monkeypatch.setenv("DEBUG_ICEPICK_GEMINI_API_KEY", "test-key")
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
        "DEBUG_ICEPICK_GEMINI_API_KEY",
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
    assert cfg.llm_model == "gemini-3.8-flash"
    assert cfg.gemini_api_key is None
    assert cfg.gcp_project is None
    assert cfg.gcp_location is None


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
    """Test resolution when no CLI, env, file, or keyring sources are present."""
    # Clear environment variables
    for var in [
        "ICEPICK_DIALECT",
        "ICEPICK_LLM_PROVIDER",
        "ICEPICK_LLM_MODEL",
        "GEMINI_API_KEY",
        "DEBUG_ICEPICK_GEMINI_API_KEY",
    ]:
        monkeypatch.delenv(var, raising=False)

    # Empty env_vars dictionary and temporary empty directory as CWD
    monkeypatch.chdir(tmp_path)
    resolver = ConfigResolver(cli_args={}, env_vars={}, use_keyring=False)
    config, summary = resolver.resolve()

    assert config.dialect == "snowflake"
    assert config.llm_provider == "gemini"
    assert config.llm_model == "gemini-3.8-flash"
    assert config.gemini_api_key is None

    assert summary.get_source("dialect") == ConfigSource.DEFAULT
    assert summary.get_source("llm_provider") == ConfigSource.DEFAULT
    assert summary.get_source("llm_model") == ConfigSource.DEFAULT
    assert summary.get_source("gemini_api_key") == ConfigSource.DEFAULT


def test_config_resolver_file_precedence(tmp_path: Path) -> None:
    """Test configuration file values override defaults with ConfigSource.FILE."""
    config_file = tmp_path / "icepick.toml"
    config_file.write_text(
        """
        dialect = "snowflake"
        llm_provider = "vertex"
        llm_model = "gemini-1.5-pro"
        gcp_project = "my-gcp-project"
        gcp_location = "europe-west1"
        interactive = true
        enabled_rules = ["SNOW-001", "SNOW-002"]
        """,
        encoding="utf-8",
    )

    resolver = ConfigResolver(
        cli_args={},
        env_vars={},
        config_file=config_file,
        use_keyring=False,
    )
    config, summary = resolver.resolve()

    assert config.llm_provider == "vertex"
    assert config.llm_model == "gemini-1.5-pro"
    assert config.gcp_project == "my-gcp-project"
    assert config.gcp_location == "europe-west1"
    assert config.interactive is True
    assert config.enabled_rules == ["SNOW-001", "SNOW-002"]

    assert summary.get_source("llm_provider") == ConfigSource.FILE
    assert summary.get_source("llm_model") == ConfigSource.FILE
    assert summary.get_source("gcp_project") == ConfigSource.FILE
    assert summary.get_source("interactive") == ConfigSource.FILE
    assert summary.get_source("dialect") == ConfigSource.FILE


def test_config_resolver_json_file(tmp_path: Path) -> None:
    """Test loading configuration from JSON file."""
    json_file = tmp_path / "icepick.json"
    json_file.write_text(
        """{
            "llm_provider": "vertex",
            "llm_model": "gemini-1.5-flash",
            "gcp_project": "my-json-project"
        }""",
        encoding="utf-8",
    )

    resolver = ConfigResolver(config_file=json_file, env_vars={}, use_keyring=False)
    config, summary = resolver.resolve()

    assert config.llm_provider == "vertex"
    assert config.llm_model == "gemini-1.5-flash"
    assert config.gcp_project == "my-json-project"
    assert summary.get_source("gcp_project") == ConfigSource.FILE


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
        llm_provider = "vertex"
        llm_model = "file-model"
        gcp_project = "file-project"
        """,
        encoding="utf-8",
    )

    env = {
        "ICEPICK_LLM_MODEL": "env-model",
        "GCP_PROJECT": "env-project",
    }

    resolver = ConfigResolver(
        cli_args={},
        env_vars=env,
        config_file=config_file,
        use_keyring=False,
    )
    config, summary = resolver.resolve()

    # Overridden by ENV
    assert config.llm_model == "env-model"
    assert summary.get_source("llm_model") == ConfigSource.ENV
    assert config.gcp_project == "env-project"
    assert summary.get_source("gcp_project") == ConfigSource.ENV

    # Retained from FILE
    assert config.llm_provider == "vertex"
    assert summary.get_source("llm_provider") == ConfigSource.FILE


def test_config_resolver_cli_precedence(tmp_path: Path) -> None:
    """Test CLI arguments override environment variables, file, and defaults."""
    config_file = tmp_path / "icepick.toml"
    config_file.write_text(
        """
        llm_provider = "vertex"
        llm_model = "file-model"
        """,
        encoding="utf-8",
    )

    env = {
        "ICEPICK_LLM_PROVIDER": "vertex",
        "ICEPICK_LLM_MODEL": "env-model",
    }

    cli = {
        "llm_model": "cli-model",
    }

    resolver = ConfigResolver(
        cli_args=cli,
        env_vars=env,
        config_file=config_file,
        use_keyring=False,
    )
    config, summary = resolver.resolve()

    # Overridden by CLI
    assert config.llm_model == "cli-model"
    assert summary.get_source("llm_model") == ConfigSource.CLI

    # From ENV (since CLI did not specify llm_provider)
    assert config.llm_provider == "vertex"
    assert summary.get_source("llm_provider") == ConfigSource.ENV


def test_config_resolver_keyring_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test secure credentials (WCM / Keyring) are resolved as ConfigSource.KEYRING."""
    # Ensure env does not have secret
    env: dict[str, str] = {}

    # Mock read_wcm_credential_fn in credentials
    import icepick.security.credentials as creds

    def mock_wcm_read(target: str) -> str | None:
        if target == "icepick:gemini_api_key":
            return "wcm-super-secret-key-12345"
        return None

    monkeypatch.setattr(creds, "read_wcm_credential_fn", mock_wcm_read)

    resolver = ConfigResolver(
        cli_args={},
        env_vars=env,
        config_file=None,
        use_keyring=True,
    )
    config, summary = resolver.resolve()

    assert config.gemini_api_key == "wcm-super-secret-key-12345"
    assert summary.get_source("gemini_api_key") == ConfigSource.KEYRING

    # Non-secret items fall back to default
    assert summary.get_source("llm_model") == ConfigSource.DEFAULT


def test_runtime_config_summary_serialization() -> None:
    """Test to_dict serialization with masked and unmasked output."""
    item_secret = RuntimeConfigItem(
        key="gemini_api_key",
        value="sk-1234567890abcdef",
        source=ConfigSource.KEYRING,
        is_secret=True,
    )
    item_plain = RuntimeConfigItem(
        key="llm_provider",
        value="gemini",
        source=ConfigSource.CLI,
        is_secret=False,
    )

    summary = RuntimeConfigSummary(
        items={
            "gemini_api_key": item_secret,
            "llm_provider": item_plain,
        }
    )

    # Masked serialization (default)
    masked = summary.to_dict(mask=True)
    assert masked["gemini_api_key"]["value"] == "sk-...cdef"
    assert masked["gemini_api_key"]["source"] == "keyring"
    assert masked["llm_provider"]["value"] == "gemini"
    assert masked["llm_provider"]["source"] == "cli"

    # Unmasked serialization
    unmasked = summary.to_dict(mask=False)
    assert unmasked["gemini_api_key"]["value"] == "sk-1234567890abcdef"
    assert unmasked["gemini_api_key"]["source"] == "keyring"
    assert unmasked["llm_provider"]["value"] == "gemini"
    assert unmasked["llm_provider"]["source"] == "cli"

    # Item accessor
    assert summary.get_item("gemini_api_key") is item_secret
    assert summary.get_item("non_existent") is None


def test_sensitive_credentials_ignored_in_config_file(tmp_path: Path) -> None:
    """Test that sensitive credentials in configuration files are strictly ignored."""
    toml_file = tmp_path / "icepick.toml"
    toml_file.write_text(
        """
        dialect = "duckdb"
        gemini_api_key = "insecure_api_key"
        gcp_project = "my-project"
        """,
        encoding="utf-8",
    )

    resolver = ConfigResolver(config_file=toml_file, env_vars={}, use_keyring=False)
    config, summary = resolver.resolve()

    # Non-sensitive items are loaded from FILE
    assert config.dialect == "duckdb"
    assert summary.get_source("dialect") == ConfigSource.FILE
    assert config.gcp_project == "my-project"
    assert summary.get_source("gcp_project") == ConfigSource.FILE

    # Sensitive items must NOT be loaded from FILE
    assert config.gemini_api_key is None
    assert summary.get_source("gemini_api_key") == ConfigSource.DEFAULT


def test_sensitive_credentials_ambient_env_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test generic ambient env variables are ignored, only DEBUG_ICEPICK_* are resolved."""
    # 1. Generic ambient variables should be ignored
    ambient_env = {
        "GEMINI_API_KEY": "ambient_gemini_key",
        "GCP_PROJECT": "ambient_gcp_project",
    }
    resolver = ConfigResolver(env_vars=ambient_env, use_keyring=False)
    config, summary = resolver.resolve()

    # Non-sensitive item is resolved from ENV
    assert config.gcp_project == "ambient_gcp_project"
    assert summary.get_source("gcp_project") == ConfigSource.ENV

    # Sensitive items are ignored from generic ambient env
    assert config.gemini_api_key is None
    assert summary.get_source("gemini_api_key") == ConfigSource.DEFAULT

    # 2. DEBUG_ICEPICK_* variables must be accepted
    debug_env = {
        "DEBUG_ICEPICK_GEMINI_API_KEY": "debug_gemini_key",
    }
    resolver_debug = ConfigResolver(env_vars=debug_env, use_keyring=False)
    config_debug, summary_debug = resolver_debug.resolve()

    assert config_debug.gemini_api_key == "debug_gemini_key"
    assert summary_debug.get_source("gemini_api_key") == ConfigSource.ENV
