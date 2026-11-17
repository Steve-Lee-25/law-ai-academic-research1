from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from models.db import (
    Author,
    DownloadedFile,
    DownloadTask,
    Paper,
    PaperAuthor,
    SearchResult,
    SearchSession,
)
from models.domain import PaperRecord

_BUCKET_PRIORITY = {
    "priority": 3,
    "reference": 2,
    "excluded": 1,
}


class SearchRepository:
    """SQLite persistence for canonical papers and reproducible search runs."""

    def __init__(self, engine: Any) -> None:
        self.engine = engine

    def save_search(
        self,
        query: str,
        papers: list[PaperRecord],
        *,
        parameters: dict[str, Any] | None = None,
    ) -> int:
        with Session(self.engine) as session:
            search_session = SearchSession(
                query=query,
                parameters=parameters or {},
                created_at=datetime.utcnow(),
            )
            session.add(search_session)
            session.flush()
            seen_paper_ids: set[int] = set()
            for rank, record in enumerate(papers, start=1):
                paper = self._find_existing(session, record)
                if paper is None:
                    paper = Paper(
                        source_api=record.source_api,
                        source_id=record.source_id,
                    )
                    session.add(paper)
                self._apply_record(paper, record)
                session.flush()
                self._attach_authors(session, paper, record.authors)
                if paper.id not in seen_paper_ids:
                    seen_paper_ids.add(paper.id)
                    session.add(
                        SearchResult(
                            session_id=search_session.id,
                            paper_id=paper.id,
                            rank=rank,
                        )
                    )
            session.commit()
            return int(search_session.id)

    def delete_papers(self, paper_ids: Iterable[int]) -> int:
        ids = [int(pid) for pid in set(paper_ids)]
        if not ids:
            return 0
        with Session(self.engine) as session:
            task_ids = list(
                session.scalars(
                    select(DownloadTask.id).where(DownloadTask.paper_id.in_(ids))
                ).all()
            )
            if task_ids:
                session.execute(
                    delete(DownloadedFile).where(DownloadedFile.task_id.in_(task_ids))
                )
                session.execute(
                    delete(DownloadTask).where(DownloadTask.id.in_(task_ids))
                )
            session.execute(
                delete(SearchResult).where(SearchResult.paper_id.in_(ids))
            )
            session.execute(
                delete(PaperAuthor).where(PaperAuthor.paper_id.in_(ids))
            )
            result = session.execute(
                delete(Paper).where(Paper.id.in_(ids))
            )
            self._cleanup_orphan_sessions_and_authors(session)
            session.commit()
            return int(result.rowcount or 0)

    def delete_session(
        self,
        session_id: int,
        *,
        remove_unreferenced_papers: bool = True,
    ) -> int:
        with Session(self.engine) as session:
            session_paper_ids = list(
                session.scalars(
                    select(SearchResult.paper_id).where(
                        SearchResult.session_id == int(session_id)
                    )
                ).all()
            )
            session.execute(
                delete(SearchResult).where(SearchResult.session_id == int(session_id))
            )
            session.execute(
                delete(SearchSession).where(SearchSession.id == int(session_id))
            )
            deleted_papers = 0
            if remove_unreferenced_papers and session_paper_ids:
                still_referenced = set(
                    session.scalars(
                        select(SearchResult.paper_id).where(
                            SearchResult.paper_id.in_(session_paper_ids)
                        )
                    ).all()
                )
                orphan_paper_ids = [
                    pid for pid in session_paper_ids if pid not in still_referenced
                ]
                if orphan_paper_ids:
                    task_ids = list(
                        session.scalars(
                            select(DownloadTask.id).where(
                                DownloadTask.paper_id.in_(orphan_paper_ids)
                            )
                        ).all()
                    )
                    if task_ids:
                        session.execute(
                            delete(DownloadedFile).where(
                                DownloadedFile.task_id.in_(task_ids)
                            )
                        )
                        session.execute(
                            delete(DownloadTask).where(DownloadTask.id.in_(task_ids))
                        )
                    session.execute(
                        delete(PaperAuthor).where(
                            PaperAuthor.paper_id.in_(orphan_paper_ids)
                        )
                    )
                    res = session.execute(
                        delete(Paper).where(Paper.id.in_(orphan_paper_ids))
                    )
                    deleted_papers = int(res.rowcount or 0)
            self._cleanup_orphan_sessions_and_authors(session)
            session.commit()
            return deleted_papers

    def clear_all(self) -> int:
        with Session(self.engine) as session:
            session.execute(delete(DownloadedFile))
            session.execute(delete(DownloadTask))
            session.execute(delete(SearchResult))
            session.execute(delete(SearchSession))
            session.execute(delete(PaperAuthor))
            result = session.execute(delete(Paper))
            session.execute(delete(Author))
            session.commit()
            return int(result.rowcount or 0)

    @staticmethod
    def _cleanup_orphan_sessions_and_authors(session: Session) -> None:
        referenced_session_ids = select(SearchResult.session_id).distinct()
        session.execute(
            delete(SearchSession).where(
                ~SearchSession.id.in_(referenced_session_ids)
            )
        )
        referenced_author_ids = select(PaperAuthor.author_id).distinct()
        session.execute(
            delete(Author).where(~Author.id.in_(referenced_author_ids))
        )

    @staticmethod
    def _find_existing(session: Session, record: PaperRecord) -> Paper | None:
        if record.doi:
            found = session.scalar(select(Paper).where(Paper.doi == record.doi))
            if found:
                return found
        found = session.scalar(
            select(Paper).where(
                Paper.source_api == record.source_api,
                Paper.source_id == record.source_id,
            )
        )
        if found:
            return found
        candidates = session.scalars(select(Paper).where(Paper.title == record.title)).all()
        compatible = [
            candidate
            for candidate in candidates
            if (candidate.doi is None or candidate.doi == record.doi)
            and (
                not candidate.publication_year
                or not record.publication_year
                or candidate.publication_year == record.publication_year
            )
        ]
        if len(compatible) == 1:
            return compatible[0]
        return None

    @staticmethod
    def _apply_record(paper: Paper, record: PaperRecord) -> None:
        paper.doi = record.doi or paper.doi
        paper.title = record.title
        paper.abstract = record.abstract or paper.abstract
        paper.keywords = list(dict.fromkeys([*(paper.keywords or []), *record.keywords]))
        paper.publication_year = record.publication_year or paper.publication_year
        paper.publication_date = record.publication_date or paper.publication_date
        paper.journal_name = record.journal_name or paper.journal_name
        paper.publisher = record.publisher or paper.publisher
        paper.source_api = paper.source_api or record.source_api
        paper.source_id = paper.source_id or record.source_id
        paper.external_ids = {**(paper.external_ids or {}), **record.external_ids}
        paper.metadata_sources = list(
            dict.fromkeys([*(paper.metadata_sources or []), *record.metadata_sources])
        )
        paper.issn = list(dict.fromkeys([*(paper.issn or []), *record.issn]))
        paper.citation_count = record.citation_count or paper.citation_count
        if record.quartile.value != "Unknown":
            paper.quartile = record.quartile.value
            paper.quartile_year = record.quartile_year or paper.quartile_year
            paper.ranking_source = record.ranking_source or paper.ranking_source
            paper.ranking_subject = record.ranking_subject or paper.ranking_subject
        if record.quality_accepted is not None:
            existing_rank = _BUCKET_PRIORITY.get(paper.quality_bucket or "", 0)
            incoming_rank = _BUCKET_PRIORITY.get(record.quality_bucket or "", 0)
            if incoming_rank >= existing_rank or paper.quality_accepted is None:
                paper.whitelist_matched = bool(
                    record.whitelist_matched or paper.whitelist_matched
                )
                paper.journal_match_method = (
                    record.journal_match_method or paper.journal_match_method
                )
                paper.matched_rule = record.matched_rule or paper.matched_rule
                paper.quality_accepted = record.quality_accepted
                paper.quality_bucket = record.quality_bucket or paper.quality_bucket
            else:
                paper.whitelist_matched = bool(
                    record.whitelist_matched or paper.whitelist_matched
                )
        if record.relevance_score is not None:
            if (
                paper.relevance_score is None
                or record.relevance_score >= paper.relevance_score
            ):
                paper.relevance_score = record.relevance_score
                paper.relevance_explanation = (
                    record.relevance_explanation or paper.relevance_explanation
                )
        paper.open_access_status = (
            record.open_access_status.value
            if record.open_access_status.value != "unknown"
            else paper.open_access_status
        )
        paper.pdf_url = record.pdf_url or paper.pdf_url
        paper.oa_locations = list(
            dict.fromkeys([*(paper.oa_locations or []), *record.oa_locations])
        )
        paper.landing_page_url = record.landing_page_url or paper.landing_page_url

    @staticmethod
    def _attach_authors(session: Session, paper: Paper, author_names: list[str]) -> None:
        existing = {author.display_name: author for author in paper.authors}
        for name in author_names:
            author = existing.get(name)
            if author is None:
                author = session.scalar(select(Author).where(Author.display_name == name))
            if author is None:
                author = Author(display_name=name)
                session.add(author)
                session.flush()
            if author not in paper.authors:
                paper.authors.append(author)
                existing[name] = author
