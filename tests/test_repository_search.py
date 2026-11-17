from sqlalchemy import select
from sqlalchemy.orm import Session

from models.db import Author, Paper, SearchResult, SearchSession
from models.domain import PaperRecord
from repositories.database import create_database_engine
from repositories.library import LibraryRepository
from repositories.search import SearchRepository


def test_save_search_upserts_by_doi_and_persists_authors() -> None:
    engine = create_database_engine("sqlite:///:memory:")
    repository = SearchRepository(engine)
    first = PaperRecord(
        source_api="openalex",
        source_id="W1",
        doi="10.1000/shared",
        title="Shared Paper",
        authors=["Author A"],
        metadata_sources=["openalex"],
    )
    second = PaperRecord(
        source_api="crossref",
        source_id="10.1000/shared",
        doi="https://doi.org/10.1000/shared",
        title="Shared Paper",
        authors=["Author B"],
        metadata_sources=["crossref"],
    )
    first_session_id = repository.save_search("first", [first])
    second_session_id = repository.save_search("second", [second])

    with Session(engine) as session:
        assert session.scalar(select(SearchSession).where(SearchSession.id == first_session_id))
        assert session.scalar(select(SearchSession).where(SearchSession.id == second_session_id))
        assert session.scalar(select(SearchResult)) is not None
        papers = session.scalars(select(Paper)).all()
        assert len(papers) == 1
        assert papers[0].metadata_sources == ["openalex", "crossref"]
        assert {author.display_name for author in papers[0].authors} == {"Author A", "Author B"}


def test_accumulated_searches_preserve_previous_papers_and_support_deletion() -> None:
    engine = create_database_engine("sqlite:///:memory:")
    search_repo = SearchRepository(engine)
    library_repo = LibraryRepository(engine)

    p1 = PaperRecord(
        source_api="openalex",
        source_id="W1",
        doi="10.1000/p1",
        title="Paper One",
        authors=["Alice"],
        quality_accepted=True,
        quality_bucket="priority",
        relevance_score=0.9,
    )
    p2 = PaperRecord(
        source_api="openalex",
        source_id="W2",
        doi="10.1000/p2",
        title="Paper Two",
        authors=["Bob"],
        quality_accepted=False,
        quality_bucket="excluded",
        relevance_score=0.3,
    )
    p1_repeat_lower = PaperRecord(
        source_api="crossref",
        source_id="10.1000/p1",
        doi="10.1000/p1",
        title="Paper One",
        authors=["Alice", "Charlie"],
        quality_accepted=False,
        quality_bucket="excluded",
        relevance_score=0.2,
    )
    p3 = PaperRecord(
        source_api="semantic_scholar",
        source_id="S3",
        doi="10.1000/p3",
        title="Paper Three",
        authors=["Diana"],
        quality_accepted=True,
        quality_bucket="reference",
        relevance_score=0.7,
    )

    s1 = search_repo.save_search("query 1", [p1, p2])
    s2 = search_repo.save_search("query 2", [p1_repeat_lower, p3])

    all_papers = library_repo.list_papers(sort_by="newest")
    assert len(all_papers) == 3
    by_doi = {row.paper.doi: row for row in all_papers}
    assert by_doi["10.1000/p1"].paper.quality_bucket == "priority"
    assert by_doi["10.1000/p1"].paper.relevance_score == 0.9
    assert set(by_doi["10.1000/p1"].paper.authors) == {"Alice", "Charlie"}

    # Delete a single paper (Paper Two)
    deleted_count = library_repo.delete_papers([by_doi["10.1000/p2"].paper_id])
    assert deleted_count == 1
    assert len(library_repo.list_papers()) == 2

    # Delete session 2 -> removes Paper Three (only in session 2), keeps Paper One (still in session 1)
    library_repo.delete_session(s2)
    remaining = library_repo.list_papers()
    assert [row.paper.doi for row in remaining] == ["10.1000/p1"]
    assert s1 in [item["id"] for item in library_repo.recent_sessions()]

    # Clear all accumulated results
    cleared = library_repo.clear_all()
    assert cleared == 1
    assert library_repo.list_papers() == []
    assert library_repo.recent_sessions() == []
    with Session(engine) as session:
        assert session.scalars(select(Author)).all() == []
