"""Диаграммы послематчевых PDF. Размеры указаны для печати на A4."""

import io
from collections.abc import Sequence
from statistics import mean
from typing import Any

from matplotlib.axes import Axes
from matplotlib.figure import Figure
from matplotlib.ticker import MaxNLocator
from reportlab.platypus import Image

from pfbc_bot.domain.postmatch import OPTIONS, PLAYING_TIME_KEY, PLAYING_TIME_OPTIONS

NAVY = "#192c46"
TEAL = "#087e83"
PURPLE = "#6353a5"
ZONES = (
    (1, 3, "Лёгкая", TEAL, "#e6f5f1"),
    (4, 7, "Умеренная", "#aa820e", "#faf3d6"),
    (8, 10, "Высокая", "#bd4d32", "#fbe6df"),
)


def zone(value: float) -> tuple[int, int, str, str, str]:
    return next(z for z in ZONES if value < z[1] + 0.5 or z[1] == 10)


def _axes(ax: Axes, title: str, xlabel: str, ylabel: str) -> None:
    ax.set_title(title, loc="left", fontsize=10, fontweight="bold", color=NAVY, pad=8)
    ax.set_xlabel(xlabel, fontsize=9, color=NAVY)
    ax.set_ylabel(ylabel, fontsize=9, color=NAVY)
    ax.tick_params(labelsize=8.5, length=0, pad=3, colors=NAVY)
    for side in ("top", "right", "left", "bottom"):
        ax.spines[side].set_visible(False)
    ax.set_axisbelow(True)
    ax.grid(axis="y", color="#b6c4d2", linewidth=0.45, alpha=0.6)


def _load_key(ax: Axes) -> None:
    title = ax.get_title(loc="left")
    ax.set_title("", loc="left")
    ax.text(
        0,
        1.09,
        title,
        transform=ax.transAxes,
        ha="left",
        va="bottom",
        fontsize=10,
        fontweight="bold",
        color=NAVY,
    )
    for i, (low, high, label, color, background) in enumerate(ZONES):
        ax.text(
            (i + 0.5) / len(ZONES),
            1.025,
            f"{low}–{high} {label}",
            transform=ax.transAxes,
            ha="center",
            va="bottom",
            fontsize=7.4,
            color=NAVY,
            bbox={
                "facecolor": background,
                "edgecolor": color,
                "linewidth": 0.6,
                "boxstyle": "round,pad=.22",
            },
        )


def _image(fig: Figure) -> Image:
    data = io.BytesIO()
    fig.savefig(data, format="png", dpi=300)
    data.seek(0)
    w, h = fig.get_size_inches()
    fig.clear()
    return Image(data, width=w * 72, height=h * 72)


def distribution(game: dict[str, Any], width: float, *, player: bool = False) -> Image:
    fig = Figure(figsize=(width / 72, 4.4), layout="constrained")
    grid = fig.add_gridspec(2, 2)
    axes = [fig.add_subplot(grid[0, :]), fig.add_subplot(grid[1, 0])]
    for ax, q, limit, label in zip(
        axes, ("1", "4"), (10, 5), ("Нагрузка", "Самооценка"), strict=True
    ):
        _axes(ax, label, f"{label}, баллы (1–{limit})", "Кол-во игроков")
        scores = game["stats"]["scores"][q]
        scale = list(range(1, limit + 1))
        counts = [scores.count(k) for k in scale]
        if q == "1":
            _load_key(ax)
            for lo, hi, _, _, background in ZONES:
                ax.axvspan(lo - 0.5, hi + 0.5, color=background, zorder=0)
        palette = (
            [zone(k)[3] for k in scale]
            if q == "1"
            else ["#ada5dc", "#9289ce", "#796cbc", PURPLE, "#43397f"]
        )
        bars = ax.bar(scale, counts, width=0.59, color=palette)
        ax.bar_label(
            bars,
            labels=[str(n) if n else "" for n in counts],
            padding=2,
            fontsize=8,
            color=NAVY,
            fontweight="bold",
        )
        ax.set(xticks=scale, xlim=(0.5, limit + 0.5), ylim=(0, max(1, *counts) * 1.32))
        if player:
            own = _personal_answer(game, q)
            if own is not None:
                _player_dot(ax, int(own), counts[int(own) - 1] / 2)
                ax.legend(fontsize=6, loc="upper right")
        ax.yaxis.set_major_locator(MaxNLocator(integer=True, nbins=4))
        for tick, color in zip(ax.get_xticklabels(), palette, strict=True):
            tick.set_color(color)
            tick.set_fontweight("bold")
    _time_distribution(fig.add_subplot(grid[1, 1]), game, player=player)
    return _image(fig)


def trends(games: list[dict[str, Any]], player_name: str, width: float) -> Image:
    fig = Figure(figsize=(width / 72, 4.6), layout="constrained")
    grid = fig.add_gridspec(2, 2)
    axes = [fig.add_subplot(grid[0, :]), fig.add_subplot(grid[1, 0])]
    for ax, q, limit, label in zip(
        axes,
        ("1", "4"),
        (10, 5),
        ("Нагрузка", "Самооценка"),
        strict=True,
    ):
        _axes(ax, label, "Матч (дата и соперник)", f"{label}, баллы (1–{limit})")
        if q == "1":
            _load_key(ax)
            for lo, hi, _, _, background in ZONES:
                ax.axhspan(lo - 0.5, hi + 0.5, color=background, zorder=0)
        else:
            for lo, hi, background in ((1, 2, "#fbe6df"), (2, 4, "#faf3d6"), (4, 5, "#e6f5f1")):
                ax.axhspan(lo, hi, color=background, zorder=0)
        x = list(range(len(games)))
        series = [mean(v) if (v := g["stats"]["scores"][q]) else float("nan") for g in games]
        ax.plot(
            x,
            series,
            "o-",
            color=TEAL if q == "1" else PURPLE,
            lw=1.7,
            ms=4,
            label="Среднее команды",
        )
        if player_name:
            personal = [
                float(value) if (value := _personal_answer(g, q)) is not None else float("nan")
                for g in games
            ]
            ax.plot(x, personal, "D--", color="#a14468", lw=1.4, ms=3.7, label=player_name)
            ax.legend(loc="upper left", fontsize=6.5, framealpha=0.9)
        ticks = []
        for game in games:
            opponent = game["opponent"]
            short = opponent[:7] + "…" if len(opponent) > 8 else opponent
            ticks.append(f"{game['date'][:5]}\n{short}")
        ax.set(xticks=x, xticklabels=ticks, xlim=(-0.35, len(x) - 0.65), ylim=(0.85, limit + 0.15))
        ax.set_yticks(range(1, limit + 1, 2) if limit == 10 else range(1, 6))
        if limit == 10:
            ax.set_yticks([1, 3, 5, 7, 9, 10])
    _time_trend(fig.add_subplot(grid[1, 1]), games, player=bool(player_name))
    return _image(fig)


def requests_chart(
    counts: dict[str, Any],
    width: float,
    *,
    player: bool = False,
    compact: bool = False,
    personal: dict[str, Any] | None = None,
) -> Image:
    fig = Figure(figsize=(width / 72, 2.0 if compact else 2.65), layout="constrained")
    axes = fig.subplots(1, 2)
    for i, q in enumerate((5, 6)):
        series = personal[str(q)] if player and personal else counts[str(q)]
        codes = sorted(OPTIONS[q], key=lambda k: (-series.get(k, 0), OPTIONS[q][k]))
        if compact or player:
            codes = [code for code in codes if series.get(code)]
        _bars(
            axes[i],
            "Индивидуальные навыки" if q == 5 else "Командные элементы",
            "Кол-во матчей" if player else "Кол-во игроков",
            "Навык" if q == 5 else "Командный элемент",
            q,
            codes,
            series,
            PURPLE if player or q == 6 else TEAL,
        )
    return _image(fig)


def _bars(
    ax: Axes,
    title: str,
    units: str,
    label: str,
    q: int,
    codes: Sequence[str],
    counts: dict[str, int],
    color: str,
) -> None:
    _axes(ax, title, units, label)
    if not codes:
        ax.set(xticks=[], yticks=[])
        ax.text(
            0.5,
            0.5,
            "Нет запросов",
            ha="center",
            va="center",
            color=NAVY,
            fontsize=9,
            transform=ax.transAxes,
        )
        return
    values = [counts.get(k, 0) for k in codes]
    bars = ax.barh(range(len(codes)), values, height=0.56, color=color)
    ax.set_yticks(range(len(codes)), [OPTIONS[q][k] for k in codes])
    ax.invert_yaxis()
    ax.set_xlim(0, max(1, *values) * 1.22)
    ax.xaxis.set_major_locator(MaxNLocator(integer=True, nbins=4))
    ax.grid(False)
    ax.grid(axis="x", linewidth=0.4, color="#dbe3ea")
    ax.bar_label(bars, padding=2, fontsize=7.5, color=NAVY)


def _time_distribution(ax: Axes, game: dict[str, Any], *, player: bool = False) -> None:
    _axes(ax, "Игровое время", "Удовлетворённость временем", "Кол-во игроков")
    counts = game["stats"].get("playing_time", {})
    if not sum(counts.values()):
        ax.text(0.5, 0.5, "Нет данных", transform=ax.transAxes, ha="center")
    values = [counts.get(code, 0) for code in PLAYING_TIME_OPTIONS]
    bars = ax.bar(range(3), values, color=["#ada5dc", "#796cbc", "#43397f"], width=0.6)
    ax.bar_label(bars, labels=[str(v) if v else "" for v in values], padding=2, fontsize=8)
    ax.set(
        xticks=range(3),
        xticklabels=["Хочу играть\nбольше", "Хочу играть\nменьше", "Удовлетворён"],
        ylim=(0, max(1, *values) * 1.3),
    )
    ax.tick_params(axis="x", labelsize=7)
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    if player and (own := _personal_answer(game, PLAYING_TIME_KEY)) in PLAYING_TIME_OPTIONS:
        index = list(PLAYING_TIME_OPTIONS).index(own)
        _player_dot(ax, index, values[index] / 2)
        ax.legend(fontsize=6, loc="upper right")


def _time_trend(ax: Axes, games: list[dict[str, Any]], *, player: bool = False) -> None:
    _axes(ax, "Игровое время", "Матч (дата и соперник)", "Доля ответов, %")
    x = list(range(len(games)))
    counts = [g["stats"].get("playing_time", {}) for g in games]
    totals = [sum(c.values()) for c in counts]
    bottom = [0.0] * len(games)
    for (code, label), color in zip(
        PLAYING_TIME_OPTIONS.items(), ("#bd4d32", "#e5bd4f", "#398565"), strict=True
    ):
        values = [100 * c.get(code, 0) / n if n else 0 for c, n in zip(counts, totals, strict=True)]
        ax.bar(
            x,
            values,
            bottom=bottom,
            color=color,
            edgecolor="white",
            width=0.58,
            label=label,
            linewidth=0.6,
        )
        bottom = [b + v for b, v in zip(bottom, values, strict=True)]
    for i, n in enumerate(totals):
        ax.text(i, 102 if n else 45, f"n={n}" if n else "Нет\nданных", ha="center", fontsize=6)
    ax.set(
        xticks=x,
        xticklabels=[f"{g['date'][:5]}\n{g['opponent'][:7]}" for g in games],
        ylim=(0, 112),
        yticks=[0, 25, 50, 75, 100],
    )
    ax.tick_params(axis="x", labelsize=6.5)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.3), fontsize=6, frameon=False)

    # Measure the final axes in points: small segments get leader-line labels.
    assert isinstance(ax.figure, Figure)
    ax.figure.draw_without_rendering()
    height_points = ax.get_window_extent().height * 72 / ax.figure.dpi
    min_height = 9 * 112 / height_points
    for i, (counts_for_game, total) in enumerate(zip(counts, totals, strict=True)):
        if not total:
            continue
        low = 0.0
        outside = []
        for code in PLAYING_TIME_OPTIONS:
            value = 100 * counts_for_game.get(code, 0) / total
            center = low + value / 2
            low += value
            if not value:
                continue
            text = "<1%" if value < 1 else f"{value:.0f}%"
            if value >= min_height:
                marked = player and _personal_answer(games[i], PLAYING_TIME_KEY) == code
                ax.text(
                    i + 0.1 if marked else i,
                    center,
                    text,
                    ha="center",
                    va="center",
                    fontsize=6 if marked else 7,
                    color=NAVY if code == "less" else "white",
                    fontweight="bold",
                )
            else:
                outside.append((center, text))
        # Separate callouts vertically, keeping them within the 0–100% range.
        positions: list[float] = []
        for center, _ in outside:
            positions.append(
                max(min_height / 2, center, positions[-1] + min_height if positions else 0)
            )
        if positions and positions[-1] > 100 - min_height / 2:
            shift = positions[-1] - (100 - min_height / 2)
            positions = [pos - shift for pos in positions]
        for (center, text), position in zip(outside, positions, strict=True):
            ax.annotate(
                text,
                xy=(i + 0.29, center),
                xytext=(i + 0.34, position),
                ha="left",
                va="center",
                fontsize=5.5,
                color=NAVY,
                annotation_clip=False,
                arrowprops={
                    "arrowstyle": "-",
                    "color": NAVY,
                    "lw": 0.5,
                    "shrinkA": 0,
                    "shrinkB": 0,
                },
            )

    if player:
        for i, game in enumerate(games):
            own = _personal_answer(game, PLAYING_TIME_KEY)
            total = totals[i]
            if own not in PLAYING_TIME_OPTIONS or not total:
                continue
            low = 0.0
            for code in PLAYING_TIME_OPTIONS:
                value = 100 * counts[i].get(code, 0) / total
                if code == own and value:
                    _player_dot(ax, i - 0.19, low + value / 2)
                    break
                low += value
        handles, labels = ax.get_legend_handles_labels()
        unique = dict(zip(labels, handles, strict=True))
        ax.legend(
            unique.values(),
            unique.keys(),
            loc="upper center",
            bbox_to_anchor=(0.5, -0.3),
            fontsize=6,
            frameon=False,
        )


def _personal_answer(game: dict[str, Any], key: str) -> Any:
    rows = game["rows"]
    return rows[0]["answers"].get(key) if rows and rows[0]["status"] == "submitted" else None


def _player_dot(ax: Axes, x: float, y: float) -> None:
    ax.plot(
        x,
        y,
        "o",
        color=NAVY,
        markeredgecolor="white",
        markeredgewidth=1,
        markersize=5,
        zorder=5,
        label="Ответ игрока",
    )
