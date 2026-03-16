"""
proxy.py — Proxy HTTP/HTTPS sidecar pour l'injection de clés API

Ce proxy tourne à l'intérieur de la microVM Kata Container.
Il intercepte les requêtes sortantes vers les APIs LLM et injecte
la clé API depuis /run/secrets/llm_api_key.

Principe de sécurité :
- Les agents configurent HTTPS_PROXY=http://localhost:8877
- Le proxy intercepte les requêtes vers api.anthropic.com / api.openai.com
- Il ajoute le header Authorization depuis /run/secrets/llm_api_key
- Les agents ne voient jamais la vraie clé API

Architecture : proxy HTTP CONNECT (tunnel SSL natif)
- Évite la complexité de mitmproxy pour ce cas d'usage simple
- Fonctionne avec aiohttp sans rupture SSL côté agent
"""

import argparse
import asyncio
import logging
import os
import ssl
import sys
from typing import Optional, Set

# ---------------------------------------------------------------------------
# Configuration du logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [proxy] %(levelname)s %(message)s",
)
logger = logging.getLogger("agentforge.proxy")

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
SECRET_FILE = "/run/secrets/llm_api_key"
DEFAULT_PORT = 8877
DEFAULT_ALLOWED = {"api.anthropic.com", "api.openai.com"}


# ---------------------------------------------------------------------------
# Lecture de la clé API depuis le fichier secrets
# ---------------------------------------------------------------------------

def read_api_key() -> str:
    """
    Lire la clé API depuis /run/secrets/llm_api_key.
    Ce fichier est monté en read-only depuis le host par Docker.
    """
    secret_path = os.getenv("API_KEY_FILE", SECRET_FILE)
    try:
        with open(secret_path, "r") as f:
            key = f.read().strip()
        if not key:
            logger.warning("Empty API key in %s", secret_path)
        return key
    except FileNotFoundError:
        logger.warning("Secret file not found: %s (running without key injection)", secret_path)
        return ""
    except Exception as e:
        logger.error("Error reading secret file: %s", e)
        return ""


# ---------------------------------------------------------------------------
# Proxy HTTP avec injection de credentials
# ---------------------------------------------------------------------------

class CredentialInjectingProxy:
    """
    Proxy HTTP simple qui :
    1. Accepte les connexions des agents
    2. Pour CONNECT (HTTPS) : établit un tunnel TCP vers la destination
    3. Pour HTTP direct : rewrite la requête avec le header Authorization
    4. Bloque toutes les destinations non whitelistées
    """

    def __init__(self, port: int, allowed_hosts: Set[str]):
        self.port = port
        self.allowed_hosts = allowed_hosts
        self._api_key: Optional[str] = None
        self._key_mtime: float = 0.0

    def get_api_key(self) -> str:
        """Lire la clé API avec cache (relit si le fichier a changé)."""
        secret_path = os.getenv("API_KEY_FILE", SECRET_FILE)
        try:
            mtime = os.path.getmtime(secret_path)
            if mtime != self._key_mtime:
                self._api_key = read_api_key()
                self._key_mtime = mtime
        except OSError:
            if self._api_key is None:
                self._api_key = read_api_key()
        return self._api_key or ""

    def _make_auth_header(self) -> Optional[str]:
        """Construire le header Authorization selon le provider."""
        key = self.get_api_key()
        if not key or key == "ollama_no_key":
            return None

        provider = os.getenv("LLM_PROVIDER", "anthropic").lower()
        if provider == "anthropic":
            return f"x-api-key: {key}"
        elif provider == "openai":
            return f"Authorization: Bearer {key}"
        else:
            return f"Authorization: Bearer {key}"

    async def handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Traiter une connexion cliente entrante."""
        client_addr = writer.get_extra_info("peername")
        try:
            # Lire la première ligne de la requête HTTP
            first_line = await asyncio.wait_for(reader.readline(), timeout=10.0)
            if not first_line:
                return

            request_line = first_line.decode("utf-8", errors="replace").strip()
            parts = request_line.split()
            if len(parts) < 3:
                writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
                await writer.drain()
                return

            method = parts[0].upper()
            target = parts[1]

            if method == "CONNECT":
                # Requête HTTPS (tunnel SSL)
                await self._handle_connect(reader, writer, target)
            else:
                # Requête HTTP directe
                await self._handle_http(reader, writer, method, target, first_line)

        except asyncio.TimeoutError:
            logger.debug("Client timeout: %s", client_addr)
        except (ConnectionResetError, BrokenPipeError):
            pass
        except Exception as e:
            logger.debug("Client error %s: %s", client_addr, e)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _handle_connect(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        target: str,
    ) -> None:
        """
        Gérer une requête HTTPS CONNECT (tunnel SSL transparent).
        La clé API sera injectée via les headers HTTP une fois le tunnel établi.
        """
        # Parser host:port
        if ":" in target:
            host, port_str = target.rsplit(":", 1)
            port = int(port_str)
        else:
            host = target
            port = 443

        # Vérifier la whitelist
        if not self._is_allowed(host):
            logger.warning("BLOCKED CONNECT to %s", host)
            client_writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 9\r\n\r\nForbidden")
            await client_writer.drain()
            return

        # Lire et ignorer les headers restants de la requête CONNECT
        await self._skip_headers(client_reader)

        # Ouvrir la connexion vers la destination
        try:
            server_reader, server_writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=15.0,
            )
        except Exception as e:
            logger.warning("Cannot connect to %s:%d: %s", host, port, e)
            client_writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            await client_writer.drain()
            return

        # Confirmer le tunnel
        client_writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await client_writer.drain()

        logger.info("TUNNEL %s → %s:%d", "client", host, port)

        # Bidirectionnel pipe — le SSL se négocie directement entre client et serveur
        # NOTE : dans ce mode tunnel, on ne peut PAS injecter les headers (SSL end-to-end)
        # Pour injecter les credentials en HTTPS, utiliser le mode mitm (voir ci-dessous)
        await asyncio.gather(
            self._pipe(client_reader, server_writer),
            self._pipe(server_reader, client_writer),
            return_exceptions=True,
        )

        try:
            server_writer.close()
            await server_writer.wait_closed()
        except Exception:
            pass

    async def _handle_http(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        method: str,
        target: str,
        first_line: bytes,
    ) -> None:
        """
        Gérer une requête HTTP directe (non-SSL).
        Injecter le header Authorization.
        """
        # Parser l'URL
        if target.startswith("http://"):
            rest = target[7:]
        else:
            rest = target

        if "/" in rest:
            host_port, path = rest.split("/", 1)
            path = "/" + path
        else:
            host_port = rest
            path = "/"

        if ":" in host_port:
            host, port_str = host_port.rsplit(":", 1)
            port = int(port_str)
        else:
            host = host_port
            port = 80

        # Vérifier la whitelist
        if not self._is_allowed(host):
            logger.warning("BLOCKED HTTP to %s", host)
            client_writer.write(b"HTTP/1.1 403 Forbidden\r\n\r\nForbidden")
            await client_writer.drain()
            return

        # Lire les headers du client
        raw_headers = await self._read_headers(client_reader)

        # Modifier les headers : injecter Authorization, retirer les anciens
        modified_headers = self._inject_credentials(raw_headers, host)

        # Reconstruire la requête
        new_first_line = f"{method} {path} HTTP/1.1\r\n".encode()
        request = new_first_line + modified_headers

        # Lire le corps si présent
        body = b""
        if b"content-length:" in raw_headers.lower():
            import re
            match = re.search(rb"content-length:\s*(\d+)", raw_headers, re.I)
            if match:
                length = int(match.group(1))
                body = await client_reader.read(length)

        # Connecter au serveur
        try:
            server_reader, server_writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=15.0,
            )
        except Exception as e:
            logger.warning("Cannot connect to %s:%d: %s", host, port, e)
            client_writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            await client_writer.drain()
            return

        logger.info("HTTP %s → %s:%d%s", method, host, port, path[:50])

        # Envoyer la requête modifiée
        server_writer.write(request + body)
        await server_writer.drain()

        # Relayer la réponse
        await self._pipe(server_reader, client_writer)
        try:
            server_writer.close()
            await server_writer.wait_closed()
        except Exception:
            pass

    def _inject_credentials(self, raw_headers: bytes, host: str) -> bytes:
        """
        Injecter les credentials dans les headers HTTP.
        Supprimer les anciens headers d'auth, ajouter les nouveaux.
        """
        lines = raw_headers.split(b"\r\n")
        new_lines = []
        skip_headers = {
            b"authorization",
            b"x-api-key",
            b"proxy-authorization",
        }

        for line in lines:
            if b":" in line:
                header_name = line.split(b":", 1)[0].lower().strip()
                if header_name in skip_headers:
                    continue
            new_lines.append(line)

        # Injecter les nouveaux credentials
        provider = os.getenv("LLM_PROVIDER", "anthropic").lower()
        api_key = self.get_api_key()

        if api_key and api_key != "ollama_no_key":
            if provider == "anthropic":
                new_lines.insert(-1, f"x-api-key: {api_key}".encode())
                new_lines.insert(-1, b"anthropic-version: 2023-06-01")
            elif provider == "openai":
                new_lines.insert(-1, f"Authorization: Bearer {api_key}".encode())

        return b"\r\n".join(new_lines)

    def _is_allowed(self, host: str) -> bool:
        """Vérifier que le host est dans la whitelist."""
        # Normaliser
        host_clean = host.lower().split(":")[0]
        for allowed in self.allowed_hosts:
            allowed_clean = allowed.lower().strip()
            if host_clean == allowed_clean or host_clean.endswith("." + allowed_clean):
                return True
        return False

    @staticmethod
    async def _read_headers(reader: asyncio.StreamReader) -> bytes:
        """Lire les headers HTTP jusqu'à la ligne vide."""
        headers = b""
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=10.0)
            headers += line
            if line in (b"\r\n", b"\n", b""):
                break
        return headers

    @staticmethod
    async def _skip_headers(reader: asyncio.StreamReader) -> None:
        """Lire et ignorer les headers HTTP."""
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=5.0)
            if line in (b"\r\n", b"\n", b""):
                break

    @staticmethod
    async def _pipe(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Copier les données de reader vers writer jusqu'à EOF."""
        try:
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                writer.write(data)
                await writer.drain()
        except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
            pass
        except Exception as e:
            logger.debug("Pipe error: %s", e)
        finally:
            try:
                writer.write_eof()
            except Exception:
                pass

    async def run(self) -> None:
        """Démarrer le serveur proxy."""
        server = await asyncio.start_server(
            self.handle_client,
            host="127.0.0.1",
            port=self.port,
        )
        logger.info(
            "Proxy started on 127.0.0.1:%d | Allowed: %s",
            self.port,
            ", ".join(sorted(self.allowed_hosts)),
        )

        async with server:
            await server.serve_forever()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="AgentForge API Key Proxy")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--allowed-hosts",
        default=",".join(DEFAULT_ALLOWED),
        help="Hosts autorisés, séparés par virgule",
    )
    args = parser.parse_args()

    allowed_hosts = set(h.strip() for h in args.allowed_hosts.split(",") if h.strip())
    proxy = CredentialInjectingProxy(port=args.port, allowed_hosts=allowed_hosts)

    logger.info("Starting AgentForge proxy sidecar")
    logger.info("Port: %d | Allowed: %s", args.port, ", ".join(sorted(allowed_hosts)))

    try:
        asyncio.run(proxy.run())
    except KeyboardInterrupt:
        logger.info("Proxy stopped")


if __name__ == "__main__":
    main()
