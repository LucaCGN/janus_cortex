from __future__ import annotations

try:
    from codex_tools.janus.live_plan import main_for_live_plan
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    import sys
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from codex_tools.janus.live_plan import main_for_live_plan


def main() -> None:
    main_for_live_plan("Build and optionally submit an executable NBA/WNBA Janus live StrategyPlanJSON.")


if __name__ == "__main__":
    main()
