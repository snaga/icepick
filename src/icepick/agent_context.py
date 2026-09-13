"""Machine-readable introspection provider for Icepick.

Provides structured metadata about CLI commands, optimization rules,
environment variables, and credential targets for AI coding agents (Layer 2 Introspection).
"""

from __future__ import annotations

from typing import Any

from icepick import __version__


def get_agent_context() -> dict[str, Any]:
    """Return structured introspection metadata of Icepick CLI for AI agents.

    Returns:
        dict[str, Any]: A complete specification of commands, rules,
            environment variables, and credentials.
    """
    return {
        "name": "icepick",
        "version": __version__,
        "description": "AST Query Optimizer for Snowflake",
        "commands": {
            "check": {
                "description": "Check a SQL file for performance anti-patterns and issues.",
                "arguments": {
                    "file": {
                        "type": "Path",
                        "required": True,
                        "description": "Path to the Snowflake SQL file to check.",
                    }
                },
                "options": {
                    "--dialect": {
                        "flag": "-d",
                        "type": "str",
                        "default": "snowflake",
                        "choices": ["snowflake", "postgres", "duckdb", "bigquery"],
                        "description": "SQL dialect to use for parsing.",
                    },
                    "--config": {
                        "flag": "-c",
                        "type": "Path",
                        "default": None,
                        "description": "Path to configuration file (.json or .toml).",
                    },
                    "--json": {
                        "type": "bool",
                        "default": False,
                        "description": "Output diagnostic issues as structured JSON array to stdout.",
                    },
                },
            },
            "rewrite": {
                "description": "Optimize Snowflake SQL queries and output Unified Diff (read-only).",
                "arguments": {
                    "file": {
                        "type": "Path",
                        "required": True,
                        "description": "Path to the Snowflake SQL file to rewrite.",
                    }
                },
                "options": {
                    "--output": {
                        "flag": "-o",
                        "type": "Path",
                        "default": None,
                        "description": "Save the unified diff output to the specified .patch file.",
                    },
                    "--dialect": {
                        "flag": "-d",
                        "type": "str",
                        "default": "snowflake",
                        "choices": ["snowflake", "postgres", "duckdb", "bigquery"],
                        "description": "SQL dialect to use for parsing.",
                    },
                    "--category": {
                        "type": "str",
                        "default": None,
                        "choices": ["CRITICAL", "HIGH", "MEDIUM", "LOW"],
                        "description": "Severity category filter (CRITICAL, HIGH, MEDIUM, LOW).",
                    },
                    "--agentic": {
                        "type": "bool",
                        "default": False,
                        "description": "Enable LLM-assisted targeted rewriting for complex patterns (e.g. correlated subqueries).",
                    },
                    "--verify-loop": {
                        "type": "bool",
                        "default": False,
                        "description": "Enable closed-loop verification with Snowflake bidirectional EXCEPT; retries LLM rewrite on difference.",
                    },
                    "--max-retries": {
                        "type": "int",
                        "default": 3,
                        "description": "Maximum retry attempts for verify-loop self-correction.",
                    },
                    "--flatten-subqueries": {
                        "type": "bool",
                        "default": False,
                        "description": "Flatten inline derived tables to top-level CTEs.",
                    },
                    "--reformat": {
                        "type": "bool",
                        "default": False,
                        "description": "Reformat entire SQL query AST (pretty=True) instead of source-preserving minimal splicing.",
                    },
                    "--json": {
                        "type": "bool",
                        "default": False,
                        "description": "Output rewrite result as structured JSON to stdout.",
                    },
                    "--config": {
                        "flag": "-c",
                        "type": "Path",
                        "default": None,
                        "description": "Path to configuration file (.json or .toml).",
                    },
                    "--provider": {
                        "flag": "-p",
                        "type": "str",
                        "default": None,
                        "choices": ["gemini", "vertex"],
                        "description": "LLM provider ('gemini' or 'vertex').",
                    },
                    "--model": {
                        "flag": "-m",
                        "type": "str",
                        "default": None,
                        "description": "LLM model identifier (e.g. 'gemini-3.8-flash').",
                    },
                },
            },
            "patch": {
                "description": "Apply a Unified Diff patch to a Snowflake SQL file.",
                "arguments": {
                    "target_file": {
                        "type": "Path",
                        "required": True,
                        "description": "Path to the Snowflake SQL file to patch.",
                    },
                    "patch_file": {
                        "type": "Path",
                        "required": False,
                        "description": "Optional path to .patch file. If omitted, diff is read from stdin.",
                    },
                },
                "options": {
                    "--interactive": {
                        "flag": "-i",
                        "type": "bool",
                        "default": False,
                        "description": "Prompt for interactive confirmation before applying each hunk.",
                    },
                    "--dry-run": {
                        "type": "bool",
                        "default": False,
                        "description": "Simulate patch application without modifying the target file.",
                    },
                    "--force": {
                        "flag": "-f",
                        "type": "bool",
                        "default": False,
                        "description": "Force overwrite files in non-interactive environments.",
                    },
                    "--json": {
                        "type": "bool",
                        "default": False,
                        "description": "Output patch result as structured JSON to stdout.",
                    },
                },
            },
            "verify": {
                "description": "Verify deterministic equivalence between original and optimized queries using EXCEPT.",
                "arguments": {
                    "original_file": {
                        "type": "Path",
                        "required": True,
                        "description": "Path to the original Snowflake SQL file.",
                    },
                    "optimized_file": {
                        "type": "Path",
                        "required": True,
                        "description": "Path to the optimized Snowflake SQL file.",
                    },
                },
                "options": {
                    "--dry-run": {
                        "type": "bool",
                        "default": False,
                        "description": "Output the generated bidirectional EXCEPT verification SQL query without connecting to Snowflake.",
                    },
                    "--dialect": {
                        "flag": "-d",
                        "type": "str",
                        "default": "snowflake",
                        "choices": ["snowflake", "postgres", "duckdb", "bigquery"],
                        "description": "SQL dialect to use.",
                    },
                    "--timeout": {
                        "flag": "-t",
                        "type": "int",
                        "default": None,
                        "description": "Snowflake statement execution timeout in seconds.",
                    },
                    "--config": {
                        "flag": "-c",
                        "type": "Path",
                        "default": None,
                        "description": "Path to configuration file (.json or .toml).",
                    },
                    "--json": {
                        "type": "bool",
                        "default": False,
                        "description": "Output verification metrics as structured JSON to stdout.",
                    },
                },
            },
            "feedback": {
                "description": "Record friction, bug, doc feedback, or idea into a local JSON Lines log.",
                "arguments": {
                    "message": {
                        "type": "str",
                        "required": True,
                        "description": "Feedback message describing friction, bug, documentation issue, or idea.",
                    }
                },
                "options": {
                    "--category": {
                        "flag": "-c",
                        "type": "str",
                        "default": "friction",
                        "choices": ["friction", "bug", "doc", "idea"],
                        "description": "Feedback category ('friction', 'bug', 'doc', 'idea').",
                    },
                    "--log-file": {
                        "type": "Path",
                        "default": None,
                        "description": "Path to feedback JSON Lines log file.",
                    },
                    "--json": {
                        "type": "bool",
                        "default": False,
                        "description": "Output the recorded feedback entry in JSON format to stdout.",
                    },
                },
            },
            "agent-context": {
                "description": "Introspect CLI capabilities, rules, commands, and environment schema as structured JSON.",
                "arguments": {},
                "options": {
                    "--json": {
                        "type": "bool",
                        "default": True,
                        "description": "Output introspected CLI specification in formatted JSON.",
                    }
                },
            },
            "config": {
                "description": "Manage and inspect Icepick configuration.",
                "arguments": {},
                "options": {},
                "subcommands": {
                    "show": {
                        "description": "Display currently resolved configuration settings and their source layers.",
                        "arguments": {},
                        "options": {
                            "--config": {
                                "flag": "-c",
                                "type": "Path",
                                "default": None,
                                "description": "Path to configuration file (.json or .toml).",
                            },
                            "--json": {
                                "type": "bool",
                                "default": False,
                                "description": "Output active configuration as structured JSON.",
                            },
                        },
                    },
                    "test": {
                        "description": "Test connectivity and authentication for LLM (Gemini/Vertex) and Snowflake.",
                        "arguments": {},
                        "options": {
                            "--target": {
                                "flag": "-t",
                                "type": "str",
                                "default": "all",
                                "choices": ["all", "llm", "snowflake"],
                                "description": "Target service to test ('all', 'llm', 'snowflake'). Default: 'all'.",
                            },
                            "--llm": {
                                "type": "bool",
                                "default": False,
                                "description": "Test LLM connection only.",
                            },
                            "--snowflake": {
                                "type": "bool",
                                "default": False,
                                "description": "Test Snowflake connection only.",
                            },
                            "--timeout": {
                                "type": "float",
                                "default": 10.0,
                                "description": "Timeout in seconds for each connection check (default: 10.0).",
                            },
                            "--config": {
                                "flag": "-c",
                                "type": "Path",
                                "default": None,
                                "description": "Path to configuration file.",
                            },
                            "--json": {
                                "type": "bool",
                                "default": False,
                                "description": "Output health report as structured JSON.",
                            },
                        },
                    },
                },
            },
        },
        "rules": [
            {
                "id": "SNOW-001",
                "name": "NonSargableRule",
                "severity": "HIGH",
                "description": "Detects function wraps on columns in WHERE/ON clauses that inhibit partition pruning (e.g., DATE(col) = '...').",
                "can_auto_fix": True,
            },
            {
                "id": "SNOW-002",
                "name": "CorrelatedSubqueryRule",
                "severity": "CRITICAL",
                "description": "Detects correlated subqueries referencing outer query tables that may trigger repetitive table scans and memory spilling.",
                "can_auto_fix": False,
            },
            {
                "id": "SNOW-003",
                "name": "RedundantSortRule",
                "severity": "MEDIUM",
                "description": "Detects redundant ORDER BY clauses inside subqueries or CTEs without LIMIT or window functions.",
                "can_auto_fix": True,
            },
            {
                "id": "SNOW-004",
                "name": "ImplicitCrossJoinRule",
                "severity": "HIGH",
                "description": "Detects comma-separated FROM clauses (implicit cross joins) that risk cartesian products.",
                "can_auto_fix": False,
            },
            {
                "id": "SNOW-005",
                "name": "DuplicateTableScanRule",
                "severity": "MEDIUM",
                "description": "Detects duplicate scans of the same large table across multiple CTEs.",
                "can_auto_fix": False,
            },
            {
                "id": "SNOW-006",
                "name": "UnionToUnionAllRule",
                "severity": "LOW",
                "description": "Detects UNION (implicit DISTINCT) where UNION ALL is sufficient to avoid deduplication sort.",
                "can_auto_fix": True,
            },
            {
                "id": "SNOW-007",
                "name": "NestedSubqueryRule",
                "severity": "MEDIUM",
                "description": "Detects deeply nested inline derived tables in FROM or JOIN clauses that should be extracted to top-level CTEs.",
                "can_auto_fix": True,
            },
        ],
        "environment_variables": {
            "DEBUG_ICEPICK_GEMINI_API_KEY": {
                "description": "Temporary debug/CI override for Google Gemini LLM API key. Never use in production.",
                "credential_target": "icepick:gemini_api_key",
                "required": False,
            },
            "DEBUG_ICEPICK_SNOWFLAKE_PASSWORD": {
                "description": "Temporary debug/CI override for Snowflake connection password.",
                "credential_target": "icepick:snowflake_password",
                "required": False,
            },
            "DEBUG_ICEPICK_SNOWFLAKE_ACCOUNT": {
                "description": "Temporary debug/CI override for Snowflake account identifier.",
                "credential_target": "icepick:snowflake_account",
                "required": False,
            },
            "DEBUG_ICEPICK_SNOWFLAKE_USER": {
                "description": "Temporary debug/CI override for Snowflake user name.",
                "credential_target": "icepick:snowflake_user",
                "required": False,
            },
        },
        "credentials": {
            "icepick:gemini_api_key": {
                "description": "Windows Credential Manager target for Google Gemini LLM API key.",
                "cmdkey_example": "cmdkey /generic:icepick:gemini_api_key /user:icepick /pass:<API_KEY>",
            },
            "icepick:snowflake_password": {
                "description": "Windows Credential Manager target for Snowflake connection password.",
                "cmdkey_example": "cmdkey /generic:icepick:snowflake_password /user:icepick /pass:<PASSWORD>",
            },
            "icepick:snowflake_account": {
                "description": "Windows Credential Manager target for Snowflake account identifier.",
                "cmdkey_example": "cmdkey /generic:icepick:snowflake_account /user:icepick /pass:<ACCOUNT>",
            },
            "icepick:snowflake_user": {
                "description": "Windows Credential Manager target for Snowflake user name.",
                "cmdkey_example": "cmdkey /generic:icepick:snowflake_user /user:icepick /pass:<USER>",
            },
        },
    }
