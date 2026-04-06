"""
Integration tests for MCP server HTTP transports.

Tests verify that:
- Streamable-HTTP server starts and responds to MCP protocol requests
- SSE server starts and establishes SSE event streams
- Tool calls return valid results over HTTP transports

Feature: NT-08
"""

import contextlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time

import httpx
import pytest

# Apply the integration marker to every test in this module.
pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_free_port() -> int:
    """Find a free TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_server(host: str, port: int, timeout: float = 15.0) -> bool:
    """Poll until the server accepts TCP connections or *timeout* seconds elapse."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return True
        except (ConnectionRefusedError, OSError):
            time.sleep(0.3)
    return False


# -- JSON-RPC message builders ---------------------------------------------


def _build_initialize_request(request_id: int = 1) -> dict:
    """Return a JSON-RPC ``initialize`` request conforming to MCP 2024-11-05."""
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "1.0.0"},
        },
    }


def _build_initialized_notification() -> dict:
    """Return a JSON-RPC ``notifications/initialized`` notification."""
    return {
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
    }


def _build_tool_call_request(
    tool_name: str, arguments: dict | None = None, request_id: int = 2
) -> dict:
    """Return a JSON-RPC ``tools/call`` request."""
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments or {},
        },
    }


# -- Response parsing -------------------------------------------------------


def _parse_jsonrpc_from_response(response: httpx.Response) -> dict | None:
    """Extract the first JSON-RPC *response* message from a JSON or SSE body.

    The MCP streamable-HTTP transport may reply with either
    ``application/json`` (single message) or ``text/event-stream`` (one or
    more SSE events).  This helper transparently handles both.
    """
    body_text = response.text.strip()
    if not body_text:
        return None

    ct = response.headers.get("content-type", "")

    if "application/json" in ct:
        return response.json()

    if "text/event-stream" in ct:
        for line in body_text.splitlines():
            if line.startswith("data:"):
                data_str = line[len("data:") :].strip()
                try:
                    obj = json.loads(data_str)
                    # Return the first JSON-RPC response (has "result" or "error").
                    if isinstance(obj, dict) and ("result" in obj or "error" in obj):
                        return obj
                except json.JSONDecodeError:
                    continue

    return None


def _post_mcp(
    client: httpx.Client,
    base_url: str,
    message: dict,
    session_id: str | None = None,
) -> tuple[httpx.Response, dict | None]:
    """POST a JSON-RPC message to ``/mcp`` and return *(response, parsed)*."""
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id

    resp = client.post(f"{base_url}/mcp/", json=message, headers=headers)
    parsed = _parse_jsonrpc_from_response(resp)
    return resp, parsed


# ---------------------------------------------------------------------------
# Fixtures — server processes
# ---------------------------------------------------------------------------

_HOST = "127.0.0.1"


def _start_server(transport: str, host: str, port: int, db_path: str) -> subprocess.Popen:
    """Launch the MCP server as a subprocess and return the ``Popen`` handle."""
    env = {**os.environ, "LIFECYCLE_DB": db_path}
    cmd = [
        sys.executable,
        "-m",
        "lifecycle_mcp.server",
        "--transport",
        transport,
        "--host",
        host,
        "--port",
        str(port),
    ]
    return subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _stop_server(proc: subprocess.Popen) -> None:
    """Gracefully stop the server subprocess."""
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


@pytest.fixture()
def streamable_http_server():
    """Start a *streamable-http* server and yield ``(host, port)``."""
    port = _get_free_port()
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    proc = _start_server("streamable-http", _HOST, port, db_path)

    if not _wait_for_server(_HOST, port):
        _stop_server(proc)
        stderr = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
        pytest.fail(
            f"streamable-http server did not start on {_HOST}:{port}.\nstderr:\n{stderr}"
        )

    yield _HOST, port

    _stop_server(proc)
    with contextlib.suppress(OSError):
        os.unlink(db_path)


@pytest.fixture()
def sse_server():
    """Start an *SSE* server and yield ``(host, port)``."""
    port = _get_free_port()
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    proc = _start_server("sse", _HOST, port, db_path)

    if not _wait_for_server(_HOST, port):
        _stop_server(proc)
        stderr = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
        pytest.fail(
            f"SSE server did not start on {_HOST}:{port}.\nstderr:\n{stderr}"
        )

    yield _HOST, port

    _stop_server(proc)
    with contextlib.suppress(OSError):
        os.unlink(db_path)


# ===========================================================================
# Streamable-HTTP Tests
# ===========================================================================


class TestStreamableHTTPServerStarts:
    """The streamable-HTTP transport starts and accepts HTTP connections."""

    def test_server_responds_to_post(self, streamable_http_server):
        """POST to /mcp returns an HTTP response (proves the server is up)."""
        host, port = streamable_http_server
        with httpx.Client(timeout=5.0) as client:
            resp = client.post(
                f"http://{host}:{port}/mcp/",
                content=b"{}",
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                },
            )
            # Any HTTP status proves the server is listening and routing.
            assert resp.status_code is not None


class TestStreamableHTTPMCPInitialization:
    """MCP initialization handshake over streamable-HTTP."""

    def test_initialize_returns_capabilities(self, streamable_http_server):
        """A valid InitializeRequest receives capabilities and serverInfo."""
        host, port = streamable_http_server
        base_url = f"http://{host}:{port}"

        with httpx.Client(timeout=10.0) as client:
            resp, parsed = _post_mcp(client, base_url, _build_initialize_request())

            assert resp.status_code == 200
            assert parsed is not None, (
                f"Could not parse JSON-RPC from response (ct={resp.headers.get('content-type')}): "
                f"{resp.text[:500]}"
            )
            assert "result" in parsed, f"Expected 'result' key in response: {parsed}"
            result = parsed["result"]
            assert "capabilities" in result
            assert "serverInfo" in result


class TestStreamableHTTPToolCall:
    """Full round-trip tool call over streamable-HTTP."""

    def test_tool_call_returns_valid_result(self, streamable_http_server):
        """Initialize, send notification, call get_project_status, verify result."""
        host, port = streamable_http_server
        base_url = f"http://{host}:{port}"

        with httpx.Client(timeout=10.0) as client:
            # Step 1 -- initialize
            init_resp, init_parsed = _post_mcp(
                client, base_url, _build_initialize_request()
            )
            assert init_resp.status_code == 200
            assert init_parsed is not None and "result" in init_parsed

            session_id = init_resp.headers.get("mcp-session-id")

            # Step 2 -- send initialized notification
            notif_resp, _ = _post_mcp(
                client,
                base_url,
                _build_initialized_notification(),
                session_id=session_id,
            )
            # Notifications may yield 200, 202, or 204.
            assert notif_resp.status_code in (200, 202, 204)

            # Step 3 -- call a tool
            tool_resp, tool_parsed = _post_mcp(
                client,
                base_url,
                _build_tool_call_request("get_project_status"),
                session_id=session_id,
            )
            assert tool_resp.status_code == 200
            assert tool_parsed is not None, (
                f"Could not parse tool response (ct={tool_resp.headers.get('content-type')}): "
                f"{tool_resp.text[:500]}"
            )
            assert "result" in tool_parsed, f"Expected 'result' in tool response: {tool_parsed}"


# ===========================================================================
# SSE Tests
# ===========================================================================


class TestSSEServerStarts:
    """The SSE transport starts and serves event streams."""

    def test_sse_endpoint_content_type(self, sse_server):
        """GET /sse returns content-type text/event-stream."""
        host, port = sse_server
        with httpx.Client(timeout=5.0) as client, client.stream("GET", f"http://{host}:{port}/sse") as resp:
            ct = resp.headers.get("content-type", "")
            assert "text/event-stream" in ct, (
                f"Expected text/event-stream content-type, got: {ct}"
            )


class TestSSEEndpointEvent:
    """The SSE /sse endpoint emits the ``endpoint`` event."""

    def test_sse_sends_endpoint_event(self, sse_server):
        """Connect to /sse and verify the first SSE event is ``endpoint``."""
        host, port = sse_server
        with httpx.Client(timeout=10.0) as client, client.stream("GET", f"http://{host}:{port}/sse") as resp:
            assert resp.status_code == 200

            found_endpoint = False
            event_type: str | None = None

            for line in resp.iter_lines():
                if line.startswith("event:"):
                    event_type = line[len("event:") :].strip()
                elif line.startswith("data:") and event_type == "endpoint":
                    data = line[len("data:") :].strip()
                    # The endpoint event carries a URL path containing /messages/
                    assert "/messages/" in data, (
                        f"Expected '/messages/' in endpoint data, got: {data}"
                    )
                    found_endpoint = True
                    break

            assert found_endpoint, "Did not receive 'endpoint' SSE event from /sse"
