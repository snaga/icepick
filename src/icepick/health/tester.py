"""Connection health tester module for proactive service diagnostics.

Validates authentication, network latency, and driver readiness for LLM
(Gemini / Vertex AI) and Snowflake services, providing actionable remediation
advice upon failure.
"""

from __future__ import annotations

import importlib
import logging
import os
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx

from icepick.config import Config
from icepick.credentials import (
    AuthenticationError,
    format_actionable_error,
    format_actionable_pair_error,
    resolve_credential_pair,
)
from icepick.llm.client import LLMClient

logger = logging.getLogger(__name__)


@dataclass
class ServiceTestResult:
    """Diagnostic outcome of a connection and responsiveness check for a single service.

    Attributes:
        service: Target service identifier (e.g. "llm", "snowflake").
        success: True if connection, authentication, and ping query succeeded.
        duration_ms: Round-trip response time in milliseconds.
        message: Human-readable status message or error summary.
        details: Diagnostic metadata such as model name, remote version, or warehouse.
        actionable_advice: Remediation guidance and CLI commands when test fails.
    """

    service: str
    success: bool
    duration_ms: float
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    actionable_advice: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert test result into a JSON-serializable dictionary.

        Returns:
            dict[str, Any]: Serialized representation of service diagnostic result.
        """
        return {
            "service": self.service,
            "success": self.success,
            "duration_ms": self.duration_ms,
            "message": self.message,
            "details": self.details,
            "actionable_advice": self.actionable_advice,
        }


@dataclass
class ConnectionHealthReport:
    """Aggregated health report containing test results for multiple services.

    Attributes:
        results: Mapping from service names to individual ServiceTestResult objects.
    """

    results: dict[str, ServiceTestResult] = field(default_factory=dict)

    @property
    def all_passed(self) -> bool:
        """Evaluate if all tested services succeeded.

        Returns:
            bool: True if results is non-empty and every test succeeded, False otherwise.
        """
        return bool(self.results) and all(r.success for r in self.results.values())

    def to_dict(self) -> dict[str, Any]:
        """Convert aggregated report into a JSON-serializable dictionary.

        Returns:
            dict[str, Any]: Structured dictionary with overall status and per-service results.
        """
        return {
            "all_passed": self.all_passed,
            "results": {k: v.to_dict() for k, v in self.results.items()},
        }


class ConnectionTester:
    """Proactive health check engine for LLM and Snowflake database connections.

    Performs lightweight authentication and round-trip verification to ensure
    all backend dependencies and credentials are functional before running
    costly optimization or equivalence verification workflows.
    """

    def test_llm(self, cfg: Config, timeout: float = 10.0) -> ServiceTestResult:
        """Test connection and round-trip responsiveness to the configured LLM provider.

        Sends a minimal ping prompt to verify API key or OAuth token validity,
        endpoint connectivity, and model responsiveness.

        Args:
            cfg: Configuration containing provider, model, and authentication settings.
            timeout: HTTP request timeout in seconds (default: 10.0).

        Returns:
            ServiceTestResult: Diagnostic details including round-trip latency and advice.
        """
        start = time.perf_counter()
        provider = getattr(cfg, "llm_provider", "gemini")
        model = getattr(cfg, "llm_model", "gemini-3.8-flash")
        norm_provider = provider.strip().lower() if provider else "gemini"
        advice: str | None = None

        try:
            client = LLMClient(config=cfg, timeout=timeout)

            # Delegate to provider health_check if supported and returns a dict
            health_res: dict[str, Any] | None = None
            if hasattr(client, "health_check"):
                try:
                    res = client.health_check()
                    if isinstance(res, dict):
                        health_res = res
                except Exception:  # noqa: BLE001
                    health_res = None
            elif hasattr(client, "_provider") and hasattr(client._provider, "health_check"):
                try:
                    res = client._provider.health_check()
                    if isinstance(res, dict):
                        health_res = res
                except Exception:  # noqa: BLE001
                    health_res = None

            if isinstance(health_res, dict):
                success = bool(health_res.get("success", False))
                details = dict(health_res.get("details", {}))
                if "provider" not in details:
                    details["provider"] = getattr(client, "provider", norm_provider)
                if "model" not in details:
                    details["model"] = getattr(client, "model", model)
                if getattr(client, "provider", norm_provider) == "vertex":
                    if "project" not in details and getattr(client, "project", None):
                        details["project"] = client.project
                    if "location" not in details and getattr(client, "location", None):
                        details["location"] = client.location

                duration_ms = float(
                    health_res.get("duration_ms", round((time.perf_counter() - start) * 1000.0, 2))
                )
                message = "Successfully connected to LLM provider." if success else str(health_res.get("message", ""))
                advice = health_res.get("actionable_advice")

                return ServiceTestResult(
                    service="llm",
                    success=success,
                    duration_ms=duration_ms,
                    message=message,
                    details=details,
                    actionable_advice=advice,
                )

            response = client.generate_text("ping")
            duration_ms = round((time.perf_counter() - start) * 1000.0, 2)

            details = {
                "provider": client.provider,
                "model": client.model,
                "response_snippet": response[:100].strip() if response else "",
            }
            if client.provider == "vertex":
                details["project"] = client.project
                details["location"] = client.location

            return ServiceTestResult(
                service="llm",
                success=True,
                duration_ms=duration_ms,
                message="Successfully connected to LLM provider.",
                details=details,
            )

        except AuthenticationError as exc:
            duration_ms = round((time.perf_counter() - start) * 1000.0, 2)
            advice = format_actionable_error("gemini_api_key")
            return ServiceTestResult(
                service="llm",
                success=False,
                duration_ms=duration_ms,
                message=str(exc),
                details={"provider": norm_provider, "model": model},
                actionable_advice=advice,
            )

        except ValueError as exc:
            duration_ms = round((time.perf_counter() - start) * 1000.0, 2)
            err_msg = str(exc)
            err_lower = err_msg.lower()

            advice = None
            if (
                "adc" in err_lower
                or "gcloud auth" in err_lower
                or "token" in err_lower
                or norm_provider in {"vertex", "vertex_ai", "vertexai"}
            ):
                if "project" in err_lower:
                    advice = (
                        "Please configure Google Cloud project via 'gcp_project' in config "
                        "or set the 'GOOGLE_CLOUD_PROJECT' environment variable."
                    )
                else:
                    advice = (
                        "Please run 'gcloud auth application-default login' to authenticate with "
                        "Google Cloud ADC."
                    )
            elif "unsupported llm provider" in err_lower:
                advice = "Supported LLM providers are 'gemini' and 'vertex'."

            return ServiceTestResult(
                service="llm",
                success=False,
                duration_ms=duration_ms,
                message=err_msg,
                details={"provider": norm_provider, "model": model},
                actionable_advice=advice,
            )

        except httpx.HTTPStatusError as exc:
            duration_ms = round((time.perf_counter() - start) * 1000.0, 2)
            status = exc.response.status_code
            advice = None

            if status in (401, 403):
                if norm_provider in {"gemini", "google"}:
                    advice = format_actionable_error("gemini_api_key")
                else:
                    advice = (
                        "Please run 'gcloud auth application-default login' to authenticate with "
                        "Google Cloud ADC."
                    )
            elif status == 404:
                advice = f"Verify that model '{model}' exists and is accessible under your account/project."
            else:
                advice = f"LLM endpoint responded with HTTP status {status}. Please inspect your network and quotas."

            return ServiceTestResult(
                service="llm",
                success=False,
                duration_ms=duration_ms,
                message=f"HTTP {status}: {exc.response.text}",
                details={"provider": norm_provider, "model": model},
                actionable_advice=advice,
            )

        except httpx.TimeoutException as exc:
            duration_ms = round((time.perf_counter() - start) * 1000.0, 2)
            advice = (
                "The LLM request timed out. Please check your network connection, proxy settings, "
                "or increase the timeout value."
            )
            return ServiceTestResult(
                service="llm",
                success=False,
                duration_ms=duration_ms,
                message=f"LLM request timed out after {timeout}s: {exc}",
                details={"provider": norm_provider, "model": model},
                actionable_advice=advice,
            )

        except Exception as exc:  # noqa: BLE001
            duration_ms = round((time.perf_counter() - start) * 1000.0, 2)
            err_str = str(exc)
            err_lower = err_str.lower()
            advice = None

            if norm_provider in {"gemini", "google"} and (
                "key" in err_lower or "auth" in err_lower or "credential" in err_lower
            ):
                advice = format_actionable_error("gemini_api_key")
            elif norm_provider in {"vertex", "vertex_ai"} and (
                "adc" in err_lower or "auth" in err_lower or "token" in err_lower
            ):
                advice = (
                    "Please run 'gcloud auth application-default login' to authenticate with "
                    "Google Cloud ADC."
                )

            return ServiceTestResult(
                service="llm",
                success=False,
                duration_ms=duration_ms,
                message=err_str,
                details={"provider": norm_provider, "model": model},
                actionable_advice=advice,
            )

    def test_snowflake(self, cfg: Config, timeout: float = 10.0) -> ServiceTestResult:
        """Test connection, authentication, and execution in Snowflake.

        Validates account credentials, opens a live session, and executes a lightweight
        ping query (SELECT CURRENT_VERSION(), CURRENT_USER(), CURRENT_WAREHOUSE()).

        Args:
            cfg: Configuration containing Snowflake connection and authentication settings.
            timeout: Connection and query timeout in seconds (default: 10.0).

        Returns:
            ServiceTestResult: Detailed Snowflake metadata or remediation advice.
        """
        start = time.perf_counter()

        account = getattr(cfg, "snowflake_account", None) or os.environ.get("SNOWFLAKE_ACCOUNT")
        database = getattr(cfg, "snowflake_database", None) or os.environ.get("SNOWFLAKE_DATABASE")
        schema = getattr(cfg, "snowflake_schema", None) or os.environ.get("SNOWFLAKE_SCHEMA")
        warehouse = getattr(cfg, "snowflake_warehouse", None) or os.environ.get(
            "SNOWFLAKE_WAREHOUSE"
        )
        role = getattr(cfg, "snowflake_role", None) or os.environ.get("SNOWFLAKE_ROLE")

        user = getattr(cfg, "snowflake_user", None)
        password = getattr(cfg, "snowflake_password", None)

        # Fallback resolution of credential pair from WCM or debug env vars
        if not user or not password:
            try:
                pair_user, pair_pass, _ = resolve_credential_pair("snowflake")
                if not user:
                    user = pair_user
                if not password:
                    password = pair_pass
            except Exception as exc:  # noqa: BLE001
                logger.debug("Failed to resolve Snowflake credential pair fallback: %s", exc)

        missing_auth: list[str] = []
        if not user:
            missing_auth.append("user")
        if not password:
            missing_auth.append("password")

        missing_infra: list[str] = []
        if not account:
            missing_infra.append("account")
        if not database:
            missing_infra.append("database")
        if not warehouse:
            missing_infra.append("warehouse")

        all_missing = missing_auth + missing_infra
        if all_missing:
            duration_ms = round((time.perf_counter() - start) * 1000.0, 2)
            advice_parts: list[str] = []
            if missing_auth:
                advice_parts.append(format_actionable_pair_error("snowflake"))
            if missing_infra:
                advice_parts.append(
                    f"Missing connection parameter(s): {', '.join(missing_infra)}. "
                    "Please configure them in your config file (.icepick.toml) "
                    "or set environment variables (e.g. SNOWFLAKE_ACCOUNT)."
                )

            return ServiceTestResult(
                service="snowflake",
                success=False,
                duration_ms=duration_ms,
                message=f"Missing required Snowflake parameter(s): {', '.join(all_missing)}.",
                details={
                    "account": account,
                    "database": database,
                    "warehouse": warehouse,
                    "schema": schema,
                    "role": role,
                },
                actionable_advice="\n\n".join(advice_parts),
            )

        # Verify snowflake-connector-python driver availability
        try:
            snowflake_connector = importlib.import_module("snowflake.connector")
        except ImportError:
            duration_ms = round((time.perf_counter() - start) * 1000.0, 2)
            return ServiceTestResult(
                service="snowflake",
                success=False,
                duration_ms=duration_ms,
                message="snowflake-connector-python is not installed.",
                details={
                    "account": account,
                    "database": database,
                    "warehouse": warehouse,
                    "schema": schema,
                    "role": role,
                },
                actionable_advice=(
                    "Please install it via 'pip install snowflake-connector-python' "
                    "or 'uv add snowflake-connector-python'."
                ),
            )

        conn_params: dict[str, Any] = {
            "account": account,
            "user": user,
            "password": password,
            "database": database,
            "warehouse": warehouse,
            "login_timeout": max(1, int(timeout)),
            "network_timeout": max(1, int(timeout)),
        }
        if schema:
            conn_params["schema"] = schema
        if role:
            conn_params["role"] = role

        try:
            conn = snowflake_connector.connect(**conn_params)
            try:
                cursor = conn.cursor()
                try:
                    cursor.execute("SELECT CURRENT_VERSION(), CURRENT_USER(), CURRENT_WAREHOUSE()")
                    row = cursor.fetchone()
                    duration_ms = round((time.perf_counter() - start) * 1000.0, 2)

                    version = str(row[0]) if row and len(row) > 0 else "unknown"
                    cur_user = str(row[1]) if row and len(row) > 1 else str(user)
                    cur_wh = str(row[2]) if row and len(row) > 2 else str(warehouse)

                    return ServiceTestResult(
                        service="snowflake",
                        success=True,
                        duration_ms=duration_ms,
                        message="Successfully connected to Snowflake.",
                        details={
                            "version": version,
                            "user": cur_user,
                            "warehouse": cur_wh,
                            "account": account,
                            "database": database,
                            "schema": schema or "",
                            "role": role or "",
                        },
                    )
                finally:
                    if hasattr(cursor, "close") and callable(cursor.close):
                        cursor.close()
            finally:
                if hasattr(conn, "close") and callable(conn.close):
                    conn.close()

        except Exception as exc:  # noqa: BLE001
            duration_ms = round((time.perf_counter() - start) * 1000.0, 2)
            err_str = str(exc)
            err_lower = err_str.lower()

            if (
                "incorrect username or password" in err_lower
                or "250001" in err_str
                or "authentication failed" in err_lower
            ):
                advice = format_actionable_pair_error("snowflake")
            else:
                advice = (
                    "Please verify your Snowflake account identifier, network/VPN access, "
                    "database/warehouse privileges, and credentials."
                )

            return ServiceTestResult(
                service="snowflake",
                success=False,
                duration_ms=duration_ms,
                message=err_str,
                details={
                    "account": account,
                    "database": database,
                    "warehouse": warehouse,
                    "schema": schema or "",
                    "role": role or "",
                },
                actionable_advice=advice,
            )

    def test_all(
        self,
        cfg: Config,
        targets: Sequence[str] = ("llm", "snowflake"),
        timeout: float = 10.0,
    ) -> ConnectionHealthReport:
        """Run connectivity diagnostics for all requested services.

        Args:
            cfg: Active configuration.
            targets: Sequence of service names to test (e.g. ("llm", "snowflake")).
            timeout: Timeout per check in seconds (default: 10.0).

        Returns:
            ConnectionHealthReport: Aggregated results for all specified services.

        Raises:
            ValueError: If an unknown target service name is passed.
        """
        results: dict[str, ServiceTestResult] = {}
        for target in targets:
            normalized = target.strip().lower()
            if normalized == "llm":
                results["llm"] = self.test_llm(cfg, timeout=timeout)
            elif normalized == "snowflake":
                results["snowflake"] = self.test_snowflake(cfg, timeout=timeout)
            else:
                msg = f"Unsupported health test target: '{target}'. Must be 'llm' or 'snowflake'."
                raise ValueError(msg)

        return ConnectionHealthReport(results=results)
