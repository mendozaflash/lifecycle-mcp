"""
Unit tests for CLI argument parsing, transport method signatures, and default behavior.

Feature: NT-07
Tests cover:
  - CLI argument parsing for --transport, --host, --port
  - Environment variable fallbacks (LIFECYCLE_TRANSPORT, LIFECYCLE_HOST, LIFECYCLE_PORT)
  - CLI precedence over env vars
  - Default values (stdio / 127.0.0.1 / 8080)
  - LifecycleMCPServer transport method existence and coroutine signatures
"""

import argparse
import asyncio
import inspect
import os
from unittest.mock import patch

import pytest

from lifecycle_mcp.server import LifecycleMCPServer

# ---------------------------------------------------------------------------
# Helper: build an argparse parser mirroring the logic inside main()
# ---------------------------------------------------------------------------

def _build_parser(env: dict[str, str] | None = None) -> argparse.ArgumentParser:
    """Build an ArgumentParser identical to the one created in main().

    Args:
        env: Explicit mapping used in place of ``os.environ`` for defaults.
             Pass an empty dict to test with no env vars set.
             Pass ``None`` to read from the real ``os.environ``.
    """
    if env is None:
        env = os.environ

    parser = argparse.ArgumentParser(description="Lifecycle MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default=env.get("LIFECYCLE_TRANSPORT", "stdio"),
        help="Transport type (default: stdio, env: LIFECYCLE_TRANSPORT)",
    )
    parser.add_argument(
        "--host",
        default=env.get("LIFECYCLE_HOST", "127.0.0.1"),
        help="Host address for network transports (default: 127.0.0.1, env: LIFECYCLE_HOST)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(env.get("LIFECYCLE_PORT", "8080")),
        help="Port for network transports (default: 8080, env: LIFECYCLE_PORT)",
    )
    return parser


# ===========================================================================
# 1. Default behaviour (no args, no env vars)
# ===========================================================================

class TestCLIDefaultBehavior:
    """Defaults are correct when no CLI args and no env vars are provided."""

    def test_defaults_to_stdio_transport(self):
        args = _build_parser(env={}).parse_args([])
        assert args.transport == "stdio"

    def test_defaults_to_localhost(self):
        args = _build_parser(env={}).parse_args([])
        assert args.host == "127.0.0.1"

    def test_defaults_to_port_8080(self):
        args = _build_parser(env={}).parse_args([])
        assert args.port == 8080

    def test_all_defaults_together(self):
        """Verify all three defaults in a single assertion."""
        args = _build_parser(env={}).parse_args([])
        assert (args.transport, args.host, args.port) == ("stdio", "127.0.0.1", 8080)


# ===========================================================================
# 2. Explicit --transport flag parsing
# ===========================================================================

class TestCLITransportParsing:
    """Explicit --transport values are parsed correctly."""

    def test_transport_stdio(self):
        args = _build_parser(env={}).parse_args(["--transport", "stdio"])
        assert args.transport == "stdio"

    def test_transport_sse(self):
        args = _build_parser(env={}).parse_args(["--transport", "sse"])
        assert args.transport == "sse"

    def test_transport_streamable_http(self):
        args = _build_parser(env={}).parse_args(["--transport", "streamable-http"])
        assert args.transport == "streamable-http"

    def test_transport_invalid_raises_error(self):
        """An invalid transport value must cause argparse to exit with an error."""
        with pytest.raises(SystemExit) as exc_info:
            _build_parser(env={}).parse_args(["--transport", "invalid"])
        # argparse exits with code 2 for usage errors
        assert exc_info.value.code == 2


# ===========================================================================
# 3. --host and --port parsing
# ===========================================================================

class TestCLIHostPortParsing:
    """Explicit --host and --port values are parsed correctly."""

    def test_host_custom(self):
        args = _build_parser(env={}).parse_args(["--host", "0.0.0.0"])
        assert args.host == "0.0.0.0"

    def test_port_custom(self):
        args = _build_parser(env={}).parse_args(["--port", "9090"])
        assert args.port == 9090

    def test_host_and_port_together(self):
        args = _build_parser(env={}).parse_args(["--host", "0.0.0.0", "--port", "9090"])
        assert args.host == "0.0.0.0"
        assert args.port == 9090

    def test_port_non_integer_raises_error(self):
        """A non-integer port value must cause argparse to exit with an error."""
        with pytest.raises(SystemExit) as exc_info:
            _build_parser(env={}).parse_args(["--port", "abc"])
        assert exc_info.value.code == 2


# ===========================================================================
# 4. Environment variable fallbacks
# ===========================================================================

class TestEnvVarFallback:
    """Environment variables are used as defaults when no CLI args are given."""

    def test_lifecycle_transport_env(self):
        """LIFECYCLE_TRANSPORT=sse is picked up when no CLI arg given."""
        args = _build_parser(env={"LIFECYCLE_TRANSPORT": "sse"}).parse_args([])
        assert args.transport == "sse"

    def test_lifecycle_host_env(self):
        """LIFECYCLE_HOST env var is used as the default host."""
        args = _build_parser(env={"LIFECYCLE_HOST": "0.0.0.0"}).parse_args([])
        assert args.host == "0.0.0.0"

    def test_lifecycle_port_env(self):
        """LIFECYCLE_PORT env var is used as the default port."""
        args = _build_parser(env={"LIFECYCLE_PORT": "3000"}).parse_args([])
        assert args.port == 3000

    def test_all_env_vars_together(self):
        """All three env vars work simultaneously."""
        env = {
            "LIFECYCLE_TRANSPORT": "streamable-http",
            "LIFECYCLE_HOST": "192.168.1.1",
            "LIFECYCLE_PORT": "5555",
        }
        args = _build_parser(env=env).parse_args([])
        assert args.transport == "streamable-http"
        assert args.host == "192.168.1.1"
        assert args.port == 5555

    def test_env_var_via_monkeypatch(self, monkeypatch):
        """Verify env vars are read from os.environ (using monkeypatch)."""
        monkeypatch.setenv("LIFECYCLE_TRANSPORT", "sse")
        monkeypatch.setenv("LIFECYCLE_HOST", "10.0.0.1")
        monkeypatch.setenv("LIFECYCLE_PORT", "7777")
        # Pass env=None so _build_parser reads from real os.environ
        args = _build_parser(env=None).parse_args([])
        assert args.transport == "sse"
        assert args.host == "10.0.0.1"
        assert args.port == 7777


# ===========================================================================
# 5. CLI precedence over environment variables
# ===========================================================================

class TestCLIPrecedenceOverEnvVars:
    """CLI arguments take precedence over environment variables."""

    def test_cli_transport_overrides_env(self):
        """CLI --transport stdio overrides LIFECYCLE_TRANSPORT=sse."""
        env = {"LIFECYCLE_TRANSPORT": "sse"}
        args = _build_parser(env=env).parse_args(["--transport", "stdio"])
        assert args.transport == "stdio"

    def test_cli_host_overrides_env(self):
        """CLI --host overrides LIFECYCLE_HOST."""
        env = {"LIFECYCLE_HOST": "10.0.0.1"}
        args = _build_parser(env=env).parse_args(["--host", "0.0.0.0"])
        assert args.host == "0.0.0.0"

    def test_cli_port_overrides_env(self):
        """CLI --port overrides LIFECYCLE_PORT."""
        env = {"LIFECYCLE_PORT": "3000"}
        args = _build_parser(env=env).parse_args(["--port", "9090"])
        assert args.port == 9090

    def test_all_cli_override_all_env(self):
        """All CLI args override all env vars simultaneously."""
        env = {
            "LIFECYCLE_TRANSPORT": "sse",
            "LIFECYCLE_HOST": "10.0.0.1",
            "LIFECYCLE_PORT": "3000",
        }
        args = _build_parser(env=env).parse_args(
            ["--transport", "streamable-http", "--host", "0.0.0.0", "--port", "9090"]
        )
        assert args.transport == "streamable-http"
        assert args.host == "0.0.0.0"
        assert args.port == 9090

    def test_partial_cli_override(self):
        """Only the CLI-provided arg overrides; the rest fall back to env."""
        env = {
            "LIFECYCLE_TRANSPORT": "sse",
            "LIFECYCLE_HOST": "10.0.0.1",
            "LIFECYCLE_PORT": "3000",
        }
        args = _build_parser(env=env).parse_args(["--transport", "stdio"])
        assert args.transport == "stdio"
        assert args.host == "10.0.0.1"
        assert args.port == 3000


# ===========================================================================
# 6. main() end-to-end wiring (patches sys.argv and asyncio.run)
# ===========================================================================

class TestMainFunctionWiring:
    """Verify that main() correctly wires parsed args into amain()."""

    def test_main_defaults_call_amain(self, monkeypatch):
        """main() with no args passes default values to amain."""
        monkeypatch.setattr("sys.argv", ["lifecycle-mcp"])
        monkeypatch.delenv("LIFECYCLE_TRANSPORT", raising=False)
        monkeypatch.delenv("LIFECYCLE_HOST", raising=False)
        monkeypatch.delenv("LIFECYCLE_PORT", raising=False)

        with patch("lifecycle_mcp.server.amain") as mock_amain, \
             patch("lifecycle_mcp.server.asyncio") as mock_asyncio_mod:
            def run_side_effect(coro):
                if hasattr(coro, "close"):
                    coro.close()
            mock_asyncio_mod.run.side_effect = run_side_effect

            from lifecycle_mcp.server import main
            main()

            mock_amain.assert_called_once_with(
                transport="stdio", host="127.0.0.1", port=8080
            )

    def test_main_with_env_vars(self, monkeypatch):
        """main() picks up env vars and passes them to amain."""
        monkeypatch.setattr("sys.argv", ["lifecycle-mcp"])
        monkeypatch.setenv("LIFECYCLE_TRANSPORT", "sse")
        monkeypatch.setenv("LIFECYCLE_HOST", "10.0.0.1")
        monkeypatch.setenv("LIFECYCLE_PORT", "4000")

        with patch("lifecycle_mcp.server.amain") as mock_amain, \
             patch("lifecycle_mcp.server.asyncio") as mock_asyncio_mod:
            # Make asyncio.run actually call the coroutine-like argument
            def run_side_effect(coro):
                # Just close the coroutine to avoid warnings
                if hasattr(coro, "close"):
                    coro.close()
            mock_asyncio_mod.run.side_effect = run_side_effect

            from lifecycle_mcp.server import main
            main()

            mock_amain.assert_called_once_with(
                transport="sse", host="10.0.0.1", port=4000
            )


# ===========================================================================
# 7. Transport method signatures on LifecycleMCPServer
# ===========================================================================

class TestTransportMethodExistence:
    """LifecycleMCPServer exposes the expected transport methods."""

    def test_has_run_stdio(self):
        assert hasattr(LifecycleMCPServer, "run_stdio")

    def test_has_run_streamable_http(self):
        assert hasattr(LifecycleMCPServer, "run_streamable_http")

    def test_has_run_sse(self):
        assert hasattr(LifecycleMCPServer, "run_sse")


class TestTransportMethodsAreCoroutines:
    """All transport methods must be async (coroutine functions)."""

    def test_run_stdio_is_coroutine(self):
        assert asyncio.iscoroutinefunction(LifecycleMCPServer.run_stdio)

    def test_run_streamable_http_is_coroutine(self):
        assert asyncio.iscoroutinefunction(LifecycleMCPServer.run_streamable_http)

    def test_run_sse_is_coroutine(self):
        assert asyncio.iscoroutinefunction(LifecycleMCPServer.run_sse)


class TestTransportMethodSignatures:
    """Transport methods have the expected parameter signatures."""

    def test_run_stdio_takes_only_self(self):
        sig = inspect.signature(LifecycleMCPServer.run_stdio)
        params = list(sig.parameters.keys())
        assert params == ["self"]

    def test_run_streamable_http_accepts_host_and_port(self):
        sig = inspect.signature(LifecycleMCPServer.run_streamable_http)
        params = list(sig.parameters.keys())
        assert "self" in params
        assert "host" in params
        assert "port" in params

    def test_run_sse_accepts_host_and_port(self):
        sig = inspect.signature(LifecycleMCPServer.run_sse)
        params = list(sig.parameters.keys())
        assert "self" in params
        assert "host" in params
        assert "port" in params

    def test_run_streamable_http_host_annotated_str(self):
        sig = inspect.signature(LifecycleMCPServer.run_streamable_http)
        assert sig.parameters["host"].annotation is str

    def test_run_streamable_http_port_annotated_int(self):
        sig = inspect.signature(LifecycleMCPServer.run_streamable_http)
        assert sig.parameters["port"].annotation is int

    def test_run_sse_host_annotated_str(self):
        sig = inspect.signature(LifecycleMCPServer.run_sse)
        assert sig.parameters["host"].annotation is str

    def test_run_sse_port_annotated_int(self):
        sig = inspect.signature(LifecycleMCPServer.run_sse)
        assert sig.parameters["port"].annotation is int
