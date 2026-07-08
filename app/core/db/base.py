from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    """
    Base class for all SQLAlchemy ORM models.

    SQLAlchemy 2.x 用 DeclarativeBase (不是舊的 declarative_base() 函式)。
    所有 model 都 `class User(Base): ...` 從這繼承。
    """
    pass