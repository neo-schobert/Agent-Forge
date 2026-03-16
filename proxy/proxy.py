"""
proxy.py — HTTP reverse proxy sidecar for AgentForge agent containers

Architecture:
- Listens on HTTP at localhost:8877 (not HTTPS, not a CONNECT proxy)
- LangChain clients point their base_url here directly
- Reads X-Agent-Name header to select the per-agent model
- Modifies the outgoing JSON body to set the correct model field
- Routes to the correct upstream based on LLM_PROVIDER env var:
    anthropic   → https://api.anthropic.com  (keep original path, e.g. /v1/messages)
    openai      → https://api.openai.com     (keep original path)
    openrouter  → https://openrouter.ai/api/v1/chat/completions (fixed endpoint)
- Injects API key from /run/secrets/llm_api_key
- For openrouter: adds HTTP-Referer and X-Title headers
- Streams SSE responses back correctly
- Keeps CONNECT tunnel support for non-LLM HTTPS traffic

Security model:
- Agents never see the real API key
- Key is read from Docker secret mount at /run/secrets/llm_api_key
- Key file is re-read on mtime change (hot-rotation support)
"""

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from typing import Dict, Optional, Set, Tuple

import httpx

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [proxy] %(levelname)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("agentforge.proxy")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SECRET_FILE = "/run/secrets/llm_api_key"
DEFAULT_PORT = 8877

# Outgoing destination whitelist (checked against upstream host, not client host)
DEFAULT_ALLOWED: Set[str] = {
    "api.anthropic.com",
    "api.openai.com",
    "openrouter.ai",
}

# Per-agent model env var names (uppercase agent name suffix)
AGENT_MODEL_ENV: Dict[str, str] = {
    "supervisor": "AGENT_SUPERVISOR_MODEL",
    "architect":  "AGENT_ARCHITECT_MODEL",
    "coder":      "AGENT_CODER_MODEL",
    "tester":     "AGENT_TESTER_MODEL",
    "reviewer":   "AGENT_REVIEWER_MODEL",
}


# ---------------------------------------------------------------------------
# API key cache (re-reads on mtime change)
# ---------------------------------------------------------------------------

class ApiKeyCache:
    def __init__(self) -> None:
        self._key: str = ""
        self._mtime: float = 0.0

    def get(self) -> str:
        secret_path = os.getenv("API_KEY_FILE", SECRET_FILE)
        try:
            mtime = os.path.getmtime(secret_path)
            if mtime != self._mtime:
                with open(secret_path, "r") as fh:
                    self._key = fh.read().strip()
                self._mtime = mtime
                logger.info("api_key_loaded path=%s", secret_path)
        except FileNotFoundError:
            if not self._key:
                logger.warning("secret_file_not_found path=%s (running without key injection)", secret_path)
        except Exception as exc:
            logger.error("secret_read_error path=%s error=%s", secret_path, exc)
        return self._key


_api_key_cache = ApiKeyCache()


# ---------------------------------------------------------------------------
# Model resolution
# ---------------------------------------------------------------------------

def resolve_model_for_agent(agent_name: str) -> str:
    """
    Return the model string for the given agent name.
    Falls back to LLM_MODEL if no per-agent override is set.
    """
    fallback = os.getenv("LLM_MODEL", "claude-sonnet-4-6")
    if not agent_name:
        return fallback
    env_var = AGENT_MODEL_ENV.get(agent_name.lower())
    if env_var:
        return os.getenv(env_var, fallback)
    # Unknown agent name — try a generic pattern
    generic = f"AGENT_{agent_name.upper()}_MODEL"
    return os.getenv(generic, fallback)


# ---------------------------------------------------------------------------
# Upstream routing
# ---------------------------------------------------------------------------

def resolve_upstream(path: str) -> Tuple[str, str]:
    """
    Determine the upstream URL and final path based on LLM_PROVIDER.

    Returns (upstream_base_url, upstream_path).
    """
    provider = os.getenv("LLM_PROVIDER", "anthropic").lower()

    if provider == "anthropic":
        return "https://api.anthropic.com", path

    elif provider == "openai":
        return "https://api.openai.com", path

    elif provider == "openrouter":
        # OpenRouter always uses a fixed endpoint regardless of the incoming path
        return "https://openrouter.ai", "/api/v1/chat/completions"

    else:
        # Unknown provider: try openai-compatible with env-supplied base
        base = os.getenv("LLM_BASE_URL", "https://api.openai.com")
        return base.rstrip("/"), path


def build_upstream_headers(
    original_headers: Dict[str, str],
    agent_name: str,
) -> Dict[str, str]:
    """
    Build the headers dict for the upstream request.
    - Strip hop-by-hop and proxy headers
    - Strip any existing auth headers (we inject the real key)
    - Inject the correct auth header for the provider
    - Add OpenRouter-specific headers if needed
    """
    provider = os.getenv("LLM_PROVIDER", "anthropic").lower()
    api_key = _api_key_cache.get()

    # Headers we never forward to upstream
    hop_by_hop = {
        "connection", "keep-alive", "proxy-authenticate",
        "proxy-authorization", "te", "trailers",
        "transfer-encoding", "upgrade",
        # strip existing auth; we inject below
        "authorization", "x-api-key",
        # strip proxy-specific
        "proxy-connection",
    }

    upstream: Dict[str, str] = {}
    for k, v in original_headers.items():
        if k.lower() in hop_by_hop:
            continue
        upstream[k] = v

    # Always set Host to the upstream host (will be overridden by httpx anyway,
    # but be explicit)
    upstream.pop("host", None)
    upstream.pop("Host", None)

    # Inject auth
    if api_key and api_key not in ("ollama_no_key", "none", ""):
        if provider == "anthropic":
            upstream["x-api-key"] = api_key
            upstream.setdefault("anthropic-version", "2023-06-01")
        else:
            # openai, openrouter, and generic providers use Bearer
            upstream["Authorization"] = f"Bearer {api_key}"

    # OpenRouter extras
    if provider == "openrouter":
        upstream["HTTP-Referer"] = "http://agentforge"
        upstream["X-Title"] = "AgentForge"

    return upstream


# ---------------------------------------------------------------------------
# HTTP request parser (asyncio streams)
# ---------------------------------------------------------------------------

async def read_http_request(
    reader: asyncio.StreamReader,
    timeout: float = 30.0,
) -> Tuple[str, str, str, Dict[str, str], bytes]:
    """
    Parse a full HTTP/1.1 request from an asyncio StreamReader.

    Returns (method, path, http_version, headers_dict, body_bytes).
    headers_dict keys are lowercased.
    """
    # Read request line
    raw_line = await asyncio.wait_for(reader.readline(), timeout=timeout)
    if not raw_line:
        raise ConnectionError("Empty request")
    request_line = raw_line.decode("utf-8", errors="replace").strip()
    parts = request_line.split(" ", 2)
    if len(parts) < 2:
        raise ValueError(f"Malformed request line: {request_line!r}")
    method = parts[0].upper()
    target = parts[1]
    http_version = parts[2] if len(parts) > 2 else "HTTP/1.1"

    # Read headers
    headers: Dict[str, str] = {}
    while True:
        raw_header = await asyncio.wait_for(reader.readline(), timeout=timeout)
        if raw_header in (b"\r\n", b"\n", b""):
            break
        decoded = raw_header.decode("utf-8", errors="replace").rstrip("\r\n")
        if ":" in decoded:
            name, _, value = decoded.partition(":")
            headers[name.strip().lower()] = value.strip()

    # Read body (based on Content-Length)
    body = b""
    content_length_str = headers.get("content-length", "")
    if content_length_str.isdigit():
        content_length = int(content_length_str)
        if content_length > 0:
            body = await asyncio.wait_for(
                reader.readexactly(content_length),
                timeout=timeout,
            )

    return method, target, http_version, headers, body


# ---------------------------------------------------------------------------
# Core proxy handler
# ---------------------------------------------------------------------------

class AgentForgeProxy:
    """
    HTTP reverse proxy with:
    - Per-agent model routing via X-Agent-Name header
    - API key injection from Docker secret
    - Support for streaming (SSE) and non-streaming responses
    - CONNECT tunnel passthrough for non-LLM HTTPS traffic
    """

    def __init__(self, port: int, allowed_hosts: Set[str]) -> None:
        self.port = port
        self.allowed_hosts = allowed_hosts
        # Shared async httpx client (created in async context)
        self._http_client: Optional[httpx.AsyncClient] = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                follow_redirects=True,
                timeout=httpx.Timeout(
                    connect=15.0,
                    read=300.0,   # LLM responses can be slow
                    write=30.0,
                    pool=5.0,
                ),
                limits=httpx.Limits(
                    max_connections=100,
                    max_keepalive_connections=20,
                ),
            )
        return self._http_client

    # ------------------------------------------------------------------
    # Connection handler entry point
    # ------------------------------------------------------------------

    async def handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        client_addr = writer.get_extra_info("peername", ("unknown", 0))
        try:
            method, target, _version, req_headers, body = await read_http_request(reader)
        except asyncio.TimeoutError:
            logger.debug("client_timeout addr=%s", client_addr)
            writer.close()
            return
        except (ConnectionError, ValueError) as exc:
            logger.debug("bad_request addr=%s error=%s", client_addr, exc)
            try:
                writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
                await writer.drain()
            except Exception:
                pass
            writer.close()
            return
        except Exception as exc:
            logger.warning("handle_client_error addr=%s error=%s", client_addr, exc)
            writer.close()
            return

        try:
            if method == "CONNECT":
                await self._handle_connect(reader, writer, target, req_headers)
            else:
                await self._handle_reverse_proxy(reader, writer, method, target, req_headers, body)
        except (BrokenPipeError, ConnectionResetError, asyncio.CancelledError):
            pass
        except Exception as exc:
            logger.warning("handler_error method=%s target=%s error=%s", method, target, exc)
            try:
                writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
                await writer.drain()
            except Exception:
                pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Reverse proxy handler (main path for LLM calls)
    # ------------------------------------------------------------------

    async def _handle_reverse_proxy(
        self,
        _reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        method: str,
        target: str,
        req_headers: Dict[str, str],
        body: bytes,
    ) -> None:
        """
        Forward the request to the appropriate upstream LLM API.
        Modifies the JSON body to inject the correct model for the agent.
        Streams the response back (SSE or JSON).
        """
        # Extract agent name for model routing
        agent_name = req_headers.get("x-agent-name", "").lower().strip()

        # Determine the path for upstream routing.
        # target may be an absolute URL (http://localhost:8877/v1/messages)
        # or a path (/v1/messages).
        if target.startswith("http://") or target.startswith("https://"):
            from urllib.parse import urlparse
            parsed = urlparse(target)
            path = parsed.path or "/"
            if parsed.query:
                path = f"{path}?{parsed.query}"
        else:
            path = target

        upstream_base, upstream_path = resolve_upstream(path)
        upstream_host = upstream_base.split("://", 1)[-1].split("/")[0]

        # Whitelist check on outgoing destination
        if not self._is_allowed(upstream_host):
            logger.warning("blocked_upstream host=%s", upstream_host)
            writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 9\r\n\r\nForbidden")
            await writer.drain()
            return

        upstream_url = upstream_base.rstrip("/") + upstream_path
        logger.info(
            "proxy_request method=%s agent=%s upstream=%s",
            method, agent_name or "(none)", upstream_url,
        )

        # Modify JSON body: inject model for the agent
        body = self._inject_model_in_body(body, agent_name, req_headers)

        # Build upstream headers
        upstream_headers = build_upstream_headers(req_headers, agent_name)
        # Update Content-Length if body changed
        upstream_headers["content-length"] = str(len(body))

        # Determine if the client expects a streaming response
        is_streaming = self._is_streaming_request(body, req_headers)

        client = self._get_client()

        if is_streaming:
            await self._stream_response(writer, client, method, upstream_url, upstream_headers, body)
        else:
            await self._buffered_response(writer, client, method, upstream_url, upstream_headers, body)

    def _inject_model_in_body(
        self,
        body: bytes,
        agent_name: str,
        req_headers: Dict[str, str],
    ) -> bytes:
        """
        Parse the JSON body and set the 'model' field to the agent-specific model.
        Returns the (possibly modified) body bytes.
        Non-JSON bodies are returned unchanged.
        """
        content_type = req_headers.get("content-type", "")
        if "json" not in content_type and not body.lstrip().startswith(b"{"):
            return body
        if not body:
            return body

        try:
            payload = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return body

        model = resolve_model_for_agent(agent_name)
        payload["model"] = model
        logger.debug("model_injected agent=%s model=%s", agent_name or "(default)", model)

        return json.dumps(payload).encode("utf-8")

    @staticmethod
    def _is_streaming_request(body: bytes, headers: Dict[str, str]) -> bool:
        """Heuristic: check if the request asks for a streaming response."""
        if body:
            try:
                payload = json.loads(body)
                if payload.get("stream") is True:
                    return True
            except Exception:
                pass
        accept = headers.get("accept", "")
        if "text/event-stream" in accept:
            return True
        return False

    async def _stream_response(
        self,
        writer: asyncio.StreamWriter,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        headers: Dict[str, str],
        body: bytes,
    ) -> None:
        """
        Stream an SSE response from the upstream back to the client.
        Writes HTTP/1.1 chunked response with raw SSE data lines.
        """
        try:
            async with client.stream(
                method,
                url,
                headers=headers,
                content=body,
            ) as resp:
                # Forward status line and response headers
                status_line = f"HTTP/1.1 {resp.status_code} {resp.reason_phrase}\r\n".encode()
                writer.write(status_line)

                # Forward upstream response headers (skip hop-by-hop)
                _skip = {"transfer-encoding", "connection", "keep-alive"}
                for hname, hval in resp.headers.multi_items():
                    if hname.lower() in _skip:
                        continue
                    writer.write(f"{hname}: {hval}\r\n".encode())
                writer.write(b"\r\n")
                await writer.drain()

                # Stream the body
                async for chunk in resp.aiter_bytes(chunk_size=4096):
                    if not chunk:
                        continue
                    writer.write(chunk)
                    await writer.drain()

        except httpx.HTTPStatusError as exc:
            logger.warning("upstream_http_error status=%d url=%s", exc.response.status_code, url)
            body_text = exc.response.text[:500]
            resp_body = body_text.encode("utf-8", errors="replace")
            writer.write(
                f"HTTP/1.1 {exc.response.status_code} Error\r\n"
                f"Content-Length: {len(resp_body)}\r\n\r\n".encode()
            )
            writer.write(resp_body)
            await writer.drain()
        except httpx.RequestError as exc:
            logger.error("upstream_request_error url=%s error=%s", url, exc)
            msg = b"upstream connection failed"
            writer.write(
                b"HTTP/1.1 502 Bad Gateway\r\n"
                b"Content-Length: " + str(len(msg)).encode() + b"\r\n\r\n" + msg
            )
            await writer.drain()

    async def _buffered_response(
        self,
        writer: asyncio.StreamWriter,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        headers: Dict[str, str],
        body: bytes,
    ) -> None:
        """
        Fetch a complete (non-streaming) response from the upstream and forward it.
        """
        try:
            resp = await client.request(
                method,
                url,
                headers=headers,
                content=body,
            )

            # Build response
            status_line = f"HTTP/1.1 {resp.status_code} {resp.reason_phrase}\r\n".encode()
            writer.write(status_line)

            _skip = {"transfer-encoding", "connection", "keep-alive"}
            for hname, hval in resp.headers.multi_items():
                if hname.lower() in _skip:
                    continue
                writer.write(f"{hname}: {hval}\r\n".encode())
            # Ensure Content-Length is correct
            resp_body = resp.content
            writer.write(f"Content-Length: {len(resp_body)}\r\n".encode())
            writer.write(b"\r\n")
            writer.write(resp_body)
            await writer.drain()

            logger.info(
                "upstream_response status=%d url=%s bytes=%d",
                resp.status_code, url, len(resp_body),
            )

        except httpx.HTTPStatusError as exc:
            logger.warning("upstream_http_error status=%d url=%s", exc.response.status_code, url)
            resp_body = exc.response.content
            writer.write(
                f"HTTP/1.1 {exc.response.status_code} Error\r\n"
                f"Content-Length: {len(resp_body)}\r\n\r\n".encode()
            )
            writer.write(resp_body)
            await writer.drain()
        except httpx.RequestError as exc:
            logger.error("upstream_request_error url=%s error=%s", url, exc)
            msg = b"upstream connection failed"
            writer.write(
                b"HTTP/1.1 502 Bad Gateway\r\n"
                b"Content-Length: " + str(len(msg)).encode() + b"\r\n\r\n" + msg
            )
            await writer.drain()

    # ------------------------------------------------------------------
    # CONNECT tunnel handler (non-LLM HTTPS traffic passthrough)
    # ------------------------------------------------------------------

    async def _handle_connect(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        target: str,
        req_headers: Dict[str, str],
    ) -> None:
        """
        Handle HTTP CONNECT tunneling for non-LLM HTTPS traffic.
        Establishes a raw TCP tunnel to the destination.
        Note: API key injection is NOT possible in CONNECT mode (end-to-end SSL).
        LLM API clients should use direct HTTP mode (base_url pointing to proxy).
        """
        if ":" in target:
            host, port_str = target.rsplit(":", 1)
            try:
                port = int(port_str)
            except ValueError:
                port = 443
        else:
            host = target
            port = 443

        # Whitelist check on outgoing destination
        if not self._is_allowed(host):
            logger.warning("blocked_connect host=%s", host)
            client_writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 9\r\n\r\nForbidden")
            await client_writer.drain()
            return

        try:
            server_reader, server_writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=15.0,
            )
        except Exception as exc:
            logger.warning("connect_failed host=%s port=%d error=%s", host, port, exc)
            client_writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
            await client_writer.drain()
            return

        client_writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await client_writer.drain()

        logger.info("tunnel_opened host=%s port=%d", host, port)

        # Bidirectional pipe
        await asyncio.gather(
            _pipe(client_reader, server_writer),
            _pipe(server_reader, client_writer),
            return_exceptions=True,
        )

        try:
            server_writer.close()
            await server_writer.wait_closed()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _is_allowed(self, host: str) -> bool:
        """Check that the outgoing destination host is whitelisted."""
        host_clean = host.lower().split(":")[0]
        for allowed in self.allowed_hosts:
            allowed_clean = allowed.lower().strip()
            if host_clean == allowed_clean or host_clean.endswith("." + allowed_clean):
                return True
        return False

    # ------------------------------------------------------------------
    # Server lifecycle
    # ------------------------------------------------------------------

    async def run(self) -> None:
        server = await asyncio.start_server(
            self.handle_client,
            host="127.0.0.1",
            port=self.port,
        )
        logger.info(
            "proxy_started addr=127.0.0.1:%d allowed=%s",
            self.port,
            ",".join(sorted(self.allowed_hosts)),
        )
        async with server:
            await server.serve_forever()


# ---------------------------------------------------------------------------
# Shared pipe utility
# ---------------------------------------------------------------------------

async def _pipe(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    chunk_size: int = 65536,
) -> None:
    """Copy bytes from reader to writer until EOF."""
    try:
        while True:
            data = await reader.read(chunk_size)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
        pass
    except Exception as exc:
        logger.debug("pipe_error error=%s", exc)
    finally:
        try:
            writer.write_eof()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="AgentForge LLM Proxy Sidecar — per-agent model routing + API key injection"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("PROXY_PORT", str(DEFAULT_PORT))),
        help="Port to listen on (default: 8877)",
    )
    parser.add_argument(
        "--allowed-hosts",
        default=",".join(sorted(DEFAULT_ALLOWED)),
        help="Comma-separated list of allowed upstream hosts",
    )
    args = parser.parse_args()

    allowed_hosts: Set[str] = {h.strip() for h in args.allowed_hosts.split(",") if h.strip()}
    proxy = AgentForgeProxy(port=args.port, allowed_hosts=allowed_hosts)

    provider = os.getenv("LLM_PROVIDER", "anthropic").lower()
    default_model = os.getenv("LLM_MODEL", "claude-sonnet-4-6")
    logger.info(
        "proxy_config provider=%s default_model=%s port=%d",
        provider, default_model, args.port,
    )

    try:
        asyncio.run(proxy.run())
    except KeyboardInterrupt:
        logger.info("proxy_stopped")


if __name__ == "__main__":
    main()
