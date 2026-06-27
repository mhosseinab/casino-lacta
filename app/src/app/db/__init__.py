"""Database layer (app member): SQLAlchemy 2.0 models + metadata.

SQLAlchemy lives ONLY here in ``app`` — never in ``engine`` (purity is a
dependency fact). The ORM ``Base.metadata`` is the single source of truth the
Alembic migrations target.
"""

from app.db.models import Base

__all__ = ["Base"]
