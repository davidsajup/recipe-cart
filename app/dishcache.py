"""Cache of LLM-parsed dishes, so a repeated dish costs no API call.

Only successful, validated parses are stored. `refresh=True` re-asks the model
and overwrites the stored copy (the escape hatch for a bad parse).
"""
import re
from datetime import datetime, timezone
from typing import Callable

from pydantic import ValidationError
from sqlalchemy import Column, DateTime
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Field, Session, SQLModel, select

from .schemas import ParsedDish


class ParsedDishRow(SQLModel, table=True):
    __tablename__ = "parsed_dishes"

    id: int | None = Field(default=None, primary_key=True)
    key: str = Field(unique=True, index=True)
    data: str  # ParsedDish as JSON
    model: str = ""
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


def dish_key(dish: str) -> str:
    """'  Fish   MOLEE! ' -> 'fish molee'"""
    return " ".join(re.sub(r"[^\w\s]", " ", dish.lower()).split())


def get_cached(session: Session, key: str) -> ParsedDish | None:
    row = session.exec(select(ParsedDishRow).where(ParsedDishRow.key == key)).first()
    if row is None:
        return None
    try:
        return ParsedDish.model_validate_json(row.data)
    except ValidationError:
        return None  # schema changed since it was stored: treat as a miss


def save_cached(session: Session, key: str, parsed: ParsedDish, model: str = "") -> None:
    row = session.exec(select(ParsedDishRow).where(ParsedDishRow.key == key)).first()
    if row is None:
        row = ParsedDishRow(key=key, data=parsed.model_dump_json(), model=model)
    else:
        row.data, row.model = parsed.model_dump_json(), model
        row.created_at = datetime.now(timezone.utc)
    session.add(row)
    try:
        session.commit()
    except IntegrityError:  # another request stored it first; theirs is fine
        session.rollback()


def parse_cached(
    session: Session,
    dish: str,
    parse: Callable[[str], ParsedDish],
    *,
    refresh: bool = False,
    is_builtin: Callable[[str], bool] = lambda _: False,
    model: str = "",
) -> tuple[ParsedDish, bool]:
    """Return (parsed, from_cache). A cache failure never fails the request."""
    key = dish_key(dish)
    if not refresh and not is_builtin(dish):
        try:
            hit = get_cached(session, key)
        except SQLAlchemyError:
            session.rollback()
            hit = None
        if hit is not None:
            return hit, True

    parsed = parse(dish)  # may raise; failures are never cached

    if not is_builtin(dish):
        try:
            save_cached(session, key, parsed, model)
        except SQLAlchemyError:
            session.rollback()
    return parsed, False
