import io

from pypdf import PdfReader

from pfbc_bot.services.survey_charts import zone
from pfbc_bot.services.survey_documents import make_snapshot, render_report, summarize


def test_pdf_pagination_and_visibility() -> None:
    comment = "Особое <наблюдение> & совет\n" * 40 + "Конец комментария"
    rows = [
        {
            "name": f"Длиннаяфамилия-Длиннаяфамилия Игрок {i}",
            "id": i,
            "status": "submitted",
            "answers": {"1": "5", "4": "4", "7": comment if i == 1 else ""},
        }
        for i in range(50)
    ]
    game = {
        "date": "22.11.2026",
        "squad": "Состав",
        "opponent": "Соперник",
        "stats": summarize(rows),
        "rows": rows,
    }
    for visibility in ("group", "staff", "player"):
        player = 1 if visibility == "player" else 0
        snapshot = make_snapshot(
            [game], visibility, "Состав", 1, player=player, player_name="Игрок" if player else ""
        )
        pages = PdfReader(io.BytesIO(render_report(snapshot, "pdf", "22.11.2026"))).pages
        texts = [page.extract_text() for page in pages]
        assert ("Кол-во ответивших игроков" in texts[0]) == (visibility != "player")
        if player:
            assert "Анкета за последний матч" in texts[0]
            content = "\n".join(texts)
            assert content.index("Анкета за последний матч") < content.index(
                "Игрок и команда за матч"
            )
            assert content.index("Анкеты игрока за период") < content.index("Динамика показателей")
            assert "Запросы команды за матч" not in content
            last_section, period_section = content.split("ДИНАМИКА ЗА ПЕРИОД", 1)
            assert last_section.index("Игрок и команда за матч") < last_section.index("Комментарии")
            assert period_section.index("Запросы игрока за период") < period_section.index(
                "Комментарии"
            )
        period_pages = [page for page in pages if "ДИНАМИКА ЗА ПЕРИОД" in page.extract_text()]
        assert len(period_pages) == 1 and "ДИНАМИКА ЗА ПЕРИОД" not in texts[0]
        assert (
            period_pages[0]
            .extract_text(extraction_mode="layout")
            .lstrip()
            .startswith("ДИНАМИКА ЗА ПЕРИОД")
        )
        content = "\n".join(texts)
        assert ("Конец комментария" in content) == (visibility != "group")
        assert ("<наблюдение> & совет" in content) == (visibility != "group")
        assert ("Игрок49" in "".join(content.split())) == (visibility == "staff")
        if visibility == "staff":
            assert sum("ФИО" in page for page in texts) >= 2


def test_counts_players_once_per_skill_and_separates_own_votes() -> None:
    rows = [
        {
            "id": uid,
            "name": str(uid),
            "status": "submitted",
            "answers": {"1": "8", "4": "3", "5": ["shot"], "6": ["help"]},
        }
        for uid in (1, 2)
    ]
    games = [{"rows": rows, "stats": summarize(rows)} for _ in range(3)]
    player = make_snapshot(games, "player", "Команда", 3, player=1)
    group = make_snapshot(games, "group", "Команда", 3)
    assert player["requests"]["5"] == {"shot": 1}
    assert player["personal_requests"]["5"] == {"shot": 3}
    assert group["requests"]["5"] == {"shot": 2} and group["unique"] == 2
    assert all(not game["rows"] for game in group["games"])


def test_load_zone_boundaries() -> None:
    for scores, label in [
        ((1, 3, 3.49), "Лёгкая"),
        ((3.5, 4, 7, 7.49), "Умеренная"),
        ((7.5, 8, 10), "Высокая"),
    ]:
        assert all(zone(value)[2] == label for value in scores)


def test_playing_time_counts_charts_and_export(monkeypatch) -> None:
    from pfbc_bot.services import survey_charts as charts

    rows = [
        {
            "id": i,
            "name": str(i),
            "status": status,
            "answers": {
                "1": "5",
                "4": "4",
                **({"playing_time_satisfaction": code} if code else {}),
            },
        }
        for i, (status, code) in enumerate(
            [
                ("submitted", "more"),
                ("submitted", "more"),
                ("submitted", "less"),
                ("submitted", "satisfied"),
                ("submitted", None),
                ("started", "less"),
            ]
        )
    ]
    game = {
        "date": "23.11.2026",
        "squad": "Состав",
        "opponent": "Длинный соперник",
        "rows": rows,
        "stats": summarize(rows),
    }
    assert game["stats"]["playing_time"] == {"more": 2, "less": 1, "satisfied": 1}
    snapshot = make_snapshot([game], "staff", "Состав", 1)
    csv = render_report(snapshot, "csv", "сейчас").decode("utf-8-sig")
    assert "Удовлетворённость сыгранным временем" in csv
    assert "Хочу играть больше" in csv and "Нет данных" in csv
    assert render_report(snapshot, "pdf", "сейчас").startswith(b"%PDF")
    monkeypatch.setattr(charts, "_image", lambda fig: fig)
    fig = charts.distribution(game, 510)
    assert [p.get_height() for p in fig.axes[2].patches] == [2, 1, 1]
    assert [p.get_facecolor() for p in fig.axes[2].patches] == [
        fig.axes[1].patches[i].get_facecolor() for i in (0, 2, 4)
    ]
    fig = charts.trends([game], "", 510)
    assert [p.get_height() for p in fig.axes[2].patches] == [50, 25, 25]
    assert all(not p.get_hatch() for p in fig.axes[2].patches)
    assert [t.get_text() for t in fig.axes[2].texts if "%" in t.get_text()] == ["50%", "25%", "25%"]
    assert [(p.get_y(), p.get_height()) for p in fig.axes[1].patches] == [(1, 1), (2, 2), (4, 1)]
    assert fig.axes[0].get_subplotspec().colspan.stop == 2


def test_small_playing_time_segments_have_callouts(monkeypatch) -> None:
    from matplotlib.text import Annotation

    from pfbc_bot.services import survey_charts as charts

    monkeypatch.setattr(charts, "_image", lambda fig: fig)
    games = [
        {
            "date": "23.11.2026",
            "opponent": "Атлант",
            "rows": [],
            "stats": {"scores": {"1": [5], "4": [4]}, "playing_time": counts},
        }
        for counts in [
            {"more": 1, "less": 1, "satisfied": 998},
            {"satisfied": 1},
            {},
            {"more": 1, "less": 1, "satisfied": 1},
        ]
    ]
    fig = charts.trends(games, "", 510)
    labels = fig.axes[2].texts
    callouts = [t for t in labels if isinstance(t, Annotation)]
    assert len(callouts) == 2
    assert all(t.get_text() == "<1%" and t.arrow_patch for t in callouts)
    assert callouts[0].get_position()[1] < callouts[1].get_position()[1]
    assert "100%" in [t.get_text() for t in labels]
    assert "Нет\nданных" in [t.get_text() for t in labels]
    assert "0%" not in [t.get_text() for t in labels]


def test_personal_cards_markers_and_requests(monkeypatch) -> None:
    from pfbc_bot.services import survey_charts as charts
    from pfbc_bot.services.survey_documents import _player_metrics

    games = []
    for code in ("more", "less", "satisfied"):
        rows = [
            {
                "id": 1,
                "name": "Игрок",
                "status": "submitted",
                "answers": {"1": "6", "4": "3", "playing_time_satisfaction": code},
            }
        ]
        stats = summarize(rows)
        stats["playing_time"] = {"more": 2, "less": 1, "satisfied": 1}
        games.append({"date": "23.11.2026", "opponent": "Атлант", "rows": rows, "stats": stats})
    cards = _player_metrics(games, period=True)
    texts = [item.text for item in cards.contents if hasattr(item, "text")]
    assert sum("33%" in text for text in texts) == 3
    assert "6.0 / 10" in texts and "3.0 / 5" in texts
    monkeypatch.setattr(charts, "_image", lambda fig: fig)
    fig = charts.trends(games, "Игрок", 510)
    assert [line.get_ydata()[0] for line in fig.axes[2].lines] == [25, 62.5, 87.5]
    fig = charts.distribution(games[0], 510, player=True)
    assert [ax.lines[0].get_xdata()[0] for ax in fig.axes] == [6, 3, 0]
    games[0]["rows"][0]["status"] = "started"
    fig = charts.distribution(games[0], 510, player=True)
    assert all(not ax.lines for ax in fig.axes)
    fig = charts.requests_chart(
        {"5": {"pass": 20}, "6": {"talk": 30}},
        510,
        player=True,
        personal={"5": {"shot": 2}, "6": {"help": 1}},
    )
    assert len(fig.axes) == 2
    assert [[bar.get_width() for bar in ax.patches] for ax in fig.axes] == [[2], [1]]


def test_unsubmitted_answers_are_dashes_in_tables() -> None:
    import csv

    from pfbc_bot.services.survey_documents import _details

    row = {
        "id": 1,
        "name": "Игрок",
        "status": "started",
        "answers": {"1": "8", "7": "Черновик комментария"},
    }
    game = {"date": "23.11.2026", "opponent": "Атлант", "rows": [row]}
    snapshot = {"games": [game], "player": 1}
    records = list(csv.reader(io.StringIO(render_report(snapshot, "csv", "").decode("utf-8-sig"))))
    assert "Статус" not in records[0]
    assert records[1][4:] == ["—"] * 8
    doc = []
    _details(doc, snapshot)
    table = doc[1]
    assert "Статус" not in [cell.getPlainText() for cell in table._cellvalues[0]]
    assert [cell.getPlainText() for cell in table._cellvalues[1][1:]] == ["—"] * 7
    assert len(doc) == 2  # No draft comments after the table.
