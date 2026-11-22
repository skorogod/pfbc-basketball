"""Диаграммы послематчевых PDF. Размеры указаны для печати на A4."""

import io
from collections.abc import Sequence
from statistics import mean
from typing import Any

from matplotlib.axes import Axes
from matplotlib.figure import Figure
from matplotlib.ticker import MaxNLocator
from reportlab.platypus import Image

from pfbc_bot.domain.postmatch import OPTIONS

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


def distribution(game: dict[str, Any], width: float) -> Image:
    fig = Figure(figsize=(width / 72, 2.2), layout="constrained")
    for ax, q, limit, label in zip(
        fig.subplots(1, 2), ("1", "4"), (10, 5), ("Нагрузка", "Самооценка"), strict=True
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
        ax.yaxis.set_major_locator(MaxNLocator(integer=True, nbins=4))
        for tick, color in zip(ax.get_xticklabels(), palette, strict=True):
            tick.set_color(color)
            tick.set_fontweight("bold")
    return _image(fig)


def trends(games: list[dict[str, Any]], player_name: str, width: float) -> Image:
    stacked = len(games) > 5 or (len(games) >= 4 and any(len(g["opponent"]) > 8 for g in games))
    fig = Figure(figsize=(width / 72, 4.95 if stacked else 2.8), layout="constrained")
    for ax, q, limit, label in zip(
        fig.subplots(2, 1) if stacked else fig.subplots(1, 2),
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
                float(g["rows"][0]["answers"].get(q, "nan")) if g["rows"] else float("nan")
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
    return _image(fig)


def requests_chart(
    counts: dict[str, Any],
    width: float,
    *,
    player: bool = False,
    compact: bool = False,
    personal: dict[str, Any] | None = None,
) -> Image:
    # Для игрока каждая строка содержит две шкалы с разными единицами.
    fig = Figure(
        figsize=(width / 72, 4.65 if player else (2.0 if compact else 2.65)), layout="constrained"
    )
    axes = fig.subplots(2 if player else 1, 2, squeeze=False)
    for i, q in enumerate((5, 6)):
        series = counts[str(q)]
        own = personal[str(q)] if personal else {}
        codes = sorted(OPTIONS[q], key=lambda k: (-series.get(k, 0), -own.get(k, 0), OPTIONS[q][k]))
        if compact:
            codes = [code for code in codes if series.get(code)]
        label = "Навык" if q == 5 else "Командный элемент"
        if player:
            _bars(axes[i, 0], "Отметил игрок", "Кол-во матчей", label, q, codes, own, PURPLE)
            _bars(
                axes[i, 1],
                "Отметили другие игроки",
                "Кол-во игроков",
                label,
                q,
                codes,
                series,
                TEAL,
            )
        else:
            _bars(
                axes[0, i],
                "Индивидуальные навыки" if q == 5 else "Командные элементы",
                "Кол-во игроков",
                label,
                q,
                codes,
                series,
                TEAL if q == 5 else PURPLE,
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
