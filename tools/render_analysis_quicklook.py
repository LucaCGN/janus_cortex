from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path
from typing import Iterable

import pandas as pd
from PIL import Image, ImageColor, ImageDraw, ImageFont


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.data.pipelines.daily.nba.analysis.contracts import (  # noqa: E402
    ANALYSIS_VERSION,
    DEFAULT_OUTPUT_ROOT,
    DEFAULT_SEASON,
    DEFAULT_SEASON_PHASE,
)


CANVAS_WIDTH = 1600
CANVAS_HEIGHT = 900
LEFT_MARGIN = 120
RIGHT_MARGIN = 60
TOP_MARGIN = 120
BOTTOM_MARGIN = 120
BACKGROUND = "#ffffff"
GRID = "#d8dbe2"
TEXT = "#111827"
SUBTLE = "#6b7280"
PRIMARY = "#2563eb"
PRIMARY_FILL = "#93c5fd"
POSITIVE = "#16a34a"
NEGATIVE = "#dc2626"
MASTER = "#f59e0b"
MASTER_FILL = "#fde68a"
ROUTER = "#7c3aed"
ROUTER_FILL = "#ddd6fe"
BLOCK = "#0f766e"
BLOCK_FILL = "#ccfbf1"
NEUTRAL_FILL = "#e5e7eb"
SOFT_GOOD = "#dcfce7"
SOFT_BAD = "#fee2e2"
SOFT_INFO = "#dbeafe"


def _load_font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    candidates = []
    if bold:
        candidates.extend(["arialbd.ttf", "segoeuib.ttf"])
    candidates.extend(["arial.ttf", "segoeui.ttf"])
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


TITLE_FONT = _load_font(34, bold=True)
SUBTITLE_FONT = _load_font(20)
LABEL_FONT = _load_font(18)
SMALL_FONT = _load_font(16)
BOLD_LABEL_FONT = _load_font(18, bold=True)


def _format_currency(value: float | int | None) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"${float(value):,.2f}"


def _format_compact_currency(value: float | int | None) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    number = float(value)
    magnitude = abs(number)
    if magnitude >= 1_000_000:
        return f"${number / 1_000_000:,.1f}M"
    if magnitude >= 1_000:
        return f"${number / 1_000:,.1f}K"
    return f"${number:,.0f}"


def _band_sort_key(value: str) -> tuple[int, str]:
    raw = str(value or "")
    head = raw.split("-", 1)[0]
    try:
        return (int(head), raw)
    except ValueError:
        return (9999, raw)


def _ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Missing artifact: {path}")
    return pd.read_csv(path)


def _create_canvas() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", (CANVAS_WIDTH, CANVAS_HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(image)
    return image, draw


def _draw_title(draw: ImageDraw.ImageDraw, title: str, subtitle: str) -> None:
    draw.text((LEFT_MARGIN, 34), title, fill=TEXT, font=TITLE_FONT)
    draw.text((LEFT_MARGIN, 78), subtitle, fill=SUBTLE, font=SUBTITLE_FONT)


def _chart_bounds() -> tuple[int, int, int, int]:
    left = LEFT_MARGIN
    top = TOP_MARGIN
    right = CANVAS_WIDTH - RIGHT_MARGIN
    bottom = CANVAS_HEIGHT - BOTTOM_MARGIN
    return left, top, right, bottom


def _draw_axes(draw: ImageDraw.ImageDraw) -> tuple[int, int, int, int]:
    left, top, right, bottom = _chart_bounds()
    draw.line((left, top, left, bottom), fill=GRID, width=2)
    draw.line((left, bottom, right, bottom), fill=GRID, width=2)
    return left, top, right, bottom


def _value_ticks(values: Iterable[float], *, log_scale: bool) -> list[float]:
    clean = [float(value) for value in values if value is not None and not pd.isna(value)]
    if not clean:
        return [0.0, 1.0]
    if log_scale:
        low = min(max(value, 1e-6) for value in clean)
        high = max(clean)
        low_power = math.floor(math.log10(low))
        high_power = math.ceil(math.log10(high))
        return [10 ** power for power in range(low_power, high_power + 1)]
    low = min(clean)
    high = max(clean)
    if math.isclose(low, high):
        return [low, high + 1.0]
    tick_count = 5
    step = (high - low) / tick_count
    return [low + (step * index) for index in range(tick_count + 1)]


def _map_y(value: float, *, min_value: float, max_value: float, top: int, bottom: int, log_scale: bool) -> float:
    if log_scale:
        clipped = max(value, 1e-6)
        min_log = math.log10(max(min_value, 1e-6))
        max_log = math.log10(max(max_value, 1e-6))
        if math.isclose(min_log, max_log):
            return float(bottom)
        ratio = (math.log10(clipped) - min_log) / (max_log - min_log)
    else:
        if math.isclose(min_value, max_value):
            return float(bottom)
        ratio = (value - min_value) / (max_value - min_value)
    return bottom - (ratio * (bottom - top))


def _save(image: Image.Image, path: Path) -> Path:
    image.save(path, format="PNG")
    return path


def _mix_colors(start: str, end: str, ratio: float) -> tuple[int, int, int]:
    ratio = min(max(ratio, 0.0), 1.0)
    start_rgb = ImageColor.getrgb(start)
    end_rgb = ImageColor.getrgb(end)
    return tuple(
        int(start_rgb[index] + ((end_rgb[index] - start_rgb[index]) * ratio))
        for index in range(3)
    )


def _safe_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def _strategy_label(strategy_family: str, portfolio_scope: str | None = None) -> str:
    short_names = {
        "master_strategy_router_v1": "master_router",
        "statistical_routing_v1": "stat_routing",
        "combined_keep_families": "combined_keep",
    }
    label = short_names.get(strategy_family, strategy_family)
    if portfolio_scope and portfolio_scope not in {"single_family"}:
        label = f"{label}\n{portfolio_scope}"
    return label


def _family_priority(strategy_family: str) -> int:
    priority = {
        "master_strategy_router_v1": 0,
        "statistical_routing_v1": 1,
        "combined_keep_families": 2,
        "winner_definition": 3,
        "inversion": 4,
        "underdog_liftoff": 5,
        "favorite_panic_fade_v1": 6,
        "q4_clutch": 7,
        "q1_repricing": 8,
        "halftime_q3_repricing_v1": 9,
        "comeback_reversion_v2": 10,
        "comeback_reversion": 11,
        "volatility_scalp": 12,
        "reversion": 13,
    }
    return priority.get(strategy_family, 99)


def render_strategy_daily_paths(render_dir: Path, daily_paths: pd.DataFrame, *, sample_name: str) -> list[Path]:
    output_paths: list[Path] = []
    full_sample = daily_paths[daily_paths["sample_name"] == sample_name].copy()
    if full_sample.empty:
        return output_paths

    full_sample["path_day_index"] = pd.to_numeric(full_sample["path_day_index"], errors="coerce")
    full_sample["ending_bankroll"] = pd.to_numeric(full_sample["ending_bankroll"], errors="coerce")
    for strategy_family, frame in full_sample.groupby("strategy_family", sort=True):
        work = frame.sort_values("path_day_index", kind="mergesort").reset_index(drop=True)
        values = [float(value) for value in work["ending_bankroll"].tolist()]
        if not values:
            continue
        log_scale = min(values) > 0 and (max(values) / max(min(values), 1e-6)) >= 50
        image, draw = _create_canvas()
        scale_note = "log scale" if log_scale else "linear scale"
        subtitle = (
            f"{sample_name} daily bankroll path | start {_format_currency(values[0])} | "
            f"end {_format_currency(values[-1])} | {scale_note}"
        )
        _draw_title(draw, f"{strategy_family} daily bankroll path", subtitle)
        left, top, right, bottom = _draw_axes(draw)

        min_value = min(max(value, 1e-6) for value in values) if log_scale else min(values)
        max_value = max(values)
        ticks = _value_ticks(values, log_scale=log_scale)
        for tick in ticks:
            y = _map_y(tick, min_value=min_value, max_value=max_value, top=top, bottom=bottom, log_scale=log_scale)
            draw.line((left, y, right, y), fill=GRID, width=1)
            draw.text((20, y - 10), _format_compact_currency(tick), fill=SUBTLE, font=SMALL_FONT)

        if len(values) == 1:
            x_positions = [left + ((right - left) / 2.0)]
        else:
            x_positions = [
                left + ((right - left) * (index / (len(values) - 1)))
                for index in range(len(values))
            ]
        points = [
            (
                x_positions[index],
                _map_y(value, min_value=min_value, max_value=max_value, top=top, bottom=bottom, log_scale=log_scale),
            )
            for index, value in enumerate(values)
        ]
        for index in range(len(points) - 1):
            draw.line((points[index], points[index + 1]), fill=PRIMARY, width=4)
        for point in points:
            radius = 4
            draw.ellipse((point[0] - radius, point[1] - radius, point[0] + radius, point[1] + radius), fill=PRIMARY)

        first_date = str(work.iloc[0]["path_date"])
        mid_date = str(work.iloc[len(work) // 2]["path_date"])
        last_date = str(work.iloc[-1]["path_date"])
        draw.text((left, bottom + 24), first_date, fill=SUBTLE, font=LABEL_FONT)
        mid_width = draw.textlength(mid_date, font=LABEL_FONT)
        draw.text((((left + right) / 2) - (mid_width / 2), bottom + 24), mid_date, fill=SUBTLE, font=LABEL_FONT)
        last_width = draw.textlength(last_date, font=LABEL_FONT)
        draw.text((right - last_width, bottom + 24), last_date, fill=SUBTLE, font=LABEL_FONT)

        output_paths.append(_save(image, render_dir / f"{strategy_family}_daily_path.png"))
    return output_paths


def render_strategy_metric_rankings(render_dir: Path, portfolio_summary: pd.DataFrame, *, sample_name: str) -> Path | None:
    work = portfolio_summary[portfolio_summary["sample_name"] == sample_name].copy()
    if work.empty:
        return None
    work = work.dropna(subset=["strategy_family"]).copy()
    work = work.sort_values(
        ["strategy_family", "portfolio_scope", "ending_bankroll"],
        kind="mergesort",
        ascending=[True, True, False],
    ).drop_duplicates(subset=["strategy_family", "portfolio_scope"], keep="first")
    if work.empty:
        return None

    metric_defs = [
        ("ending_bankroll", "Ending bankroll", True),
        ("compounded_return", "Compounded return", True),
        ("max_drawdown_pct", "Drawdown", False),
        ("avg_executed_trade_return_with_slippage", "Avg trade return", True),
        ("executed_trade_count", "Trade count", True),
    ]
    for metric, _, _ in metric_defs:
        work[metric] = _safe_numeric(work[metric])
        work[f"{metric}_rank"] = work[metric].rank(method="min", ascending=metric == "max_drawdown_pct")
    rank_columns = [f"{metric}_rank" for metric, _, _ in metric_defs]
    work["composite_rank"] = work[rank_columns].mean(axis=1)
    work = work.sort_values(["composite_rank", "ending_bankroll"], ascending=[True, False], kind="mergesort").reset_index(drop=True)

    label_width = 380
    metric_width = 170
    row_height = 48
    top_padding = 170
    bottom_padding = 80
    image_width = max(CANVAS_WIDTH, LEFT_MARGIN + label_width + (metric_width * (len(metric_defs) + 1)) + RIGHT_MARGIN)
    image_height = max(CANVAS_HEIGHT, top_padding + (row_height * len(work)) + bottom_padding)
    image = Image.new("RGB", (image_width, image_height), BACKGROUND)
    draw = ImageDraw.Draw(image)
    draw.text((LEFT_MARGIN, 34), "Multi-metric strategy ranking", fill=TEXT, font=TITLE_FONT)
    draw.text(
        (LEFT_MARGIN, 78),
        f"{sample_name} | ranks are best-to-worst within the current benchmark set | master router highlighted",
        fill=SUBTLE,
        font=SUBTITLE_FONT,
    )

    origin_x = LEFT_MARGIN
    origin_y = top_padding
    headers = [metric_name for _, metric_name, _ in metric_defs] + ["Composite"]
    draw.text((origin_x, origin_y - 34), "Strategy", fill=TEXT, font=LABEL_FONT)
    for index, header in enumerate(headers):
        x = origin_x + label_width + (index * metric_width)
        draw.text((x + 10, origin_y - 34), header, fill=TEXT, font=LABEL_FONT)

    total_rows = max(len(work) - 1, 1)
    for row_index, row in enumerate(work.to_dict(orient="records")):
        y = origin_y + (row_index * row_height)
        family = str(row["strategy_family"])
        portfolio_scope = str(row.get("portfolio_scope") or "")
        label = _strategy_label(family, portfolio_scope)
        ending_bankroll = row.get("ending_bankroll")
        drawdown = row.get("max_drawdown_pct")
        subtitle = f"{_format_compact_currency(ending_bankroll)} | DD {drawdown * 100:.1f}%" if pd.notna(drawdown) else _format_compact_currency(ending_bankroll)
        family_font = BOLD_LABEL_FONT if family in {"master_strategy_router_v1", "statistical_routing_v1", "combined_keep_families"} else LABEL_FONT
        draw.rectangle(
            (origin_x - 8, y - 2, image_width - RIGHT_MARGIN, y + row_height - 8),
            outline=GRID if family not in {"master_strategy_router_v1", "statistical_routing_v1"} else MASTER,
            width=2 if family in {"master_strategy_router_v1", "statistical_routing_v1"} else 1,
        )
        draw.text((origin_x, y + 2), label, fill=TEXT, font=family_font)
        draw.text((origin_x, y + 24), subtitle, fill=SUBTLE, font=SMALL_FONT)

        for metric_index, (metric, _, higher_better) in enumerate(metric_defs):
            rank_value = float(row[f"{metric}_rank"])
            ratio = 0.0 if len(work) == 1 else (rank_value - 1.0) / float(len(work) - 1)
            fill = _mix_colors(SOFT_GOOD, SOFT_BAD, ratio)
            if not higher_better:
                fill = _mix_colors(SOFT_BAD, SOFT_GOOD, ratio)
            x = origin_x + label_width + (metric_index * metric_width)
            draw.rectangle((x, y, x + metric_width - 12, y + row_height - 10), fill=fill, outline=GRID, width=1)
            value = int(round(rank_value))
            value_width = draw.textlength(str(value), font=LABEL_FONT)
            draw.text((x + ((metric_width - 12 - value_width) / 2), y + 12), str(value), fill=TEXT, font=LABEL_FONT)

        composite_x = origin_x + label_width + (len(metric_defs) * metric_width)
        composite_fill = MASTER_FILL if family == "master_strategy_router_v1" else ROUTER_FILL if family == "statistical_routing_v1" else BLOCK_FILL if family in {"winner_definition", "inversion", "underdog_liftoff", "favorite_panic_fade_v1"} else NEUTRAL_FILL
        draw.rectangle((composite_x, y, composite_x + metric_width - 12, y + row_height - 10), fill=composite_fill, outline=GRID, width=1)
        composite_value = float(row["composite_rank"])
        composite_text = f"{composite_value:.1f}"
        composite_width = draw.textlength(composite_text, font=LABEL_FONT)
        draw.text((composite_x + ((metric_width - 12 - composite_width) / 2), y + 12), composite_text, fill=TEXT, font=LABEL_FONT)

    path = render_dir / "strategy_multi_metric_rankings.png"
    return _save(image, path)


def render_master_router_position_scatter(render_dir: Path, portfolio_summary: pd.DataFrame, *, sample_name: str) -> Path | None:
    work = portfolio_summary[portfolio_summary["sample_name"] == sample_name].copy()
    if work.empty:
        return None
    work = work.dropna(subset=["strategy_family", "ending_bankroll", "max_drawdown_pct"]).copy()
    if work.empty:
        return None
    work["ending_bankroll"] = _safe_numeric(work["ending_bankroll"])
    work["max_drawdown_pct"] = _safe_numeric(work["max_drawdown_pct"])
    work["executed_trade_count"] = _safe_numeric(work.get("executed_trade_count", pd.Series(dtype=float)))
    work = work.sort_values(["portfolio_scope", "strategy_family"], kind="mergesort").drop_duplicates(subset=["strategy_family", "portfolio_scope"], keep="first")

    x_values = [float(value) for value in work["ending_bankroll"].tolist() if value and value > 0]
    y_values = [float(value) for value in work["max_drawdown_pct"].tolist() if pd.notna(value)]
    if not x_values or not y_values:
        return None

    left, top, right, bottom = _chart_bounds()
    image = Image.new("RGB", (CANVAS_WIDTH, CANVAS_HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(image)
    draw.text((LEFT_MARGIN, 34), "Master router vs. individual strategies", fill=TEXT, font=TITLE_FONT)
    draw.text(
        (LEFT_MARGIN, 78),
        f"{sample_name} | x = ending bankroll (log), y = max drawdown | router and master highlighted against building blocks",
        fill=SUBTLE,
        font=SUBTITLE_FONT,
    )

    x_ticks = _value_ticks(x_values, log_scale=True)
    y_ticks = _value_ticks(y_values, log_scale=False)
    min_x = min(x_values)
    max_x = max(x_values)
    min_y = 0.0
    max_y = max(1.0, max(y_values))

    def map_x(value: float) -> float:
        clipped = max(value, 1e-6)
        min_log = math.log10(max(min_x, 1e-6))
        max_log = math.log10(max(max_x, 1e-6))
        if math.isclose(min_log, max_log):
            return float(left)
        ratio = (math.log10(clipped) - min_log) / (max_log - min_log)
        return left + (ratio * (right - left))

    for tick in x_ticks:
        x = map_x(tick)
        draw.line((x, top, x, bottom), fill=GRID, width=1)
        tick_label = _format_compact_currency(tick)
        label_width = draw.textlength(tick_label, font=SMALL_FONT)
        draw.text((x - (label_width / 2), bottom + 18), tick_label, fill=SUBTLE, font=SMALL_FONT)

    for tick in y_ticks:
        y = _map_y(tick, min_value=min_y, max_value=max_y, top=top, bottom=bottom, log_scale=False)
        draw.line((left, y, right, y), fill=GRID, width=1)
        draw.text((20, y - 10), f"{tick * 100:.0f}%", fill=SUBTLE, font=SMALL_FONT)

    legend_x = right - 390
    legend_y = top - 48
    legend_items = [
        ("master router", MASTER, MASTER_FILL),
        ("stat routing", ROUTER, ROUTER_FILL),
        ("building blocks", BLOCK, BLOCK_FILL),
        ("other strategies", PRIMARY, PRIMARY_FILL),
    ]
    for index, (label, outline, fill) in enumerate(legend_items):
        x = legend_x + (index * 95)
        draw.rectangle((x, legend_y, x + 14, legend_y + 14), fill=fill, outline=outline, width=2)
        draw.text((x + 20, legend_y - 1), label, fill=TEXT, font=SMALL_FONT)

    prominent_families = {
        "master_strategy_router_v1",
        "statistical_routing_v1",
        "combined_keep_families",
        "winner_definition",
        "inversion",
        "underdog_liftoff",
        "favorite_panic_fade_v1",
        "q1_repricing",
        "q4_clutch",
        "halftime_q3_repricing_v1",
    }
    short_names = {
        "master_strategy_router_v1": "master_router",
        "statistical_routing_v1": "stat_routing",
        "combined_keep_families": "combined_keep",
        "favorite_panic_fade_v1": "favorite_panic",
        "halftime_q3_repricing_v1": "halftime_q3",
        "comeback_reversion_v2": "comeback_v2",
    }
    for row in work.to_dict(orient="records"):
        family = str(row["strategy_family"])
        scope = str(row.get("portfolio_scope") or "")
        ending_bankroll = float(row["ending_bankroll"])
        drawdown = float(row["max_drawdown_pct"])
        x = map_x(ending_bankroll)
        y = _map_y(drawdown, min_value=min_y, max_value=max_y, top=top, bottom=bottom, log_scale=False)
        if family == "master_strategy_router_v1":
            fill, outline, radius = MASTER_FILL, MASTER, 11
        elif family == "statistical_routing_v1":
            fill, outline, radius = ROUTER_FILL, ROUTER, 10
        elif family == "combined_keep_families":
            fill, outline, radius = NEUTRAL_FILL, "#6b7280", 10
        elif family in prominent_families:
            fill, outline, radius = BLOCK_FILL, BLOCK, 9
        else:
            fill, outline, radius = PRIMARY_FILL, PRIMARY, 8
        if family in {"master_strategy_router_v1", "statistical_routing_v1"}:
            draw.ellipse((x - radius - 2, y - radius - 2, x + radius + 2, y + radius + 2), outline=outline, width=3)
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=fill, outline=outline, width=2)
        label = _strategy_label(short_names.get(family, family), scope)
        label_x = x + 12 if x < right - 240 else x - 220
        label_y = y - 10
        draw.text((label_x, label_y), label, fill=TEXT, font=SMALL_FONT)
        if family in {"master_strategy_router_v1", "statistical_routing_v1", "combined_keep_families"}:
            detail = f"{_format_compact_currency(ending_bankroll)} | DD {drawdown * 100:.1f}%"
            draw.text((label_x, label_y + 14), detail, fill=SUBTLE, font=SMALL_FONT)

    return _save(image, render_dir / "master_router_vs_individual_strategies.png")


def render_master_router_band_map(render_dir: Path, master_router_decisions: pd.DataFrame, route_summary: pd.DataFrame, *, sample_name: str) -> Path | None:
    work = master_router_decisions[master_router_decisions["sample_name"] == sample_name].copy()
    if work.empty:
        return None
    work["opening_band"] = work["opening_band"].astype(str)
    work["selected_core_family"] = work["selected_core_family"].astype(str)
    family_order = ["underdog_liftoff", "inversion", "winner_definition", "favorite_panic_fade_v1"]
    family_columns = [family for family in family_order if family in set(work["selected_core_family"].tolist())]
    extras = sorted(
        [family for family in work["selected_core_family"].unique().tolist() if family not in family_columns],
        key=_family_priority,
    )
    family_columns.extend(extras)
    if not family_columns:
        return None

    pivot = work.groupby(["opening_band", "selected_core_family"], dropna=False).size().unstack(fill_value=0)
    pivot = pivot.reindex(sorted(pivot.index.tolist(), key=_band_sort_key))
    pivot = pivot.reindex(columns=family_columns, fill_value=0)
    if pivot.empty:
        return None

    route_work = route_summary[route_summary["selection_sample_name"] == "time_train"].copy()
    route_work["opening_band"] = route_work["opening_band"].astype(str)
    route_lookup = {str(row["opening_band"]): row for row in route_work.to_dict(orient="records")}

    cell_width = 150
    cell_height = 54
    label_width = 190
    info_width = 360
    image_width = max(CANVAS_WIDTH, LEFT_MARGIN + label_width + (cell_width * len(family_columns)) + info_width + RIGHT_MARGIN)
    image_height = max(CANVAS_HEIGHT, 170 + (cell_height * len(pivot.index)) + 120)
    image = Image.new("RGB", (image_width, image_height), BACKGROUND)
    draw = ImageDraw.Draw(image)
    draw.text((LEFT_MARGIN, 34), "Master router band map", fill=TEXT, font=TITLE_FONT)
    draw.text(
        (LEFT_MARGIN, 78),
        f"{sample_name} | heatmap = selected core-family counts by opening band | right column = best family from routing benchmark",
        fill=SUBTLE,
        font=SUBTITLE_FONT,
    )

    origin_x = LEFT_MARGIN + label_width
    origin_y = 170
    max_count = int(pivot.to_numpy().max()) if not pivot.empty else 1
    draw.text((LEFT_MARGIN, origin_y - 34), "Opening band", fill=TEXT, font=LABEL_FONT)
    for column_index, family in enumerate(family_columns):
        x = origin_x + (column_index * cell_width)
        draw.text((x + 10, origin_y - 34), family, fill=TEXT, font=SMALL_FONT)
    info_x = origin_x + (len(family_columns) * cell_width) + 24
    draw.text((info_x, origin_y - 34), "Routing benchmark", fill=TEXT, font=LABEL_FONT)

    for row_index, opening_band in enumerate(pivot.index.tolist()):
        y = origin_y + (row_index * cell_height)
        draw.text((LEFT_MARGIN, y + 14), str(opening_band), fill=TEXT, font=LABEL_FONT)
        for column_index, family in enumerate(family_columns):
            value = int(pivot.loc[opening_band, family])
            x = origin_x + (column_index * cell_width)
            intensity = 0 if max_count <= 0 else int(240 - (180 * (value / max_count)))
            fill = (intensity, intensity, 255)
            outline = MASTER if family == "winner_definition" and value == max_count else GRID
            draw.rectangle((x, y, x + cell_width - 8, y + cell_height - 8), fill=fill, outline=outline, width=1)
            label = str(value)
            label_width = draw.textlength(label, font=LABEL_FONT)
            draw.text((x + ((cell_width - 8 - label_width) / 2), y + 14), label, fill=TEXT, font=LABEL_FONT)

        route_row = route_lookup.get(str(opening_band))
        x = info_x
        draw.rectangle((x, y, x + info_width - 28, y + cell_height - 8), fill=NEUTRAL_FILL, outline=GRID, width=1)
        if route_row is not None:
            selected_family = str(route_row["selected_family"])
            selected_return = float(route_row["selected_avg_gross_return_with_slippage"])
            selected_trades = int(route_row["selected_trade_count"])
            line_1 = selected_family
            line_2 = f"{selected_trades} trades | avg return {selected_return:.3f}"
        else:
            line_1 = "n/a"
            line_2 = ""
        draw.text((x + 10, y + 10), line_1, fill=TEXT, font=LABEL_FONT)
        if line_2:
            draw.text((x + 10, y + 28), line_2, fill=SUBTLE, font=SMALL_FONT)

    return _save(image, render_dir / "master_router_band_map.png")


def render_full_sample_ending_bankrolls(render_dir: Path, portfolio_summary: pd.DataFrame, *, sample_name: str) -> Path | None:
    full_sample = portfolio_summary[portfolio_summary["sample_name"] == sample_name].copy()
    if full_sample.empty:
        return None
    full_sample["ending_bankroll"] = pd.to_numeric(full_sample["ending_bankroll"], errors="coerce")
    full_sample = full_sample.sort_values("ending_bankroll", ascending=False, kind="mergesort").reset_index(drop=True)
    values = full_sample["ending_bankroll"].fillna(0.0).astype(float).tolist()
    if not values:
        return None

    image, draw = _create_canvas()
    _draw_title(
        draw,
        "Full-sample ending bankroll by strategy",
        f"{sample_name} | sorted by ending bankroll | log scale for readability",
    )
    left, top, right, bottom = _draw_axes(draw)
    positive_values = [value for value in values if value > 0]
    min_value = min(positive_values) if positive_values else 1.0
    max_value = max(max(values), 1.0)
    ticks = _value_ticks(positive_values if positive_values else [1.0, 10.0], log_scale=True)
    for tick in ticks:
        y = _map_y(tick, min_value=min_value, max_value=max_value, top=top, bottom=bottom, log_scale=True)
        draw.line((left, y, right, y), fill=GRID, width=1)
        draw.text((20, y - 10), _format_compact_currency(tick), fill=SUBTLE, font=SMALL_FONT)

    bar_count = len(full_sample)
    usable_width = right - left
    gap = 18
    bar_width = max(18, int((usable_width - (gap * (bar_count + 1))) / max(bar_count, 1)))
    for index, row in enumerate(full_sample.to_dict(orient="records")):
        value = max(float(row["ending_bankroll"] or 0.0), min_value)
        x0 = left + gap + (index * (bar_width + gap))
        x1 = x0 + bar_width
        y = _map_y(value, min_value=min_value, max_value=max_value, top=top, bottom=bottom, log_scale=True)
        draw.rectangle((x0, y, x1, bottom), fill=PRIMARY_FILL, outline=PRIMARY, width=2)
        family = str(row["strategy_family"])
        label_width = draw.textlength(family, font=SMALL_FONT)
        draw.text((x0 + ((bar_width - label_width) / 2), bottom + 18), family, fill=TEXT, font=SMALL_FONT)
        value_label = _format_compact_currency(row["ending_bankroll"])
        label_x = x0 + ((bar_width - draw.textlength(value_label, font=SMALL_FONT)) / 2)
        draw.text((label_x, y - 22), value_label, fill=TEXT, font=SMALL_FONT)

    return _save(image, render_dir / "full_sample_ending_bankrolls.png")


def render_robustness_mean_bankrolls(render_dir: Path, robustness_summary: pd.DataFrame) -> Path | None:
    if robustness_summary.empty:
        return None
    work = robustness_summary.copy()
    work["mean_ending_bankroll"] = pd.to_numeric(work["mean_ending_bankroll"], errors="coerce")
    work["positive_seed_rate"] = pd.to_numeric(work["positive_seed_rate"], errors="coerce")
    work = work.sort_values("mean_ending_bankroll", ascending=False, kind="mergesort").reset_index(drop=True)
    values = work["mean_ending_bankroll"].fillna(0.0).astype(float).tolist()
    positive_values = [value for value in values if value > 0]
    if not positive_values:
        return None

    image, draw = _create_canvas()
    _draw_title(draw, "Repeated-seed mean ending bankroll", "10-seed robustness summary | bar color shows positive-seed rate")
    left, top, right, bottom = _draw_axes(draw)
    min_value = min(positive_values)
    max_value = max(positive_values)
    ticks = _value_ticks(positive_values, log_scale=True)
    for tick in ticks:
        y = _map_y(tick, min_value=min_value, max_value=max_value, top=top, bottom=bottom, log_scale=True)
        draw.line((left, y, right, y), fill=GRID, width=1)
        draw.text((20, y - 10), _format_compact_currency(tick), fill=SUBTLE, font=SMALL_FONT)

    bar_count = len(work)
    usable_width = right - left
    gap = 18
    bar_width = max(24, int((usable_width - (gap * (bar_count + 1))) / max(bar_count, 1)))
    for index, row in enumerate(work.to_dict(orient="records")):
        value = max(float(row["mean_ending_bankroll"] or min_value), min_value)
        rate = float(row["positive_seed_rate"] or 0.0)
        x0 = left + gap + (index * (bar_width + gap))
        x1 = x0 + bar_width
        y = _map_y(value, min_value=min_value, max_value=max_value, top=top, bottom=bottom, log_scale=True)
        fill = POSITIVE if rate >= 0.8 else PRIMARY if rate >= 0.5 else NEGATIVE
        draw.rectangle((x0, y, x1, bottom), fill=fill, outline=fill)
        family = str(row["strategy_family"])
        label_width = draw.textlength(family, font=SMALL_FONT)
        draw.text((x0 + ((bar_width - label_width) / 2), bottom + 18), family, fill=TEXT, font=SMALL_FONT)
        rate_label = f"{rate * 100:.0f}%"
        rate_width = draw.textlength(rate_label, font=SMALL_FONT)
        draw.text((x0 + ((bar_width - rate_width) / 2), y - 42), rate_label, fill=TEXT, font=SMALL_FONT)
        value_label = _format_compact_currency(row["mean_ending_bankroll"])
        value_width = draw.textlength(value_label, font=SMALL_FONT)
        draw.text((x0 + ((bar_width - value_width) / 2), y - 22), value_label, fill=TEXT, font=SMALL_FONT)

    return _save(image, render_dir / "robustness_mean_ending_bankrolls.png")


def render_strategy_classification_heatmap(render_dir: Path, classification: pd.DataFrame, *, sample_name: str) -> Path | None:
    work = classification[classification["sample_name"] == sample_name].copy()
    if work.empty:
        return None
    pivot = (
        work.groupby(["opening_band", "best_strategy_family"], dropna=False)
        .size()
        .unstack(fill_value=0)
    )
    pivot = pivot.loc[sorted(pivot.index.tolist(), key=_band_sort_key)]
    family_columns = sorted(pivot.columns.tolist())
    if pivot.empty or not family_columns:
        return None

    cell_width = 150
    cell_height = 56
    image_width = max(CANVAS_WIDTH, LEFT_MARGIN + 260 + (cell_width * len(family_columns)))
    image_height = TOP_MARGIN + 120 + (cell_height * len(pivot.index)) + 120
    image = Image.new("RGB", (image_width, image_height), BACKGROUND)
    draw = ImageDraw.Draw(image)
    draw.text((LEFT_MARGIN, 34), "Best strategy by opening band", fill=TEXT, font=TITLE_FONT)
    draw.text((LEFT_MARGIN, 78), f"{sample_name} | realized best family counts from benchmark_game_strategy_classification.csv", fill=SUBTLE, font=SUBTITLE_FONT)

    origin_x = LEFT_MARGIN + 180
    origin_y = 160
    max_count = int(pivot.to_numpy().max())
    for column_index, family in enumerate(family_columns):
        x = origin_x + (column_index * cell_width)
        draw.text((x + 12, origin_y - 34), family, fill=TEXT, font=SMALL_FONT)
    for row_index, opening_band in enumerate(pivot.index.tolist()):
        y = origin_y + (row_index * cell_height)
        draw.text((LEFT_MARGIN, y + 16), str(opening_band), fill=TEXT, font=LABEL_FONT)
        for column_index, family in enumerate(family_columns):
            value = int(pivot.loc[opening_band, family])
            x = origin_x + (column_index * cell_width)
            intensity = 0 if max_count <= 0 else int(230 - (170 * (value / max_count)))
            fill = (intensity, intensity, 255)
            draw.rectangle((x, y, x + cell_width - 8, y + cell_height - 8), fill=fill, outline=GRID, width=1)
            label = str(value)
            label_width = draw.textlength(label, font=LABEL_FONT)
            draw.text((x + ((cell_width - 8 - label_width) / 2), y + 14), label, fill=TEXT, font=LABEL_FONT)

    path = render_dir / "best_strategy_by_opening_band.png"
    image.save(path, format="PNG")
    return path


def render_payout_thresholds(render_dir: Path, daily_paths: pd.DataFrame, *, sample_name: str) -> Path | None:
    work = daily_paths[daily_paths["sample_name"] == sample_name].copy()
    if work.empty:
        return None
    work["path_day_index"] = pd.to_numeric(work["path_day_index"], errors="coerce")
    work["ending_bankroll"] = pd.to_numeric(work["ending_bankroll"], errors="coerce")
    rows: list[dict[str, object]] = []
    for strategy_family, frame in work.groupby("strategy_family", sort=True):
        ordered = frame.sort_values("path_day_index", kind="mergesort").reset_index(drop=True)
        hit_500 = ordered[ordered["ending_bankroll"] >= 500].head(1)
        hit_10k = ordered[ordered["ending_bankroll"] >= 10_000].head(1)
        rows.append(
            {
                "strategy_family": strategy_family,
                "day_500": int(hit_500.iloc[0]["path_day_index"]) if not hit_500.empty else None,
                "day_10k": int(hit_10k.iloc[0]["path_day_index"]) if not hit_10k.empty else None,
            }
        )
    summary = pd.DataFrame(rows).sort_values(["day_500", "day_10k", "strategy_family"], na_position="last", kind="mergesort")

    image = Image.new("RGB", (1200, 720), BACKGROUND)
    draw = ImageDraw.Draw(image)
    draw.text((80, 34), "Payout threshold timing", fill=TEXT, font=TITLE_FONT)
    draw.text((80, 78), f"{sample_name} | first day index to hit $500 and $10,000 from a $10 start", fill=SUBTLE, font=SUBTITLE_FONT)

    x_positions = {"strategy": 80, "day_500": 420, "day_10k": 640}
    draw.text((x_positions["strategy"], 150), "Strategy", fill=TEXT, font=LABEL_FONT)
    draw.text((x_positions["day_500"], 150), "Day to $500", fill=TEXT, font=LABEL_FONT)
    draw.text((x_positions["day_10k"], 150), "Day to $10k", fill=TEXT, font=LABEL_FONT)
    y = 190
    row_height = 42
    for row in summary.to_dict(orient="records"):
        draw.text((x_positions["strategy"], y), str(row["strategy_family"]), fill=TEXT, font=LABEL_FONT)
        draw.text((x_positions["day_500"], y), "n/a" if row["day_500"] is None else str(row["day_500"]), fill=TEXT, font=LABEL_FONT)
        draw.text((x_positions["day_10k"], y), "n/a" if row["day_10k"] is None else str(row["day_10k"]), fill=TEXT, font=LABEL_FONT)
        y += row_height

    path = render_dir / "payout_threshold_timing.png"
    image.save(path, format="PNG")
    return path


def write_index_html(render_dir: Path, images: list[Path], *, source_dir: Path) -> Path:
    rows = []
    for image in images:
        rows.append(
            f"<section><h2>{image.stem}</h2><img src='{image.name}' alt='{image.stem}' style='max-width: 100%; border: 1px solid #d8dbe2;'/></section>"
        )
    html = "\n".join(
        [
            "<!doctype html>",
            "<html lang='en'>",
            "<head>",
            "<meta charset='utf-8'/>",
            "<title>NBA analysis quicklook</title>",
            "<style>body{font-family:Segoe UI,Arial,sans-serif;margin:32px;color:#111827}section{margin:24px 0}code{background:#f3f4f6;padding:2px 6px;border-radius:4px}</style>",
            "</head>",
            "<body>",
            "<h1>NBA analysis quicklook</h1>",
            f"<p>Source artifacts: <code>{source_dir}</code></p>",
            f"<p>Rendered PNG folder: <code>{render_dir}</code></p>",
            *rows,
            "</body></html>",
        ]
    )
    path = render_dir / "index.html"
    path.write_text(html, encoding="utf-8")
    return path


def resolve_source_dir(args: argparse.Namespace) -> Path:
    if args.source_dir:
        return Path(args.source_dir)
    return Path(args.output_root) / args.season / args.season_phase / args.analysis_version / "backtests"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render quick PNG views for NBA analysis benchmark artifacts.")
    parser.add_argument("--season", default=DEFAULT_SEASON)
    parser.add_argument("--season-phase", default=DEFAULT_SEASON_PHASE)
    parser.add_argument("--analysis-version", default=ANALYSIS_VERSION)
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--source-dir", default=None)
    parser.add_argument("--render-dir", default=None)
    parser.add_argument("--sample-name", default="full_sample")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    source_dir = resolve_source_dir(args)
    render_dir = _ensure_dir(Path(args.render_dir) if args.render_dir else source_dir / "quicklook_png")

    portfolio_daily_paths = _read_csv(source_dir / "benchmark_portfolio_daily_paths.csv")
    portfolio_summary = _read_csv(source_dir / "benchmark_portfolio_summary.csv")
    robustness_summary = _read_csv(source_dir / "benchmark_portfolio_robustness_summary.csv")
    classification = _read_csv(source_dir / "benchmark_game_strategy_classification.csv")
    route_summary = _read_csv(source_dir / "benchmark_route_summary.csv")
    master_router_decisions = _read_csv(source_dir / "benchmark_master_router_decisions.csv")

    images: list[Path] = []
    images.extend(render_strategy_daily_paths(render_dir, portfolio_daily_paths, sample_name=args.sample_name))
    for generator in (
        lambda: render_full_sample_ending_bankrolls(render_dir, portfolio_summary, sample_name=args.sample_name),
        lambda: render_robustness_mean_bankrolls(render_dir, robustness_summary),
        lambda: render_strategy_classification_heatmap(render_dir, classification, sample_name=args.sample_name),
        lambda: render_payout_thresholds(render_dir, portfolio_daily_paths, sample_name=args.sample_name),
        lambda: render_strategy_metric_rankings(render_dir, portfolio_summary, sample_name=args.sample_name),
        lambda: render_master_router_position_scatter(render_dir, portfolio_summary, sample_name=args.sample_name),
        lambda: render_master_router_band_map(render_dir, master_router_decisions, route_summary, sample_name=args.sample_name),
    ):
        output = generator()
        if output is not None:
            images.append(output)
    index_path = write_index_html(render_dir, images, source_dir=source_dir)

    print(f"source_dir={source_dir}")
    print(f"render_dir={render_dir}")
    print(f"index_html={index_path}")
    for image in images:
        print(f"png={image}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
