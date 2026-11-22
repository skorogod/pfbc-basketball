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
        assert "Кол-во ответивших игроков" in texts[0]
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
