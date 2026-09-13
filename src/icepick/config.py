"""Configuration management for Icepick.

Defines the configuration schema, default values, cascading resolution
across CLI, environment variables, configuration files, and secure keyrings,
along with runtime provenance tracking.
"""

from __future__ import annotations

import dataclasses
import json
import os
from dataclasses import dataclass, field, fields
from enum import Enum
from pathlib import Path
from typing import Any

try:
    import tomllib  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[import-not-found]


class ConfigSource(str, Enum):
    """Source layer that determined a configuration value."""

    CLI = "cli"
    ENV = "env"
    FILE = "file"
    KEYRING = "keyring"
    DEFAULT = "default"


def mask_sensitive(value: str | None, prefix_len: int = 3, suffix_len: int = 4) -> str:
    """Mask sensitive string to protect secrets in logs, terminal, and serialized outputs.

    Args:
        value: Secret string to mask (e.g. API keys or passwords).
        prefix_len: Number of plain characters to reveal at the start (default: 3).
        suffix_len: Number of plain characters to reveal at the end (default: 4).

    Returns:
        Masked string (e.g., 'sk-...abcd') or safe fallback for short / empty strings.
    """
    if not value:
        return ""
    if len(value) <= (prefix_len + suffix_len):
        return "***"
    return f"{value[:prefix_len]}...{value[-suffix_len:]}"


@dataclass
class RuntimeConfigItem:
    """Represents a single configuration setting and its resolution provenance.

    Attributes:
        key: The configuration field name.
        value: The resolved configuration value.
        source: The source layer that determined the value.
        is_secret: Whether this item contains sensitive credentials.
    """

    key: str
    value: Any
    source: ConfigSource
    is_secret: bool = False

    def display_value(self) -> str:
        """Return a string representation safe for display, masking secrets if needed."""
        if self.value is None:
            return ""
        if self.is_secret:
            return mask_sensitive(str(self.value))
        return str(self.value)

    def to_dict(self, mask: bool = True) -> dict[str, Any]:
        """Convert item to a dictionary representation.

        Args:
            mask: Whether to mask sensitive values.

        Returns:
            Dictionary containing 'value' and 'source'.
        """
        if mask and self.is_secret and self.value is not None:
            val = self.display_value()
        else:
            val = self.value
        return {
            "value": val,
            "source": self.source.value,
        }


@dataclass
class RuntimeConfigSummary:
    """Summary of all resolved configuration items and their sources."""

    items: dict[str, RuntimeConfigItem] = field(default_factory=dict)

    def to_dict(self, mask: bool = True) -> dict[str, Any]:
        """Convert all configuration items to a nested dictionary representation.

        Args:
            mask: Whether to mask sensitive items.

        Returns:
            Mapping of configuration keys to their serialized items.
        """
        return {key: item.to_dict(mask=mask) for key, item in self.items.items()}

    def get_source(self, key: str) -> ConfigSource | None:
        """Get the resolution source for a given configuration key."""
        item = self.items.get(key)
        return item.source if item is not None else None

    def get_item(self, key: str) -> RuntimeConfigItem | None:
        """Get the RuntimeConfigItem for a given configuration key."""
        return self.items.get(key)


@dataclass
class Config:
    """Configuration settings for Icepick optimizer and linter.

    Attributes:
        dialect: SQL dialect name supported by sqlglot (default: "snowflake").
        enabled_rules: Whitelist of rule IDs to run. If empty, all non-disabled rules are active.
        disabled_rules: Blacklist of rule IDs to skip.
        interactive: Whether to prompt for confirmation before applying each hunk/rewrite.
        show_diff: Whether to output colored Unified Diff in terminal.
        write_in_place: Whether to overwrite the target SQL file directly.
        output_patch: Optional path to output the generated Unified Diff as a .patch file.
        llm_enabled: Whether to allow LLM-based local AST rewriting (e.g. for correlated subqueries).
        llm_provider: LLM service provider ("gemini" or "vertex").
        llm_model: Model name to invoke for rewrites.
        gemini_api_key: API key for Google AI Studio Gemini API (or loaded via GEMINI_API_KEY env).
        gcp_project: GCP project ID when using Vertex AI.
        gcp_location: GCP location/region when using Vertex AI.
        llm_options: Generic provider-specific options passed to the pluggable LLM provider.
            Supports arbitrary key-value pairs from [llm.options] TOML table or llm_options JSON key.
    """

    dialect: str = "snowflake"
    enabled_rules: list[str] = field(default_factory=list)
    disabled_rules: list[str] = field(default_factory=list)
    interactive: bool = False
    show_diff: bool = True
    write_in_place: bool = False
    output_patch: str | None = None
    llm_enabled: bool = False
    llm_provider: str = "gemini"
    llm_model: str = "gemini-3.8-flash"
    gemini_api_key: str | None = None
    gcp_project: str | None = None
    gcp_location: str | None = None
    llm_options: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Normalize rule lists and perform basic validation."""
        self.dialect = self.dialect.strip().lower()
        self.enabled_rules = [r.strip().upper() for r in self.enabled_rules if r.strip()]
        self.disabled_rules = [r.strip().upper() for r in self.disabled_rules if r.strip()]
        if not isinstance(self.llm_options, dict):
            self.llm_options = {}
        self.validate()

    def validate(self) -> None:
        """Validate configuration settings.

        Raises:
            ValueError: If configuration values are invalid or conflicting.
        """
        if not self.dialect:
            raise ValueError("Dialect cannot be empty.")

        conflict = set(self.enabled_rules) & set(self.disabled_rules)
        if conflict:
            raise ValueError(f"Rules cannot be both enabled and disabled: {sorted(conflict)}")

        valid_providers = {"gemini", "vertex"}
        if self.llm_provider not in valid_providers:
            raise ValueError(
                f"Unsupported llm_provider '{self.llm_provider}'. Expected one of {valid_providers}."
            )

    def is_rule_enabled(self, rule_id: str) -> bool:
        """Determine whether a specific rule should be executed.

        Args:
            rule_id: The identifier of the rule (e.g., 'SNOW-001').

        Returns:
            bool: True if the rule is enabled, False otherwise.
        """
        normalized_id = rule_id.strip().upper()
        if normalized_id in self.disabled_rules:
            return False
        if self.enabled_rules:
            return normalized_id in self.enabled_rules
        return True

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Config:
        """Create a Config instance from a dictionary.

        Args:
            data: Key-value mapping of configuration options.

        Returns:
            Config: An initialized and validated Config instance.
        """
        valid_fields = {f.name for f in fields(cls)}
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered)

    @classmethod
    def from_env(cls) -> Config:
        """Create a Config instance populated with environment variables.

        Returns:
            Config: Configuration with environment values applied.
        """
        resolver = ConfigResolver(use_keyring=False)
        config, _ = resolver.resolve()
        return config


# Mapping of Config fields to supported environment variables (in priority order)
_ENV_VAR_MAPPING: dict[str, list[str]] = {
    "dialect": ["ICEPICK_DIALECT"],
    "enabled_rules": ["ICEPICK_ENABLED_RULES"],
    "disabled_rules": ["ICEPICK_DISABLED_RULES"],
    "interactive": ["ICEPICK_INTERACTIVE"],
    "show_diff": ["ICEPICK_SHOW_DIFF"],
    "write_in_place": ["ICEPICK_WRITE_IN_PLACE"],
    "output_patch": ["ICEPICK_OUTPUT_PATCH"],
    "llm_enabled": ["ICEPICK_LLM_ENABLED"],
    "llm_provider": ["ICEPICK_LLM_PROVIDER"],
    "llm_model": ["ICEPICK_LLM_MODEL"],
    "gemini_api_key": ["DEBUG_ICEPICK_GEMINI_API_KEY"],
    "gcp_project": ["GCP_PROJECT", "GOOGLE_CLOUD_PROJECT"],
    "gcp_location": ["GCP_LOCATION", "GOOGLE_CLOUD_REGION"],
}


class ConfigResolver:
    """Cascading configuration resolver with provenance tracking.

    Resolves configuration values across the priority pyramid:
        CLI > ENV > FILE > KEYRING > DEFAULT
    """

    SECRET_KEYS: frozenset[str] = frozenset({"gemini_api_key"})

    def __init__(
        self,
        cli_args: dict[str, Any] | None = None,
        env_vars: dict[str, str] | None = None,
        config_file: Path | str | None = None,
        use_keyring: bool = True,
    ) -> None:
        """Initialize the ConfigResolver.

        Args:
            cli_args: Command-line arguments mapping.
            env_vars: Environment variables dictionary (defaults to os.environ).
            config_file: Explicit path to configuration file (.toml or .json).
            use_keyring: Whether to resolve secrets from secure credentials (WCM / Keyring).
        """
        self.cli_args: dict[str, Any] = cli_args or {}
        self.env_vars: dict[str, str] | None = env_vars
        self.config_file: Path | None = Path(config_file) if config_file is not None else None
        self.use_keyring: bool = use_keyring

    def _load_config_file(self) -> dict[str, Any]:
        """Load configuration from file if specified or found in CWD.

        Returns:
            Parsed configuration dictionary.

        Raises:
            FileNotFoundError: If an explicitly requested config_file does not exist.
        """
        target_path: Path | None = None
        if self.config_file is not None:
            if not self.config_file.is_file():
                raise FileNotFoundError(f"Configuration file not found: {self.config_file}")
            target_path = self.config_file
        else:
            cwd = Path.cwd()
            for candidate in [cwd / ".icepick.toml", cwd / "icepick.json"]:
                if candidate.is_file():
                    target_path = candidate
                    break

        if target_path is None:
            return {}

        content = target_path.read_text(encoding="utf-8")
        data: dict[str, Any]
        if target_path.suffix.lower() == ".json":
            data = json.loads(content)
        else:
            # Default to TOML (standard in Python 3.11+)
            data = tomllib.loads(content)

        if not isinstance(data, dict):
            return {}

        # Support both [icepick] sub-table and top-level configuration
        config_data = (
            data["icepick"] if "icepick" in data and isinstance(data["icepick"], dict) else data
        )
        if not isinstance(config_data, dict):
            return {}

        # Strictly exclude secrets from config files to prevent accidental leakage
        result = {k: v for k, v in config_data.items() if k not in self.SECRET_KEYS}

        # Merge [llm.options] TOML sub-table (or "llm_options" flat key in JSON) into llm_options.
        # Priority: explicit "llm_options" dict key first, then nested [llm][options] sub-table.
        merged_llm_options: dict[str, Any] = {}

        # TOML: [llm.options] appears as data["llm"]["options"] inside config_data
        llm_section = config_data.get("llm")
        if isinstance(llm_section, dict):
            opts = llm_section.get("options")
            if isinstance(opts, dict):
                merged_llm_options.update(opts)

        # Flat key "llm_options" (e.g. JSON: {"llm_options": {...}}) overrides nested [llm.options]
        flat_opts = result.get("llm_options")
        if isinstance(flat_opts, dict):
            merged_llm_options.update(flat_opts)

        if merged_llm_options:
            result["llm_options"] = merged_llm_options

        return result

    def _get_env_value(self, key: str, env: dict[str, str]) -> Any:
        """Extract and parse value for a key from environment variables mapping."""
        env_names = _ENV_VAR_MAPPING.get(key, [f"ICEPICK_{key.upper()}"])
        raw_val: str | None = None
        for name in env_names:
            if name in env and env[name] != "":
                raw_val = env[name]
                break

        if raw_val is None:
            return None

        # Parse boolean values
        if key in {"interactive", "show_diff", "write_in_place", "llm_enabled"}:
            return raw_val.strip().lower() in ("1", "true", "yes", "on")

        # Parse comma-separated lists
        if key in {"enabled_rules", "disabled_rules"}:
            return [item.strip() for item in raw_val.split(",") if item.strip()]

        return raw_val.strip()

    def _get_keyring_value(self, key: str) -> str | None:
        """Resolve a sensitive credential from Keyring / WCM."""
        if not self.use_keyring or key not in self.SECRET_KEYS:
            return None

        try:
            from icepick.credentials import read_wcm_credential

            target = f"icepick:{key.lower()}"
            return read_wcm_credential(target)
        except Exception:  # noqa: BLE001
            return None

    def resolve(self) -> tuple[Config, RuntimeConfigSummary]:
        """Resolve all configuration settings according to the priority pyramid.

        Returns:
            tuple[Config, RuntimeConfigSummary]: Validated Config and summary with source metadata.
        """
        file_cfg = self._load_config_file()
        env = self.env_vars if self.env_vars is not None else dict(os.environ)

        summary_items: dict[str, RuntimeConfigItem] = {}
        config_kwargs: dict[str, Any] = {}

        for f in fields(Config):
            key = f.name
            is_secret = key in self.SECRET_KEYS
            val: Any = None
            src: ConfigSource

            # 1. CLI option
            if key in self.cli_args and self.cli_args[key] is not None:
                val = self.cli_args[key]
                src = ConfigSource.CLI

            # 2. Environment variable
            elif (env_val := self._get_env_value(key, env)) is not None:
                val = env_val
                src = ConfigSource.ENV

            # 3. Configuration file (Strictly ignore secret keys to avoid plaintext leakage)
            elif key not in self.SECRET_KEYS and key in file_cfg and file_cfg[key] is not None:
                val = file_cfg[key]
                src = ConfigSource.FILE

            # 4. Keyring / Windows Credential Manager
            elif (keyring_val := self._get_keyring_value(key)) is not None:
                val = keyring_val
                src = ConfigSource.KEYRING

            # 5. Embedded default
            else:
                if f.default is not dataclasses.MISSING:
                    val = f.default
                elif f.default_factory is not dataclasses.MISSING:
                    val = f.default_factory()
                else:
                    val = None
                src = ConfigSource.DEFAULT

            config_kwargs[key] = val
            summary_items[key] = RuntimeConfigItem(
                key=key,
                value=val,
                source=src,
                is_secret=is_secret,
            )

        config = Config(**config_kwargs)
        summary = RuntimeConfigSummary(items=summary_items)
        return config, summary


# Alias for backward compatibility and semantic clarity
OptimizerConfig = Config
