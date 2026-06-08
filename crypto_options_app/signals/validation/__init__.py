"""Signal validation contracts and registry for strategy-family research."""

from crypto_options_app.signals.validation.models import (
    SignalObservation,
    SignalCandidateSpec,
    SignalQueueItem,
    SignalValidationFrame,
    SignalValidationPhaseResult,
    SignalValidationRunResult,
    SignalValidationSummary,
    build_signal_id,
)
from crypto_options_app.signals.validation.registry import (
    FIRST_BATCH_SIGNAL_SPECS,
    get_signal_spec,
    list_signal_specs,
    signal_catalog_summary,
)

__all__ = [
    "FIRST_BATCH_SIGNAL_SPECS",
    "SignalCandidateSpec",
    "SignalObservation",
    "SignalQueueItem",
    "SignalValidationFrame",
    "SignalValidationPhaseResult",
    "SignalValidationRunResult",
    "SignalValidationSummary",
    "build_signal_id",
    "get_signal_spec",
    "list_signal_specs",
    "signal_catalog_summary",
]
