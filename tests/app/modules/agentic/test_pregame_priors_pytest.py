from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.modules.agentic.pregame_priors import (
    PregameResearchPrior,
    build_optional_pregame_prior_evidence,
    write_pregame_prior_artifact,
)


def _write_prior(root: Path, *, event_id: str, generated_at: str, expires_at: str) -> Path:
    path = root / "pregame-priors" / "2026-05-25" / event_id / "current.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": "pregame_research_prior_v1",
                "event_id": event_id,
                "league": "wnba",
                "generated_at_utc": generated_at,
                "expires_at_utc": expires_at,
                "teams": ["Atlanta Dream", "Phoenix Mercury"],
                "likely_regimes": ["low_band_rebound"],
                "risk_flags": ["sample_size_caveat"],
                "source_caveats": ["wnba_feed_adapter_caveat"],
                "proposed_signal_config_changes": [{"source": "deterministic", "action": "review_only"}],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_missing_pregame_prior_is_optional_not_liveness_blocking_pytest(tmp_path: Path) -> None:
    evidence = build_optional_pregame_prior_evidence(
        event_id="wnba-phx-atl-2026-05-25",
        league="wnba",
        day="2026-05-25",
        root=tmp_path,
        now=datetime(2026, 5, 25, 18, 0, tzinfo=timezone.utc),
    )

    assert evidence.status == "missing"
    assert evidence.reason_codes == ["optional_prior_missing"]
    assert evidence.liveness_blocking is False
    assert evidence.live_disabled is False
    assert evidence.event_control_mutation_allowed is False


def test_current_pregame_prior_preserves_structured_research_fields_pytest(tmp_path: Path) -> None:
    path = _write_prior(
        tmp_path,
        event_id="wnba-phx-atl-2026-05-25",
        generated_at="2026-05-25T15:00:00Z",
        expires_at="2026-05-25T23:00:00Z",
    )

    evidence = build_optional_pregame_prior_evidence(
        event_id="wnba-phx-atl-2026-05-25",
        league="wnba",
        day="2026-05-25",
        root=tmp_path,
        now=datetime(2026, 5, 25, 18, 0, tzinfo=timezone.utc),
    )

    assert evidence.status == "current"
    assert evidence.prior_path == str(path)
    assert evidence.prior_schema_version == "pregame_research_prior_v1"
    assert evidence.reason_codes == ["optional_prior_current"]
    assert evidence.teams == ["Atlanta Dream", "Phoenix Mercury"]
    assert evidence.likely_regimes == ["low_band_rebound"]
    assert evidence.risk_flags == ["sample_size_caveat"]
    assert evidence.proposed_signal_config_changes == [{"source": "deterministic", "action": "review_only"}]


def test_pregame_prior_writer_creates_versioned_and_current_artifacts_pytest(tmp_path: Path) -> None:
    generated_at = datetime(2026, 5, 25, 15, 0, tzinfo=timezone.utc)
    prior = PregameResearchPrior(
        event_id="nba-ind-nyk-2026-05-25",
        league="nba",
        session_date="2026-05-25",
        generated_at_utc=generated_at,
        expires_at_utc=generated_at + timedelta(hours=8),
        source="nba-pregame-research",
        teams=["Indiana Pacers", "New York Knicks"],
        likely_regimes=["pace_watch"],
        risk_flags=["injury_report_pending"],
    )

    result = write_pregame_prior_artifact(prior, root=tmp_path)
    current_path = Path(result["current_path"])
    version_path = Path(result["version_path"])

    assert result["status"] == "stored"
    assert current_path.exists()
    assert version_path.exists()
    payload = json.loads(current_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "pregame_research_prior_v1"
    assert payload["source"] == "nba-pregame-research"
    assert payload["risk_flags"] == ["injury_report_pending"]


def test_expired_pregame_prior_is_stale_but_not_live_disabled_pytest(tmp_path: Path) -> None:
    _write_prior(
        tmp_path,
        event_id="wnba-phx-atl-2026-05-25",
        generated_at="2026-05-25T10:00:00Z",
        expires_at="2026-05-25T12:00:00Z",
    )

    evidence = build_optional_pregame_prior_evidence(
        event_id="wnba-phx-atl-2026-05-25",
        league="wnba",
        day="2026-05-25",
        root=tmp_path,
        now=datetime(2026, 5, 25, 18, 0, tzinfo=timezone.utc),
    )

    assert evidence.status == "stale"
    assert "optional_prior_expired" in evidence.reason_codes
    assert evidence.liveness_blocking is False
    assert evidence.live_disabled is False
