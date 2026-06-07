from __future__ import annotations

from crypto_options_app.pipelines.options.market_data_store import default_market_data_store_path
from crypto_options_app.pipelines.options.profile_store import default_profile_store_path
from crypto_options_app.scripts import run_crypto_options_market_data, run_crypto_options_profile_store


def test_profile_store_cli_defaults_to_legacy_profile_store_path() -> None:
    args = run_crypto_options_profile_store.build_parser().parse_args(["summary"])

    assert args.db_path == str(default_profile_store_path())
    assert args.db_path != "crypto_options_app\\data\\crypto_options_data.sqlite"
    assert args.db_path != "crypto_options_app/data/crypto_options_data.sqlite"


def test_market_data_cli_defaults_to_legacy_market_store_path() -> None:
    args = run_crypto_options_market_data.build_parser().parse_args(["summary"])

    assert args.db_path == str(default_market_data_store_path())
    assert args.db_path != "crypto_options_app\\data\\crypto_options_data.sqlite"
    assert args.db_path != "crypto_options_app/data/crypto_options_data.sqlite"
