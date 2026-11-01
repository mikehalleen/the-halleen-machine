"""Error bodies: every failure is ``{"code", "detail", "extra"}`` with a machine-readable code."""

from __future__ import annotations

import json

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from thm_core import CommandRefused, ProjectNotFound, SchemaVersionError

REFUSED_STATUS = {"unknown_id": 404, "unknown_command": 404, "invalid_payload": 422, "bad_value": 422, "private_project": 403}
HTTP_CODES = {400: "bad_request", 404: "not_found", 405: "method_not_allowed", 413: "too_large", 415: "unsupported_media"}


class ApiError(Exception):
    def __init__(self, status: int, code: str, detail: str = "", extra: dict | None = None):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.status = status
        self.code = code
        self.detail = detail
        self.extra = extra or {}


def error_body(code: str, detail: str = "", extra: dict | None = None) -> dict:
    return {"code": code, "detail": detail, "extra": extra or {}}


def install_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError):
        return JSONResponse(error_body(exc.code, exc.detail, exc.extra), status_code=exc.status)

    @app.exception_handler(CommandRefused)
    async def _refused(request: Request, exc: CommandRefused):
        return JSONResponse(error_body(exc.code, exc.detail, exc.extra), status_code=REFUSED_STATUS.get(exc.code, 409))

    @app.exception_handler(ProjectNotFound)
    async def _missing(request: Request, exc: ProjectNotFound):
        return JSONResponse(error_body("unknown_project", str(exc)), status_code=404)

    @app.exception_handler(SchemaVersionError)
    async def _schema(request: Request, exc: SchemaVersionError):
        return JSONResponse(error_body("schema_version", str(exc)), status_code=409)

    @app.exception_handler(ValidationError)
    async def _validation(request: Request, exc: ValidationError):
        try:
            errors = json.loads(exc.json())
        except Exception:
            errors = [{"msg": str(exc)}]
        return JSONResponse(error_body("invalid_payload", "validation failed", {"errors": errors}), status_code=422)

    # FastAPI's own request validation (query params, multipart fields) and Starlette's
    # HTTP errors (bad multipart, unknown routes) would otherwise answer with a bare
    # {"detail": ...}; keep every body in the one shape.
    @app.exception_handler(RequestValidationError)
    async def _request_validation(request: Request, exc: RequestValidationError):
        return JSONResponse(
            error_body("invalid_payload", "request validation failed", {"errors": jsonable_encoder(exc.errors())}), status_code=422
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        code = HTTP_CODES.get(exc.status_code, "http_error")
        return JSONResponse(error_body(code, str(exc.detail or "")), status_code=exc.status_code, headers=exc.headers)
