from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from models.domain import PaperRecord
from repositories.database import create_database_engine
from repositories.library import LibraryRepository
from repositories.search import SearchRepository


def test_library_selection_tracks_sqlite_paper_id_across_rerun(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'ui.db'}"
    monkeypatch.setenv("LAW_RESEARCH_DATABASE_URL", database_url)
    monkeypatch.setenv("LAW_RESEARCH_DOWNLOAD_DIR", str(tmp_path / "downloads"))
    engine = create_database_engine(database_url)
    SearchRepository(engine).save_search(
        "selection",
        [
            PaperRecord(source_api="test", source_id="one", title="Paper One"),
            PaperRecord(source_api="test", source_id="two", title="Paper Two"),
        ],
    )
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "app" / "main.py"
    ).run()
    app.radio[0].set_value("Paper Library").run()
    app.checkbox[1].set_value(True).run()
    assert app.session_state["selected_paper_ids"] == {1}
    app.radio[0].set_value("Research Dashboard").run()
    app.radio[0].set_value("Paper Library").run()
    assert app.session_state["selected_paper_ids"] == {1}
    assert app.checkbox[1].value is True


def test_empty_dashboard_button_opens_academic_search(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'empty.db'}"
    monkeypatch.setenv("LAW_RESEARCH_DATABASE_URL", database_url)
    monkeypatch.setenv("LAW_RESEARCH_DOWNLOAD_DIR", str(tmp_path / "downloads"))
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "app" / "main.py"
    ).run()

    assert app.button[0].label == "Mở Academic Search"
    app.button[0].click().run()

    assert not app.exception
    assert app.session_state["active_page"] == "Academic Search"
    assert app.header[0].value == "Academic Search"


def test_academic_search_button_shows_validation_feedback(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'search.db'}"
    monkeypatch.setenv("LAW_RESEARCH_DATABASE_URL", database_url)
    monkeypatch.setenv("LAW_RESEARCH_DOWNLOAD_DIR", str(tmp_path / "downloads"))
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "app" / "main.py"
    ).run()
    app.radio[0].set_value("Academic Search").run()

    app.text_input[0].set_value("").run()
    app.button[0].click().run()

    assert not app.exception
    assert any("Enter a research topic" in item.value for item in app.error)


def test_academic_search_reloads_accumulated_results_and_can_clear(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'accumulated.db'}"
    monkeypatch.setenv("LAW_RESEARCH_DATABASE_URL", database_url)
    monkeypatch.setenv("LAW_RESEARCH_DOWNLOAD_DIR", str(tmp_path / "downloads"))
    engine = create_database_engine(database_url)
    repo = SearchRepository(engine)
    repo.save_search(
        "first query",
        [PaperRecord(source_api="test", source_id="p1", title="First Saved Paper")],
    )
    repo.save_search(
        "second query",
        [PaperRecord(source_api="test", source_id="p2", title="Second Saved Paper")],
    )

    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "app" / "main.py"
    ).run()
    app.radio[0].set_value("Academic Search").run()

    assert not app.exception
    assert len(app.dataframe) == 1
    assert len(app.dataframe[0].value) == 2

    clear_btn = next(
        btn for btn in app.button if btn.label == "Xoá toàn bộ kết quả đã lưu"
    )
    clear_btn.click().run()
    assert not app.exception
    assert LibraryRepository(engine).list_papers() == []


def test_library_export_produces_csv_and_xlsx(tmp_path: Path) -> None:
    from app.main import _export_bytes

    database_url = f"sqlite:///{tmp_path / 'export.db'}"
    engine = create_database_engine(database_url)
    SearchRepository(engine).save_search(
        "export",
        [PaperRecord(source_api="test", source_id="one", title="Export paper")],
    )
    rows = LibraryRepository(engine).list_papers()
    csv_bytes, xlsx_bytes = _export_bytes(rows)
    assert b"paper_id" in csv_bytes
    assert xlsx_bytes[:2] == b"PK"
