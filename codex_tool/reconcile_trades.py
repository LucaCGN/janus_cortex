try:
    from codex_tools.janus.reconciliation import (
        TRADE_RECONCILIATION_PATH,
        build_trade_reconciliation_query,
        get_trade_reconciliation,
        main_for_trade_reconciliation,
    )
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from pathlib import Path
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from codex_tools.janus.reconciliation import (  # type: ignore[no-redef]
        TRADE_RECONCILIATION_PATH,
        build_trade_reconciliation_query,
        get_trade_reconciliation,
        main_for_trade_reconciliation,
    )


def main() -> None:
    main_for_trade_reconciliation("Build a non-destructive duplicate-fill reconciliation report.")


if __name__ == "__main__":
    main()
