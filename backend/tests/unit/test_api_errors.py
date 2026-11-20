"""Error paths of the API: bodies, statuses, request ids, access log, log privacy.

Covers Requirements 4.5 to 4.10 and 5.3 to 5.6. Every case goes through
``create_app`` with fakes and test-only routes added to the app instance. The
application's log output is read from the ``log_stream`` fixture; lines written by
the test client's own loggers are excluded (see ``tests.fakes_api.log_records``).
"""

import io
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from recromancer.api.app import create_app
from recromancer.api.errors import (
    MAX_MESSAGE_LENGTH,
    register_exception_handlers,
    validation_message,
)
from recromancer.domain.errors import ErrorCode, RecromancerError
from tests.fakes_api import (
    FakeClock,
    FakeProbe,
    app_output,
    counter_ids,
    log_records,
    records_for,
)

MARKER = "leak-marker-" + "q" * 8
TRACE = "trace-marker-" + "t" * 8
ACCESS = "recromancer.access"
ERRORS = "recromancer.api.errors"
PREFIX = "Request validation failed: "
INTERNAL = "An internal error occurred"

EXPECTED_STATUS = {
    ErrorCode.VALIDATION_ERROR: 422,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.METHOD_NOT_ALLOWED: 405,
    ErrorCode.CONFIG_INVALID: 500,
    ErrorCode.MIGRATION_FAILED: 500,
    ErrorCode.DEPENDENCY_UNAVAILABLE: 503,
    ErrorCode.INTERNAL_ERROR: 500,
}
ACCESS_KEYS = {
    "timestamp",
    "level",
    "logger",
    "message",
    "request_id",
    "method",
    "path",
    "status",
    "duration_ms",
}
ERROR_LOG_KEYS = {
    "timestamp",
    "level",
    "logger",
    "message",
    "exception",
    "request_id",
    "method",
    "path",
    "exception_class",
}


class Many(BaseModel):
    f0: int
    f1: int
    f2: int
    f3: int
    f4: int
    f5: int
    f6: int
    f7: int
    f8: int
    f9: int
    f10: int


class LongName(BaseModel):
    value: int = Field(alias="a" * 300)  # type: ignore[literal-required]


def message_for(code: ErrorCode) -> str:
    return f"message for {code.value}"


def build_app() -> FastAPI:
    app = create_app(
        readiness=FakeProbe(),
        clock=FakeClock(step=0.001),
        new_id=counter_ids("gen-id"),
    )

    @app.get("/domain/{code}")
    async def domain(code: ErrorCode) -> None:
        raise RecromancerError(code, message_for(code))

    @app.post("/many")
    async def many(body: Many) -> None:
        return None

    @app.post("/long")
    async def long_name(body: LongName) -> None:
        return None

    @app.get("/need-int")
    async def need_int(n: int) -> None:
        return None

    @app.get("/http/{status}")
    async def http_error(status: int) -> None:
        raise StarletteHTTPException(
            status_code=status, detail=MARKER, headers={"X-Leak": MARKER}
        )

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("boom " + MARKER)

    @app.post("/echo")
    async def echo(body: dict[str, str]) -> None:
        raise RuntimeError("route failed")

    return app


@pytest.fixture
def client(log_stream: io.StringIO) -> TestClient:
    return TestClient(build_app(), raise_server_exceptions=False)


def assert_error_body(
    response: Any, status: int, code: str, message: str, request_id: str | None = None
) -> None:
    assert response.status_code == status
    body = response.json()
    assert set(body) == {"code", "message", "request_id"}
    assert body["code"] == code
    assert body["message"] == message
    assert body["request_id"] == response.headers["x-request-id"]
    if request_id is not None:
        assert body["request_id"] == request_id


def assert_no_marker(response: Any) -> None:
    assert MARKER not in response.text
    assert all(MARKER not in v for v in response.headers.values())
    assert "x-leak" not in response.headers


# --- domain errors -------------------------------------------------------------


def test_every_error_code_has_an_expected_status() -> None:
    assert set(EXPECTED_STATUS) == set(ErrorCode)


@pytest.mark.parametrize("code", list(ErrorCode))
def test_domain_error_keeps_its_code_message_and_mapped_status(
    client: TestClient, code: ErrorCode
) -> None:
    response = client.get(f"/domain/{code.value}")
    assert_error_body(
        response, EXPECTED_STATUS[code], code.value, message_for(code), "gen-id-0001"
    )


# --- 422 -----------------------------------------------------------------------


def test_validation_lists_at_most_ten_locations_and_never_values(
    client: TestClient,
) -> None:
    response = client.post("/many", json={"f0": MARKER})
    expected = PREFIX + ", ".join(f"body.f{i}" for i in range(10))
    assert_error_body(response, 422, "VALIDATION_ERROR", expected)
    assert len(expected) <= MAX_MESSAGE_LENGTH
    assert MARKER not in response.text
    for text in ("Field required", "valid integer", "Input should", "body.f10"):
        assert text not in response.text


def test_validation_caps_an_overlong_location(client: TestClient) -> None:
    response = client.post("/long", json={"a" * 300: MARKER})
    location = ("body." + "a" * 300)[:100]
    assert len(location) == 100
    assert_error_body(response, 422, "VALIDATION_ERROR", PREFIX + location)
    assert len(response.json()["message"]) <= MAX_MESSAGE_LENGTH
    assert MARKER not in response.text


def test_validation_message_for_a_query_parameter(client: TestClient) -> None:
    response = client.get("/need-int", params={"n": MARKER})
    assert_error_body(response, 422, "VALIDATION_ERROR", PREFIX + "query.n")
    assert MARKER not in response.text


@pytest.mark.parametrize("count", [0, 30])
def test_validation_message_is_bounded_and_ignores_input_ctx_and_msg(
    count: int,
) -> None:
    errors: list[Any] = [
        {
            "type": "x",
            "loc": ("body", "f" * 300, i, True, 1.5),
            "msg": "MSG-LEAK",
            "input": "LEAK",
            "ctx": {"a": "LEAK"},
        }
        for i in range(count)
    ]
    message = validation_message(RequestValidationError(errors))
    assert len(message) <= MAX_MESSAGE_LENGTH
    assert "LEAK" not in message
    assert (message == "Request validation failed") == (count == 0)


def test_validation_message_skips_empty_and_duplicate_locations() -> None:
    errors: list[Any] = [
        {"type": "x", "loc": ()},
        {"type": "x", "loc": ("body", "a")},
        {"type": "x", "loc": ("body", "a")},
        {"type": "x", "loc": (b"bytes-only",)},
    ]
    assert validation_message(RequestValidationError(errors)) == PREFIX + "body.a"


# --- 404, 405, other HTTP statuses ----------------------------------------------


def test_unknown_path_is_a_fixed_404(client: TestClient) -> None:
    response = client.get("/missing")
    assert_error_body(response, 404, "NOT_FOUND", "Not found")
    assert set(response.headers) == {"content-length", "content-type", "x-request-id"}


def test_wrong_method_is_a_fixed_405_that_keeps_only_allow(
    client: TestClient,
) -> None:
    response = client.post("/healthz")
    assert_error_body(response, 405, "METHOD_NOT_ALLOWED", "Method not allowed")
    assert "GET" in response.headers["allow"]
    assert set(response.headers) == {
        "allow",
        "content-length",
        "content-type",
        "x-request-id",
    }


@pytest.mark.parametrize("status", [401, 418])
def test_other_http_statuses_keep_status_with_a_fixed_internal_error(
    client: TestClient, status: int
) -> None:
    response = client.get(f"/http/{status}")
    assert_error_body(response, status, "INTERNAL_ERROR", INTERNAL)
    assert_no_marker(response)


# --- catch-all -----------------------------------------------------------------


def test_unhandled_exception_hides_the_text_and_correlates_the_logs(
    client: TestClient, log_stream: io.StringIO
) -> None:
    response = client.get("/boom")
    request_id = response.headers["x-request-id"]
    assert response.json() == {
        "code": "INTERNAL_ERROR",
        "message": INTERNAL,
        "request_id": request_id,
    }
    assert response.status_code == 500
    assert response.headers.get_list("x-request-id") == [request_id]
    assert_no_marker(response)

    (access,) = records_for(log_stream, ACCESS)
    (error,) = records_for(log_stream, ERRORS)
    assert access["request_id"] == error["request_id"] == request_id
    assert access["status"] == 500
    assert error["message"] == "unhandled_exception"
    assert error["level"] == "error"
    assert set(error) == ERROR_LOG_KEYS
    assert error["exception_class"] == "RuntimeError"
    assert (error["method"], error["path"]) == ("GET", "/boom")


# --- request id ------------------------------------------------------------------


def test_valid_inbound_request_id_is_echoed_in_header_and_body(
    client: TestClient,
) -> None:
    response = client.get("/missing", headers={"X-Request-Id": "client-id-0001"})
    assert_error_body(response, 404, "NOT_FOUND", "Not found", "client-id-0001")
    assert response.headers.get_list("x-request-id") == ["client-id-0001"]


@pytest.mark.parametrize(
    "bad",
    [
        b"has space in it",
        b"abcdefgh\r\n",
        b"a\r\nX-Injected: 1",
        b"a" * 65,
        b"short",
        "caf\u00e9-12345".encode("latin-1"),
    ],
    ids=["space", "crlf", "injection", "65-chars", "short", "non-ascii"],
)
def test_invalid_inbound_request_id_is_replaced(client: TestClient, bad: bytes) -> None:
    response = client.get("/missing", headers=[(b"X-Request-Id", bad)])
    assert_error_body(response, 404, "NOT_FOUND", "Not found", "gen-id-0001")
    assert response.headers.get_list("x-request-id") == ["gen-id-0001"]


# --- access log ------------------------------------------------------------------

SCENARIOS = [
    pytest.param("GET", "/healthz", {}, 200, id="ok"),
    pytest.param("GET", "/missing", {}, 404, id="404"),
    pytest.param("POST", "/healthz", {}, 405, id="405"),
    pytest.param("POST", "/many", {"json": {}}, 422, id="422"),
    pytest.param("GET", "/domain/DEPENDENCY_UNAVAILABLE", {}, 503, id="domain"),
    pytest.param("GET", "/http/401", {}, 401, id="http-401"),
    pytest.param("GET", "/http/418", {}, 418, id="http-418"),
    pytest.param("GET", "/boom", {}, 500, id="500"),
]


@pytest.mark.parametrize(("method", "path", "extra", "status"), SCENARIOS)
def test_exactly_one_access_line_per_request_without_query_string(
    client: TestClient,
    log_stream: io.StringIO,
    method: str,
    path: str,
    extra: dict[str, Any],
    status: int,
) -> None:
    response = client.request(method, path, params={"trace": TRACE}, **extra)
    assert response.status_code == status
    request_id = response.headers["x-request-id"]
    assert response.headers.get_list("x-request-id") == [request_id]

    (line,) = records_for(log_stream, ACCESS)
    assert set(line) == ACCESS_KEYS
    assert line["message"] == "request"
    assert (line["method"], line["path"], line["status"]) == (method, path, status)
    assert line["request_id"] == request_id
    assert line["duration_ms"] == 1.0
    assert TRACE not in app_output(log_stream)


# --- traceback privacy -------------------------------------------------------------


def test_error_logging_never_contains_request_secrets(
    client: TestClient, log_stream: io.StringIO
) -> None:
    markers = {
        "query": "query-marker-" + "a" * 8,
        "authorization": "auth-marker-" + "b" * 8,
        "cookie": "cookie-marker-" + "c" * 8,
        "body": "body-marker-" + "d" * 8,
    }
    response = client.post(
        f"/echo?token={markers['query']}",
        headers={
            "Authorization": "Bearer " + markers["authorization"],
            "Cookie": "session=" + markers["cookie"],
        },
        json={"note": markers["body"]},
    )
    assert response.status_code == 500

    records = log_records(log_stream)
    assert {r["logger"] for r in records} == {ACCESS, ERRORS}
    text = app_output(log_stream)
    for marker in markers.values():
        assert marker not in text
    (access,) = records_for(log_stream, ACCESS)
    assert access["path"] == "/echo"
    (error,) = records_for(log_stream, ERRORS)
    assert "RuntimeError: route failed" in error["exception"]


# --- handler fallback ---------------------------------------------------------------


def test_missing_scope_state_falls_back_to_a_generated_id() -> None:
    app = FastAPI()
    register_exception_handlers(app, counter_ids("fallback-id"))
    response = TestClient(app, raise_server_exceptions=False).get("/missing")
    assert response.json()["request_id"] == "fallback-id-0001"
