from __future__ import annotations

try:
    from codex_tools.janus.live_activation import main_for_live_activation_preflight
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    import sys
    from pathlib import Path

    _REPO_ROOT = Path(__file__).resolve().parents[1]
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    from codex_tools.janus.live_activation import main_for_live_activation_preflight


if __name__ == "__main__":
    main_for_live_activation_preflight("Validate Janus live activation toggles before a covered game or portfolio-manager live pass.")
