"""Сборка файлов из неизменяемого снимка. Нет обращений к БД или Telegram."""

import csv
import io
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
from typing import Any
from xml.sax.saxutils import escape

from matplotlib import get_data_path
from matplotlib.figure import Figure
from matplotlib.ticker import MaxNLocator
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Flowable, Image, LongTable, Paragraph, SimpleDocTemplate, TableStyle

from pfbc_bot.domain.postmatch import EXCLUDED, OPTIONS, STATUS, answer_label

Row = dict[str, Any]


def summarize(rows: list[Row]) -> Row:
    answered = [r["answers"] for r in rows if r["status"] == "submitted"]
    totals = Counter(r["status"] for r in rows)
    return {
        "n": len(answered),
        "total": sum(v for k, v in totals.items() if k not in EXCLUDED),
        "absent": totals["absent"],
        "declined": totals["declined"],
        "scores": {str(q): [int(a[str(q)]) for a in answered] for q in (1, 4)},
        "skills": {
            str(q): dict(
                Counter(code for a in answered for code in a.get(str(q), []) if code != "none")
            )
            for q in (5, 6)
        },
    }


_FONTS = Path(get_data_path()) / "fonts" / "ttf"
for _name in ("DejaVuSans", "DejaVuSans-Bold"):
    pdfmetrics.registerFont(TTFont(_name, str(_FONTS / f"{_name}.ttf")))

_BODY = ParagraphStyle("body", fontName="DejaVuSans", fontSize=10, leading=14, spaceAfter=5)
_CELL = ParagraphStyle("cell", parent=_BODY, fontSize=8, leading=10, spaceAfter=0)
_HEADER = ParagraphStyle("header", parent=_CELL, fontName="DejaVuSans-Bold")
_HEADINGS = [
    ParagraphStyle(
        f"heading-{size}",
        parent=_BODY,
        fontName="DejaVuSans-Bold",
        fontSize=size,
        leading=size + 4,
        spaceBefore=6,
        keepWithNext=True,
    )
    for size in (19, 15, 12)
]
_WIDTH = A4[0] - 84  # 36 pt margins plus the default 6 pt frame padding on each side.


def _paragraph(value: str, style: ParagraphStyle = _BODY) -> Paragraph:
    return Paragraph("<br/>".join(escape(value).splitlines()), style)


def _table(doc: list[Flowable], rows: list[list[str]], widths: list[int]) -> None:
    data = [
        [_paragraph(v, _HEADER if i == 0 else _CELL) for v in row] for i, row in enumerate(rows)
    ]
    table = LongTable(
        data,
        colWidths=[_WIDTH * w / sum(widths) for w in widths],
        repeatRows=1,
        splitInRow=1,
        spaceAfter=8,
    )
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dcebf1")),
                ("GRID", (0, 0), (-1, -1), 0.3, colors.lightgrey),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    doc.append(table)


def _figure(doc: list[Flowable], figure: Figure) -> None:
    buf = io.BytesIO()
    figure.savefig(buf, format="png", dpi=130, bbox_inches="tight")
    buf.seek(0)
    image = Image(buf)
    image.drawWidth = min(_WIDTH, 432)
    image.drawHeight = image.drawWidth * image.imageHeight / image.imageWidth
    doc.append(image)
    figure.clear()


def render_report(snapshot: Row, kind: str, as_of: str) -> bytes:
    games = snapshot["games"]
    if kind == "csv":
        stream = io.StringIO()
        writer = csv.writer(stream)
        sensitive = snapshot["visibility"] == "sensitive"
        cols = [1, 4, 5, 6] + ([2, 3, 7] if sensitive else [])
        labels = {
            1: "Нагрузка",
            2: "Самочувствие",
            3: "Боль",
            4: "Самооценка",
            5: "Индивидуально",
            6: "Командно",
            7: "Комментарий",
        }
        writer.writerow(["Дата", "Соперник", "ID", "ФИО", "Статус", *(labels[q] for q in cols)])
        for game in games:
            for row in game["rows"]:
                if snapshot["player"] and row["id"] != snapshot["player"]:
                    continue
                values = [
                    game["date"],
                    game["opponent"],
                    row["id"],
                    row["name"],
                    STATUS[row["status"]],
                    *(
                        answer_label(q, row["answers"]) if row["status"] == "submitted" else ""
                        for q in cols
                    ),
                ]
                writer.writerow(
                    [
                        "'" + v
                        if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@"))
                        else v
                        for v in values
                    ]
                )
        return stream.getvalue().encode("utf-8-sig")
    if kind != "pdf":
        raise ValueError(f"Неизвестный формат отчёта: {kind}")
    doc: list[Flowable] = []
    doc.append(_paragraph(snapshot["title"], _HEADINGS[0]))
    doc.append(_paragraph("Данные на " + as_of))
    if not games:
        doc.append(_paragraph("За выбранный период матчей нет."))
    if snapshot["history"] and games:
        _history(doc, snapshot)
    else:
        for game in games:
            _match(doc, game, snapshot["visibility"])
    output = io.BytesIO()
    SimpleDocTemplate(
        output,
        pagesize=A4,
        leftMargin=36,
        rightMargin=36,
        topMargin=32,
        bottomMargin=32,
        title=snapshot["title"],
    ).build(doc)
    return output.getvalue()


def _match(doc: list[Flowable], game: Row, visibility: str) -> None:
    doc.append(
        _paragraph(f"{game['date']} · {game['squad']} против {game['opponent']}", _HEADINGS[1])
    )
    stats = game["stats"]
    ratio = f"Ответили {stats['n']} из {stats['total']}"
    doc.append(
        _paragraph(
            f"{ratio}. Не участвовали в матче: {stats['absent']}. "
            f"Не будут проходить опрос: {stats['declined']}."
        )
    )
    if visibility != "group":
        columns = ["ФИО", "Статус", "Нагрузка", "Самооценка", "Индивидуально", "Командно"]
        fields = [1, 4, 5, 6]
        if visibility == "sensitive":
            columns += ["Состояние", "Боль"]
            fields += [2, 3]
        rows = [columns]
        for row in game["rows"]:
            rows.append(
                [
                    row["name"],
                    STATUS[row["status"]],
                    *(
                        answer_label(q, row["answers"]) if row["status"] == "submitted" else "—"
                        for q in fields
                    ),
                ],
            )
        widths = [15, 13, 9, 12, 18, 18] + ([12, 14] if visibility == "sensitive" else [])
        _table(doc, rows, widths)
        if visibility == "sensitive":
            comments = [
                r for r in game["rows"] if r["status"] == "submitted" and r["answers"].get("7")
            ]
            if comments:
                doc.append(_paragraph("Комментарии", _HEADINGS[2]))
                for row in comments:
                    doc.append(_paragraph(f"{row['name']} — {answer_label(7, row['answers'])}"))
    for score, title, limit in (("1", "Нагрузка", 10), ("4", "Самооценка", 5)):
        doc.append(_paragraph(title, _HEADINGS[2]))
        values = stats["scores"][score]
        if not values:
            doc.append(_paragraph("Нет данных · " + ratio))
            continue
        fig = Figure(figsize=(6, 2.5), layout="constrained")
        ax = fig.subplots()
        scale = list(range(1, limit + 1))
        ax.bar(scale, [values.count(i) for i in scale], color="#286480")
        ax.set(xticks=scale, ylabel="Игроки", xlabel="Оценка", ylim=(0, None))
        ax.yaxis.set_major_locator(MaxNLocator(integer=True))
        _figure(doc, fig)
        doc.append(_paragraph(f"Среднее {mean(values):.1f} из {limit} · {ratio}".replace(".", ",")))
    doc.append(_paragraph("Запросы на тренировку", _HEADINGS[2]))
    for q, title in ((5, "Индивидуально"), (6, "Командно")):
        doc.append(
            _paragraph(
                title
                + ": "
                + ", ".join(
                    f"{label} — {stats['skills'][str(q)].get(code, 0)}"
                    for code, label in OPTIONS[q].items()
                )
            )
        )


def _history(doc: list[Flowable], snapshot: Row) -> None:
    games = snapshot["games"]
    target = snapshot["player"]
    skills: dict[tuple[int, str], set[int]] = defaultdict(set)
    player_skills: Counter[tuple[int, str]] = Counter()
    rows = [
        [
            "Дата · соперник",
            "Ответили",
            "Нагрузка команды",
            "Самооценка команды",
            "Оценки игрока",
            "Запросы игрока",
        ],
    ]
    series: dict[int, list[float]] = {1: [], 4: []}
    personal: dict[int, list[float]] = {1: [], 4: []}
    for g in games:
        stats = g["stats"]
        for q in (1, 4):
            vals = stats["scores"][str(q)]
            series[q].append(mean(vals) if vals else float("nan"))
        own = {}
        for row in g["rows"]:
            if row["status"] != "submitted":
                continue
            if row["id"] == target:
                own = row["answers"]
            for q in (5, 6):
                for code in row["answers"].get(str(q), []):
                    if code != "none":
                        skills[q, code].add(row["id"])
                        if row["id"] == target:
                            player_skills[q, code] += 1
        for q in (1, 4):
            personal[q].append(float(own.get(str(q), "nan")))
        rows.append(
            [
                g["date"] + " · " + g["opponent"],
                f"{stats['n']} из {stats['total']}",
                *(f"{series[q][-1]:.1f}" if stats["n"] else "Нет данных" for q in (1, 4)),
                f"{own['1']} / {own['4']}" if own else "—",
                " / ".join(answer_label(q, own) for q in (5, 6)) if own else "—",
            ],
        )
    _table(doc, rows, [21, 11, 15, 17, 13, 23])
    for q, label, limit in ((1, "Нагрузка", 10), (4, "Самооценка", 5)):
        # Длинную историю делим на читаемые панели.
        for i in range(0, len(games), 10):
            batch = games[i : i + 10]
            fig = Figure(figsize=(6, 3), layout="constrained")
            ax = fig.subplots()
            x = list(range(len(batch)))
            ax.plot(x, series[q][i : i + 10], "o-", label="Команда")
            if target:
                ax.plot(x, personal[q][i : i + 10], "s--", label=snapshot["player_name"])
            ax.set(ylim=(1, limit), title=label, xticks=x)
            ax.set_xticklabels(
                [g["date"][:5] + " · " + g["opponent"][:16] for g in batch], rotation=35, ha="right"
            )
            ax.legend()
            _figure(doc, fig)
    doc.append(_paragraph("Запросы за период", _HEADINGS[2]))
    responders = {r["id"] for g in games for r in g["rows"] if r["status"] == "submitted"}
    doc.append(
        _paragraph(
            f"{games[0]['date']} — {games[-1]['date']}. Ответили игроков: {len(responders)}."
        )
    )
    for q, label in ((5, "Индивидуально"), (6, "Командно")):
        fig = Figure(figsize=(6, 3), layout="constrained")
        ax = fig.subplots()
        for idx, code in enumerate(OPTIONS[q]):
            ax.barh(
                idx - 0.16,
                len(skills[q, code]),
                height=0.32,
                color="#286480",
                label="Игроков" if idx == 0 else None,
            )
            if target:
                ax.barh(
                    idx + 0.16,
                    player_skills[q, code],
                    height=0.32,
                    color="#bb7433",
                    label="Матчей игрока" if idx == 0 else None,
                )
        ax.set(
            yticks=list(range(len(OPTIONS[q]))), yticklabels=list(OPTIONS[q].values()), title=label
        )
        ax.xaxis.set_major_locator(MaxNLocator(integer=True))
        ax.legend()
        _figure(doc, fig)
