"""Equivalence verification module for Snowflake queries."""

from icepick.verifier.equivalence import (
    EquivalenceVerifier,
    VerificationResult,
    generate_verification_sql,
)

__all__ = [
    "EquivalenceVerifier",
    "VerificationResult",
    "generate_verification_sql",
]
