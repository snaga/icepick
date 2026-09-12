"""Configuration management for Icepick.

Defines the configuration schema, default values, environment variable parsing,
and validation logic for query optimization and anti-pattern linting.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from typing import Any


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
        snowflake_account: Snowflake account identifier.
        snowflake_user: Snowflake username.
        snowflake_password: Optional Snowflake password.
        snowflake_database: Default Snowflake database.
        snowflake_schema: Default Snowflake schema.
        snowflake_warehouse: Default Snowflake virtual warehouse.
        snowflake_role: Optional Snowflake role.
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
    snowflake_account: str | None = None
    snowflake_user: str | None = None
    snowflake_password: str | None = None
    snowflake_database: str | None = None
    snowflake_schema: str | None = None
    snowflake_warehouse: str | None = None
    snowflake_role: str | None = None

    def __post_init__(self) -> None:
        """Normalize rule lists and perform basic validation."""
        self.dialect = self.dialect.strip().lower()
        self.enabled_rules = [r.strip().upper() for r in self.enabled_rules if r.strip()]
        self.disabled_rules = [r.strip().upper() for r in self.disabled_rules if r.strip()]
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

        Supported environment variables:
            - ICEPICK_DIALECT: Target SQL dialect (default: "snowflake")
            - ICEPICK_INTERACTIVE: Set to "1", "true", "yes" to enable interactive mode
            - ICEPICK_ENABLED_RULES: Comma-separated rule IDs (e.g. "SNOW-001,SNOW-003")
            - ICEPICK_DISABLED_RULES: Comma-separated rule IDs
            - ICEPICK_LLM_ENABLED: Set to "1", "true", "yes" to enable LLM features
            - ICEPICK_LLM_PROVIDER: "gemini" or "vertex"
            - ICEPICK_LLM_MODEL: Model identifier
            - GEMINI_API_KEY: Google AI Studio API key
            - GCP_PROJECT / GOOGLE_CLOUD_PROJECT: GCP Project ID
            - GCP_LOCATION / GOOGLE_CLOUD_REGION: GCP Region / Location
            - SNOWFLAKE_ACCOUNT: Snowflake account identifier
            - SNOWFLAKE_USER: Snowflake username
            - SNOWFLAKE_PASSWORD: Optional Snowflake password
            - SNOWFLAKE_DATABASE: Default Snowflake database
            - SNOWFLAKE_SCHEMA: Default Snowflake schema
            - SNOWFLAKE_WAREHOUSE: Default Snowflake virtual warehouse
            - SNOWFLAKE_ROLE: Optional Snowflake role

        Returns:
            Config: Configuration with environment values applied.
        """
        def _bool_from_env(key: str, default: bool = False) -> bool:
            val = os.environ.get(key)
            if val is None:
                return default
            return val.strip().lower() in ("1", "true", "yes", "on")

        def _list_from_env(key: str) -> list[str]:
            val = os.environ.get(key)
            if not val:
                return []
            return [item.strip() for item in val.split(",") if item.strip()]

        dialect = os.environ.get("ICEPICK_DIALECT", "snowflake")
        enabled_rules = _list_from_env("ICEPICK_ENABLED_RULES")
        disabled_rules = _list_from_env("ICEPICK_DISABLED_RULES")
        interactive = _bool_from_env("ICEPICK_INTERACTIVE", False)
        llm_enabled = _bool_from_env("ICEPICK_LLM_ENABLED", False)
        llm_provider = os.environ.get("ICEPICK_LLM_PROVIDER", "gemini")
        llm_model = os.environ.get("ICEPICK_LLM_MODEL", "gemini-3.8-flash")

        gemini_api_key = os.environ.get("GEMINI_API_KEY")
        gcp_project = os.environ.get("GCP_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT")
        gcp_location = os.environ.get("GCP_LOCATION") or os.environ.get("GOOGLE_CLOUD_REGION")

        snowflake_account = os.environ.get("SNOWFLAKE_ACCOUNT")
        snowflake_user = os.environ.get("SNOWFLAKE_USER")
        snowflake_password = os.environ.get("SNOWFLAKE_PASSWORD")
        snowflake_database = os.environ.get("SNOWFLAKE_DATABASE")
        snowflake_schema = os.environ.get("SNOWFLAKE_SCHEMA")
        snowflake_warehouse = os.environ.get("SNOWFLAKE_WAREHOUSE")
        snowflake_role = os.environ.get("SNOWFLAKE_ROLE")

        return cls(
            dialect=dialect,
            enabled_rules=enabled_rules,
            disabled_rules=disabled_rules,
            interactive=interactive,
            llm_enabled=llm_enabled,
            llm_provider=llm_provider,
            llm_model=llm_model,
            gemini_api_key=gemini_api_key,
            gcp_project=gcp_project,
            gcp_location=gcp_location,
            snowflake_account=snowflake_account,
            snowflake_user=snowflake_user,
            snowflake_password=snowflake_password,
            snowflake_database=snowflake_database,
            snowflake_schema=snowflake_schema,
            snowflake_warehouse=snowflake_warehouse,
            snowflake_role=snowflake_role,
        )


# Alias for backward compatibility and semantic clarity
OptimizerConfig = Config
