from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import CreatedMixin, IdentityMixin, Model, UpdatedMixin, UTCDateTime


class QuestionCategory(IdentityMixin, CreatedMixin, UpdatedMixin, Model):
    __tablename__ = "question_categories"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Question(IdentityMixin, CreatedMixin, Model):
    __tablename__ = "questions"
    __table_args__ = (
        UniqueConstraint("question_key", "version"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            "answer_type IN ('single_choice','multiple_choice','text')",
            name="answer_type",
        ),
    )

    question_key: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    category_id: Mapped[str] = mapped_column(
        ForeignKey("question_categories.id"), nullable=False, index=True
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    answer_type: Mapped[str] = mapped_column(String(24), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    options: Mapped[list[QuestionOption]] = relationship(
        back_populates="question",
        order_by="QuestionOption.sort_order",
        passive_deletes="all",
    )


class QuestionOption(IdentityMixin, Model):
    __tablename__ = "question_options"

    question_id: Mapped[str] = mapped_column(
        ForeignKey("questions.id"), nullable=False, index=True
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    question: Mapped[Question] = relationship(back_populates="options")


class GlossaryTerm(IdentityMixin, CreatedMixin, UpdatedMixin, Model):
    __tablename__ = "glossary_terms"

    term: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)


class QuestionGlossaryTerm(Model):
    __tablename__ = "question_glossary_terms"

    question_id: Mapped[str] = mapped_column(
        ForeignKey("questions.id"), primary_key=True, nullable=False
    )
    glossary_term_id: Mapped[str] = mapped_column(
        ForeignKey("glossary_terms.id"), primary_key=True, nullable=False
    )
