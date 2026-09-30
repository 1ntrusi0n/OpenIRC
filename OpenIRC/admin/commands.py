"""Stable command and error types for desktop and future adapters."""
from OpenIRC.models.domain import AdminCommand


class AdminError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


__all__ = ["AdminCommand", "AdminError"]
