"""Typed persistence records live in models; Database is the repository boundary."""
from .database import Database

__all__ = ["Database"]
