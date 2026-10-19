from __future__ import annotations

from typing import Any


class ApiError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        reasons: list[dict[str, str]] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.reasons = reasons or []
        self.extra = extra or {}

    def body(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message, "reasons": self.reasons, **self.extra}}


def not_found(what: str = "Resource") -> ApiError:
    # Same response for "missing" and "belongs to another business" to avoid tenant probing.
    return ApiError(404, "NOT_FOUND", f"{what} not found.")
