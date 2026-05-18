from __future__ import annotations

try:
    from codex_tools.janus.strategy import (
        PREGAME_PLAN_PATH,
        build_pregame_research_payload,
        main_for_pregame_research,
        submit_pregame_research,
    )
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from _client import add_cycle_args, api_json, base_parser, cycle_payload, exit_for_response, read_text

    PREGAME_PLAN_PATH = "/v1/ops/pregame-plan"

    def build_pregame_research_payload(args):  # type: ignore[no-untyped-def]
        payload = cycle_payload(args)
        payload["research_path"] = args.research_path
        payload["research_markdown"] = read_text(args.research_path)
        return payload

    def submit_pregame_research(api_root: str, payload: dict):  # type: ignore[type-arg]
        return api_json(api_root, "POST", PREGAME_PLAN_PATH, payload)

    def main_for_pregame_research(description: str) -> None:
        parser = add_cycle_args(base_parser(description))
        parser.add_argument("--research-path", default=None)
        args = parser.parse_args()
        exit_for_response(submit_pregame_research(args.api_root, build_pregame_research_payload(args)))


def main() -> None:
    main_for_pregame_research("Submit Codex pregame research to Janus.")


if __name__ == "__main__":
    main()
