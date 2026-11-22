"""Сборка файлов из неизменяемого снимка. Нет обращений к БД или Telegram."""

import csv
import io
import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
from typing import Any
from xml.sax.saxutils import escape

from matplotlib import get_data_path
from reportlab.graphics.shapes import Circle, Drawing, Group, Rect, String, Wedge
from reportlab.graphics.shapes import Path as ShapePath
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Flowable,
    KeepTogether,
    LongTable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from pfbc_bot.domain.postmatch import EXCLUDED, STATUS, answer_label
from pfbc_bot.services import survey_charts as charts

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
_NUMBER = ParagraphStyle("number", parent=_CELL, alignment=0, fontName="DejaVuSans-Bold")
_HEADER = ParagraphStyle("header", parent=_CELL, fontName="DejaVuSans-Bold")
_HEADINGS = [
    ParagraphStyle(
        f"heading-{size}",
        parent=_BODY,
        fontName="DejaVuSans-Bold",
        fontSize=size,
        leading=size + 4,
        spaceBefore=12,
        keepWithNext=True,
    )
    for size in (19, 15, 12)
]
_WIDTH = A4[0] - 84  # 36 pt margins plus the default 6 pt frame padding on each side.


def _paragraph(value: str, style: ParagraphStyle = _BODY) -> Paragraph:
    return Paragraph("<br/>".join(escape(value).splitlines()), style)


def _table(
    doc: list[Flowable], rows: list[list[str]], widths: list[int], numeric: tuple[int, ...] = ()
) -> None:
    data = [
        [
            _paragraph(v, _HEADER if i == 0 else _NUMBER if j in numeric else _CELL)
            for j, v in enumerate(row)
        ]
        for i, row in enumerate(rows)
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
                ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#cad5df")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, _LIGHT]),
                ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#cad5df")),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    doc.append(table)


def render_report(snapshot: Row, kind: str, as_of: str) -> bytes:
    games = snapshot["games"]
    if kind == "csv":
        stream = io.StringIO()
        writer = csv.writer(stream)
        cols = [1, 4, 5, 6, 2, 3, 7]
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
    return _pdf(snapshot, as_of)


def make_snapshot(
    games: list[Row],
    visibility: str,
    squad: str,
    anchor: int,
    period: str = "5",
    player: int = 0,
    player_name: str = "",
) -> Row:
    responders = set()
    voters: dict[str, dict[str, set[int]]] = {"5": defaultdict(set), "6": defaultdict(set)}
    personal: dict[str, Counter[str]] = {"5": Counter(), "6": Counter()}
    sanitized = []
    for game in games:
        for row in game["rows"]:
            if row["status"] != "submitted":
                continue
            uid = row["id"]
            responders.add(uid)
            for q in voters:
                codes = set(row["answers"].get(q, [])) - {"none"}
                if uid == player:
                    personal[q].update(codes)
                else:
                    for code in codes:
                        voters[q][code].add(uid)
        rows = game["rows"]
        if visibility == "group":
            rows = []
        elif player:
            rows = [row for row in rows if row["id"] == player]
        sanitized.append({**game, "rows": rows})
    return {
        "layout": 4,
        "visibility": visibility,
        "squad": squad,
        "anchor": anchor,
        "period": period,
        "player": player,
        "player_name": player_name,
        "games": sanitized,
        "unique": len(responders),
        "requests": {q: {k: len(ids) for k, ids in codes.items()} for q, codes in voters.items()},
        "personal_requests": {q: dict(counts) for q, counts in personal.items()},
    }


_INK = colors.HexColor(charts.NAVY)
_MUTED = colors.HexColor("#516579")
_ACCENT = colors.HexColor(charts.TEAL)
_LIGHT = colors.HexColor("#eef4f7")
_ICONS = json.loads((Path(__file__).parent / "assets" / "report-icons.json").read_text())
_BANNER = ParagraphStyle(
    "banner",
    parent=_HEADINGS[0],
    textColor=colors.white,
    fontSize=22,
    leading=27,
    spaceBefore=0,
    spaceAfter=0,
)
_KICKER = ParagraphStyle(
    "kicker",
    parent=_BODY,
    fontName="DejaVuSans-Bold",
    textColor=colors.HexColor("#9de1d7"),
    fontSize=9,
    leading=12,
    spaceAfter=0,
)
_SUB = ParagraphStyle("sub", parent=_BODY, textColor=_MUTED, fontSize=9, leading=12)


def _banner(doc: list[Flowable], label: str, title: str, subtitle: str) -> None:
    sub = ParagraphStyle("banner-sub", parent=_BODY, textColor=colors.white, spaceAfter=0)
    panel = Table(
        [[_paragraph(label, _KICKER)], [_paragraph(title, _BANNER)], [_paragraph(subtitle, sub)]],
        colWidths=[_WIDTH],
        style=[
            ("BACKGROUND", (0, 0), (-1, -1), _INK),
            ("LEFTPADDING", (0, 0), (-1, -1), 14),
            ("TOPPADDING", (0, 0), (0, 0), 9),
            ("BOTTOMPADDING", (0, -1), (0, -1), 9),
        ],
    )
    doc.extend([panel, Spacer(1, 7)])


def _icon(d: Drawing, name: str, x: float, y: float) -> None:
    # Локальные контуры Lucide, лицензия в assets/LUCIDE-LICENSE.
    group = Group()
    group.translate(x, y)
    group.scale(0.6, -0.6)
    path = ShapePath(
        fillColor=None, strokeColor=_ACCENT, strokeWidth=2, strokeLineCap=1, strokeLineJoin=1
    )
    for method, points in _ICONS[name]:
        if method == "closePath":
            path.closePath()
        elif method != "endPath":
            getattr(path, method)(*(coord for point in points for coord in point))
    group.add(path)
    d.add(group)


def _text(
    d: Drawing,
    x: float,
    y: float,
    value: str,
    size: float = 9,
    *,
    bold: bool = False,
    color: Any = _INK,
    center: bool = False,
) -> None:
    d.add(
        String(
            x,
            y,
            value,
            fontName="DejaVuSans-Bold" if bold else "DejaVuSans",
            fontSize=size,
            fillColor=color,
            textAnchor="middle" if center else "start",
        )
    )


def _metrics(
    n: int, total: int, values: list[tuple[str, str, str]], *, period: bool = False
) -> Drawing:
    d = Drawing(_WIDTH, 70)
    w = _WIDTH / 3
    for i in range(3):
        d.add(Rect(i * w, 2, w - 4, 66, rx=5, ry=5, fillColor=_LIGHT, strokeColor=None))
    _icon(d, "users", 7, 62)
    _text(
        d,
        25,
        51,
        "Заполнено анкет за период" if period else "Кол-во ответивших игроков",
        8,
        bold=True,
    )
    cx, cy, r = 31, 27, 19
    d.add(Circle(cx, cy, r, fillColor=colors.HexColor("#cad5df"), strokeColor=None))
    if total and n:
        if n == total:
            d.add(Circle(cx, cy, r, fillColor=_ACCENT, strokeColor=None))
        else:
            d.add(Wedge(cx, cy, r, 90 - 360 * n / total, 90, fillColor=_ACCENT, strokeColor=None))
    d.add(Circle(cx, cy, r - 4, fillColor=_LIGHT, strokeColor=None))
    _text(d, cx, cy - 2.4, f"{n}/{total}", 6.8, bold=True, center=True)
    _text(d, 57, 32, f"{n / total:.0%}" if total else "Нет участников", 11, bold=True)
    _text(d, 57, 21, f"Ответили · {n}", 7.6, color=_ACCENT)
    _text(d, 57, 11, f"Без ответа · {total - n}", 7.6, color=_MUTED)
    for i, (label, value, note) in enumerate(values, 1):
        x = i * w + 9
        _icon(d, "gauge" if i == 1 else "target", x, 62)
        _text(d, x + 18, 51, label, 8, bold=True)
        _text(d, x, 25, value, 19, bold=True)
        _text(d, x, 11, note, 8, color=_MUTED)
    return d


def _scores(game: Row, player: int) -> list[tuple[str, str, str]]:
    result = []
    for q, label, limit in (("1", "Нагрузка", 10), ("4", "Самооценка", 5)):
        vals = game["stats"]["scores"][q]
        if player:
            vals = [int(row["answers"][q]) for row in game["rows"] if q in row["answers"]]
        value = mean(vals) if vals else None
        display = f"{value:.1f} / {limit}".replace(".", ",") if value is not None else "—"
        note = "Нет данных" if value is None else ("Оценка игрока" if player else "Среднее команды")
        if q == "1" and value is not None:
            note += " · " + charts.zone(value)[2].lower()
        result.append((label, display, note))
    return result


def _details(doc: list[Flowable], snapshot: Row) -> None:
    player = snapshot["player"]
    games = snapshot["games"] if player else snapshot["games"][-1:]
    block: list[Flowable] = []
    block.append(_paragraph("Анкеты игрока" if player else "Ответы игроков", _HEADINGS[1]))
    rows = [
        [
            "Дата · соперник" if player else "ФИО",
            "Статус",
            "Нагрузка",
            "Оценка",
            "Состояние",
            "Боль",
            "Навыки",
            "Командные элементы",
        ]
    ]
    comments = []
    for game in games:
        for row in game["rows"]:
            who = f"{game['date']} · {game['opponent']}" if player else row["name"]
            answers = row["answers"]
            fields = [
                answer_label(q, answers) if row["status"] == "submitted" else "—"
                for q in (1, 4, 2, 3, 5, 6)
            ]
            rows.append([who, STATUS[row["status"]], *fields])
            if answers.get("7"):
                comments.append((who, answers["7"]))
    _table(block, rows, [16, 12, 10, 8, 13, 11, 15, 15], numeric=(2, 3))
    if comments:
        block.append(_paragraph("Комментарии", _HEADINGS[2]))
        for who, comment in comments:
            block.append(_paragraph(f"{who} — {comment}"))
    doc.append(KeepTogether(block))


def _pdf(snapshot: Row, as_of: str) -> bytes:
    games = snapshot["games"]
    player = snapshot["player"]
    name = snapshot["player_name"]
    view = snapshot["visibility"]
    title = {"group": "Общий отчет", "staff": "Отчёт штаба", "player": "Отчет по игроку"}[view]
    doc: list[Flowable] = []
    if games:
        last = games[-1]
        stats = last["stats"]
        fixture = f"{last['squad']} — {last['opponent']}"
        _banner(
            doc,
            title.upper(),
            name if player else fixture,
            f"{last['date']}  ·  {fixture if player else 'Последний матч'}",
        )
        doc.append(_metrics(stats["n"], stats["total"], _scores(last, player)))
        doc.append(
            _paragraph(
                f"Не участвовали в матче: {stats['absent']}   ·   Отказались: {stats['declined']}",
                _SUB,
            )
        )
        if stats["n"]:
            doc.append(Spacer(1, 12))
            doc.append(_paragraph("Оценки команды за матч", _HEADINGS[2]))
            doc.append(charts.distribution(last, _WIDTH))
            doc.append(
                KeepTogether(
                    [
                        _paragraph("Запросы команды за матч", _HEADINGS[2]),
                        charts.requests_chart(stats["skills"], _WIDTH, compact=True),
                    ]
                )
            )
        else:
            doc.append(_paragraph("Нет данных · Ответили 0 из " + str(stats["total"])))
        if player and last["rows"]:
            last_answers: list[Flowable] = []
            last_answers.append(_paragraph("Анкета за последний матч", _HEADINGS[2]))
            row = last["rows"][0]
            if row["status"] == "submitted":
                a = row["answers"]
                _table(
                    last_answers,
                    [
                        ["Самочувствие", "Боль", "Индивидуально", "Командно"],
                        [answer_label(q, a) for q in (2, 3, 5, 6)],
                    ],
                    [22, 22, 28, 28],
                )
                if a.get("7"):
                    last_answers.append(_paragraph(a["7"]))
            else:
                last_answers.append(_paragraph("Нет ответа · " + STATUS[row["status"]]))
            doc.append(KeepTogether(last_answers))
        if view == "staff":
            _details(doc, snapshot)
        doc.append(PageBreak())
        dates = f"{games[0]['date']} — {last['date']}"
        _banner(doc, "ДИНАМИКА ЗА ПЕРИОД", name if player else snapshot["squad"], dates)
        doc.append(
            _metrics(
                sum(g["stats"]["n"] for g in games),
                sum(g["stats"]["total"] for g in games),
                [
                    ("Матчи", str(len(games)), "За выбранный период"),
                    ("Ответили игроки", str(snapshot["unique"]), "Уникальных игроков"),
                ],
                period=True,
            )
        )
        for start in range(0, len(games), 10):
            batch = games[start : start + 10]
            trend_block: list[Flowable] = [
                _paragraph("Динамика нагрузки и самооценки", _HEADINGS[2]),
            ]
            if any(g["stats"]["n"] for g in batch):
                trend_block.append(charts.trends(batch, name, _WIDTH))
            else:
                trend_block.append(_paragraph("Нет данных"))
            table = [["Дата", "Соперник", "Ответы / участники", "Нагрузка / 10", "Самооценка / 5"]]
            for game in batch:
                stats = game["stats"]
                means = [
                    f"{mean(scores):.1f}".replace(".", ",") if scores else "—"
                    for scores in (stats["scores"]["1"], stats["scores"]["4"])
                ]
                table.append(
                    [game["date"], game["opponent"], f"{stats['n']} / {stats['total']}", *means]
                )
            _table(trend_block, table, [17, 26, 19, 18, 20])
            doc.append(KeepTogether(trend_block))
        request_block: list[Flowable] = [
            _paragraph("Запросы за период", _HEADINGS[2]),
            _paragraph(f"{dates}   ·   Ответили игроков: {snapshot['unique']}", _SUB),
        ]
        if any(snapshot["requests"].values()) or any(snapshot["personal_requests"].values()):
            request_block.append(
                charts.requests_chart(
                    snapshot["requests"],
                    _WIDTH,
                    player=bool(player),
                    personal=snapshot["personal_requests"],
                )
            )
        else:
            request_block.append(_paragraph("Нет запросов"))
        doc.append(KeepTogether(request_block))
        if player:
            _details(doc, snapshot)
    else:
        _banner(doc, title.upper(), name or snapshot["squad"], "За выбранный период матчей нет.")
    output = io.BytesIO()

    def footer(canvas: Any, document: Any) -> None:
        canvas.saveState()
        canvas.setFont("DejaVuSans", 8)
        canvas.setFillColor(_MUTED)
        canvas.drawString(36, 20, "Данные на " + as_of)
        canvas.drawRightString(A4[0] - 36, 20, f"{title}   ·   {document.page}")
        canvas.restoreState()

    SimpleDocTemplate(
        output,
        pagesize=A4,
        leftMargin=36,
        rightMargin=36,
        topMargin=25,
        bottomMargin=36,
        title=title,
    ).build(doc, onFirstPage=footer, onLaterPages=footer)
    return output.getvalue()
