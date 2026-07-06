"""«Человеческий» слой данных: избранное, участие в торгах, заметки, рекомендации ИИ.

Это корпус RAG для ИИ-экономиста (docs/analytics-brief.md): всё, что бюро вводит
из веба, немедленно доступно отбору кейсов — переиндексация не нужна, кейсы
собираются SQL-ом в момент запроса.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Sequence
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from tender_ingest.db.models import (
    AiRecommendation,
    RecommendationFeedback,
    TenderFavorite,
    TenderNote,
    TenderParticipation,
)

PARTICIPATION_STATUSES = ("applied", "rejected", "lost", "won")
STATUS_LABELS = {
    "applied": "подали заявку",
    "rejected": "не допущены",
    "lost": "проиграли",
    "won": "выиграли",
}


class TrackingRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    # --- избранное ---

    def is_favorite(self, reestr_number: str) -> bool:
        return self.session.get(TenderFavorite, reestr_number) is not None

    def toggle_favorite(self, reestr_number: str) -> bool:
        """Переключить звёздочку. Возвращает новое состояние (True — в избранном)."""
        existing = self.session.get(TenderFavorite, reestr_number)
        if existing is not None:
            self.session.delete(existing)
            self.session.commit()
            return False
        self.session.add(TenderFavorite(reestr_number=reestr_number))
        self.session.commit()
        return True

    def favorites_among(self, numbers: Iterable[str]) -> set[str]:
        """Какие из номеров в избранном — для звёздочек в списке одной выборкой."""
        nums = list(numbers)
        if not nums:
            return set()
        rows = self.session.execute(
            select(TenderFavorite.reestr_number).where(TenderFavorite.reestr_number.in_(nums))
        ).scalars()
        return set(rows)

    # --- участие ---

    def get_participation(self, reestr_number: str) -> TenderParticipation | None:
        return self.session.get(TenderParticipation, reestr_number)

    def upsert_participation(
        self,
        reestr_number: str,
        *,
        status: str,
        our_price: Decimal | None,
        winner_price: Decimal | None,
        decided_at: dt.date | None,
        comment: str | None,
    ) -> None:
        stmt = insert(TenderParticipation).values(
            reestr_number=reestr_number,
            status=status,
            our_price=our_price,
            winner_price=winner_price,
            decided_at=decided_at,
            comment=comment,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[TenderParticipation.reestr_number],
            set_={
                "status": stmt.excluded.status,
                "our_price": stmt.excluded.our_price,
                "winner_price": stmt.excluded.winner_price,
                "decided_at": stmt.excluded.decided_at,
                "comment": stmt.excluded.comment,
            },
        )
        self.session.execute(stmt)
        self.session.commit()

    def delete_participation(self, reestr_number: str) -> None:
        self.session.execute(
            delete(TenderParticipation).where(TenderParticipation.reestr_number == reestr_number)
        )
        self.session.commit()

    # --- заметки ---

    def list_notes(self, reestr_number: str) -> Sequence[TenderNote]:
        return (
            self.session.execute(
                select(TenderNote)
                .where(TenderNote.reestr_number == reestr_number)
                .order_by(TenderNote.created_at.desc())
            )
            .scalars()
            .all()
        )

    def add_note(self, reestr_number: str, text: str) -> None:
        self.session.add(TenderNote(reestr_number=reestr_number, text=text))
        self.session.commit()

    def delete_note(self, reestr_number: str, note_id: int) -> None:
        self.session.execute(
            delete(TenderNote).where(
                TenderNote.id == note_id, TenderNote.reestr_number == reestr_number
            )
        )
        self.session.commit()

    # --- рекомендации ИИ-экономиста ---

    def latest_recommendation(self, reestr_number: str) -> AiRecommendation | None:
        return self.session.execute(
            select(AiRecommendation)
            .where(AiRecommendation.reestr_number == reestr_number)
            .order_by(AiRecommendation.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()

    def add_recommendation(
        self, reestr_number: str, *, model: str, recommendation: dict[str, object]
    ) -> None:
        self.session.add(
            AiRecommendation(
                reestr_number=reestr_number, model=model, recommendation=recommendation
            )
        )
        self.session.commit()

    def feedback_for(self, recommendation_id: int) -> Sequence[RecommendationFeedback]:
        return (
            self.session.execute(
                select(RecommendationFeedback)
                .where(RecommendationFeedback.recommendation_id == recommendation_id)
                .order_by(RecommendationFeedback.created_at.desc())
            )
            .scalars()
            .all()
        )

    def add_feedback(self, recommendation_id: int, *, useful: bool, comment: str | None) -> None:
        self.session.add(
            RecommendationFeedback(
                recommendation_id=recommendation_id, useful=useful, comment=comment
            )
        )
        self.session.commit()

    def recent_misses(self, limit: int = 3) -> list[tuple[AiRecommendation, str | None]]:
        """Последние рекомендации с фидбеком «мимо» — контрпримеры для калибровки промпта."""
        rows = self.session.execute(
            select(AiRecommendation, RecommendationFeedback.comment)
            .join(
                RecommendationFeedback,
                RecommendationFeedback.recommendation_id == AiRecommendation.id,
            )
            .where(RecommendationFeedback.useful.is_(False))
            .order_by(RecommendationFeedback.created_at.desc())
            .limit(limit)
        ).all()
        return [(rec, comment) for rec, comment in rows]
