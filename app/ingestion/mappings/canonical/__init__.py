"""Canonical mapping orchestration wrappers."""

from app.data.pipelines.canonical.mapping_service import (
    CanonicalMappingResult,
    build_canonical_mapping_result,
    build_canonical_mapping_result_from_payloads,
)

__all__ = [
    "CanonicalMappingResult",
    "build_canonical_mapping_result",
    "build_canonical_mapping_result_from_payloads",
]

