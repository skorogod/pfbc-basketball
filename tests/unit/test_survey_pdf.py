import io

from pypdf import PdfReader

from pfbc_bot.services.survey_documents import render_report, summarize


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
    for visibility in ("group", "staff", "sensitive"):
        snapshot = {
            "title": "Отчёт за матч",
            "history": False,
            "games": [game],
            "visibility": visibility,
        }
        pages = PdfReader(io.BytesIO(render_report(snapshot, "pdf", "22.11.2026"))).pages
        texts = [page.extract_text() for page in pages]
        content = "\n".join(texts)
        assert ("Конец комментария" in content) == (visibility == "sensitive")
        assert ("<наблюдение> & совет" in content) == (visibility == "sensitive")
        assert ("Игрок49" in "".join(content.split())) == (visibility != "group")
        if visibility != "group":
            assert sum("ФИО" in page for page in texts) >= 2
