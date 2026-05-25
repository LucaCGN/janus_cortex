from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.modules.agentic.pregame_priors import (
    PregameResearchPrior,
    build_optional_pregame_prior_evidence,
    write_pregame_prior_artifacts_from_research_bundle,
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


def test_research_bundle_adoption_writes_per_event_prior_artifacts_pytest(tmp_path: Path) -> None:
    bundle_path = tmp_path / "wnba_bundle.json"
    bundle_path.write_text(
        json.dumps(
            {
                "schema_version": "wnba_optional_pregame_prior_v1",
                "automation_id": "wnba-pregame-research",
                "generated_at_utc": "2026-05-25T15:00:00Z",
                "source_caveats": ["official_injury_pdf_used"],
                "events": [
                    {
                        "event_slug": "wnba-por-nyl-2026-05-25",
                        "teams": {
                            "away": {"name": "Portland Fire"},
                            "home": {"name": "New York Liberty"},
                        },
                        "prior_status": "provisional_optional_prior_injury_incomplete",
                        "likely_regimes": ["favorite_no_chase", "underdog_monitor"],
                        "risk_flags": ["official_new_york_availability_not_yet_submitted"],
                        "candidate_signal_config": {
                            "runtime_mutation_allowed": False,
                            "monitor_only": ["nyl_official_availability_refresh"],
                        },
                        "freshness": {"hard_expire_utc": "2026-05-26T00:05:00Z"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = write_pregame_prior_artifacts_from_research_bundle(bundle_path, root=tmp_path)

    assert result["status"] == "stored"
    assert result["prior_count"] == 1
    current_path = tmp_path / "pregame-priors" / "2026-05-25" / "wnba-por-nyl-2026-05-25" / "current.json"
    payload = json.loads(current_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "pregame_research_prior_v1"
    assert payload["event_id"] == "wnba-por-nyl-2026-05-25"
    assert payload["league"] == "wnba"
    assert payload["expires_at_utc"] == "2026-05-26T00:05:00Z"
    assert payload["teams"] == ["Portland Fire", "New York Liberty"]
    assert payload["likely_regimes"] == ["favorite_no_chase", "underdog_monitor"]
    assert payload["risk_flags"] == ["official_new_york_availability_not_yet_submitted"]
    assert payload["proposed_signal_config_changes"][0]["source"] == "candidate_signal_config"
    assert "official_injury_pdf_used" in payload["source_caveats"]


def test_research_bundle_adoption_normalizes_nba_regime_dicts_pytest(tmp_path: Path) -> None:
    bundle_path = tmp_path / "nba_bundle.json"
    bundle_path.write_text(
        json.dumps(
            {
                "schema_version": "janus_optional_pregame_prior_v1",
                "automation": "nba-pregame-research",
                "generated_at_utc": "2026-05-25T15:00:00Z",
                "session_date": "2026-05-25",
                "events": [
                    {
                        "event_id": "nba-nyk-cle-2026-05-25",
                        "league": "NBA",
                        "teams": {"away": "New York Knicks", "home": "Cleveland Cavaliers"},
                        "likely_regimes": [
                            {
                                "name": "knicks_closeout_control",
                                "fit": "baseline",
                                "description": "Knicks modest road favorite.",
                            }
                        ],
                        "candidate_signal_config_changes": [
                            {"path": "event-control-or-strategyplan", "recommendation": "monitor only"}
                        ],
                        "freshness": {
                            "hard_expire_after_utc": "2026-05-26T01:00:00Z",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = write_pregame_prior_artifacts_from_research_bundle(bundle_path, root=tmp_path)

    assert result["status"] == "stored"
    current_path = tmp_path / "pregame-priors" / "2026-05-25" / "nba-nyk-cle-2026-05-25" / "current.json"
    payload = json.loads(current_path.read_text(encoding="utf-8"))
    assert payload["league"] == "nba"
    assert payload["likely_regimes"] == [
        "knicks_closeout_control | baseline | Knicks modest road favorite."
    ]
    assert payload["proposed_signal_config_changes"] == [
        {"path": "event-control-or-strategyplan", "recommendation": "monitor only"}
    ]
