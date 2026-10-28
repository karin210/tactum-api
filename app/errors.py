import logging
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)

GENERIC_ERROR_MESSAGE = "Something went wrong on our side. Please try again in a moment."


class ApiError(Exception):
    """An expected failure with a status code and a message that is safe to show to users.

    `code` is a stable identifier the frontend can map to a translated message.
    """

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.headers = headers


class RequestIdMiddleware:
    """Gives every request a short ID that is logged and returned in the X-Request-ID header."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = uuid4().hex[:12]
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message).append("X-Request-ID", request_id)
            await send(message)

        await self.app(scope, receive, send_with_request_id)


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    field_errors: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body: dict[str, object] = {"code": code, "message": message, "requestId": _request_id(request)}
    if field_errors:
        body["fieldErrors"] = field_errors
    return JSONResponse(body, status_code=status_code, headers=headers)


async def _handle_api_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    return _error_response(request, exc.status_code, exc.code, exc.message, headers=exc.headers)


async def _handle_validation_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    field_errors: dict[str, str] = {}
    for error in exc.errors():
        # Drop the "body"/"query" prefix so the key matches the field name the client sent.
        location = [str(part) for part in error["loc"] if part not in ("body", "query", "path")]
        field_errors.setdefault(".".join(location) or "request", error["msg"])
    return _error_response(
        request, 422, "validation_error", "Some fields are invalid.", field_errors
    )


async def _handle_http_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    messages = {404: "This resource does not exist.", 405: "This method is not allowed here."}
    message = messages.get(exc.status_code, "The request could not be processed.")
    return _error_response(request, exc.status_code, "http_error", message)


async def _handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    logger.exception(
        "Unhandled error on %s %s (request_id=%s)",
        request.method,
        request.url.path,
        _request_id(request),
    )
    return _error_response(request, 500, "internal_error", GENERIC_ERROR_MESSAGE)


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _handle_api_error)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_error)
    app.add_exception_handler(Exception, _handle_unexpected_error)
