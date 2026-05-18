try:
    from codex_tools.janus.reconciliation import (
        OPERATOR_INTERVENTION_RECONCILE_PATH,
        build_order_reconciliation_payload,
        main_for_order_reconciliation,
        reconcile_operator_interventions,
    )
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from pathlib import Path
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from codex_tools.janus.reconciliation import (  # type: ignore[no-redef]
        OPERATOR_INTERVENTION_RECONCILE_PATH,
        build_order_reconciliation_payload,
        main_for_order_reconciliation,
        reconcile_operator_interventions,
    )


def main() -> None:
    main_for_order_reconciliation("Ask Janus to reconcile operator/manual order interventions.")


if __name__ == "__main__":
    main()
