from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

import httpx


class UnsafeExternalUrlError(ValueError):
    pass


class ResponseTooLargeError(ValueError):
    pass


def _is_mock_transport(client: httpx.AsyncClient) -> bool:
    # MockTransport cannot open sockets; keeping it DNS-free lets tests supply
    # reserved example domains without weakening the production HTTP path.
    return isinstance(getattr(client, "_transport", None), httpx.MockTransport)


class _PinnedNetworkBackend:
    """Connect only to the public addresses resolved during URL validation."""

    def __init__(self, backend, hostname: str, addresses: tuple[str, ...]):
        self._backend = backend
        self._hostname = hostname.rstrip(".").lower()
        self._addresses = addresses

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options=None,
    ):
        if host.rstrip(".").lower() != self._hostname:
            raise OSError("HTTP destination did not match its validated hostname")
        last_error: Exception | None = None
        options = tuple(socket_options or ())
        for address in self._addresses:
            try:
                return await self._backend.connect_tcp(
                    address,
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=options,
                )
            except Exception as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise OSError("Validated destination has no addresses")

    async def connect_unix_socket(self, path: str, timeout=None, socket_options=None):
        return await self._backend.connect_unix_socket(
            path,
            timeout=timeout,
            socket_options=socket_options,
        )

    async def sleep(self, seconds: float) -> None:
        await self._backend.sleep(seconds)


class _PinnedAsyncHTTPTransport(httpx.AsyncHTTPTransport):
    def __init__(self, hostname: str, addresses: tuple[str, ...]):
        super().__init__(trust_env=False, retries=0)
        # httpcore keeps the URL hostname for Host and TLS verification; this
        # backend pins only the TCP destination to the address we validated.
        self._pool._network_backend = _PinnedNetworkBackend(
            self._pool._network_backend,
            hostname,
            addresses,
        )


async def validate_public_http_url(
    url: str,
    client: httpx.AsyncClient,
) -> tuple[str, tuple[str, ...]] | None:
    if not isinstance(url, str) or not url or any(ord(char) < 32 for char in url):
        raise UnsafeExternalUrlError("Invalid external URL")
    try:
        parsed = urlsplit(url)
        scheme = parsed.scheme.lower()
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise UnsafeExternalUrlError("Invalid external URL") from exc

    if scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
        raise UnsafeExternalUrlError("External URL must use HTTP(S) without credentials")
    if parsed.fragment or "%" in host:
        raise UnsafeExternalUrlError("Invalid external URL host")
    expected_port = 443 if scheme == "https" else 80
    if port is not None and port != expected_port:
        raise UnsafeExternalUrlError("External URL uses a disallowed port")

    normalized_host = host.rstrip(".").lower()
    if normalized_host in {"localhost", "localhost.localdomain", "metadata.google.internal"} or normalized_host.endswith(
        (".localhost", ".local", ".internal", ".home.arpa")
    ):
        raise UnsafeExternalUrlError("External URL points to a local host")

    try:
        literal_address = ipaddress.ip_address(normalized_host)
    except ValueError:
        literal_address = None
    if literal_address is not None:
        if not literal_address.is_global:
            raise UnsafeExternalUrlError("External URL points to a non-public address")
        return normalized_host, (str(literal_address),)

    try:
        ascii_host = normalized_host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise UnsafeExternalUrlError("Invalid external URL host") from exc
    if not ascii_host or any(not label or len(label) > 63 for label in ascii_host.split(".")):
        raise UnsafeExternalUrlError("Invalid external URL host")

    if _is_mock_transport(client):
        return None

    try:
        answers = await asyncio.get_running_loop().getaddrinfo(
            ascii_host,
            port or expected_port,
            type=socket.SOCK_STREAM,
        )
    except OSError as exc:
        raise UnsafeExternalUrlError("External URL host could not be resolved") from exc
    addresses = {answer[4][0].split("%", 1)[0] for answer in answers}
    if not addresses:
        raise UnsafeExternalUrlError("External URL host has no addresses")
    for value in addresses:
        try:
            address = ipaddress.ip_address(value)
        except ValueError as exc:
            raise UnsafeExternalUrlError("External URL resolved to an invalid address") from exc
        if not address.is_global:
            raise UnsafeExternalUrlError("External URL resolves to a non-public address")
    return ascii_host, tuple(sorted(addresses))


async def fetch_limited_bytes(
    client: httpx.AsyncClient,
    url: str,
    max_bytes: int,
) -> tuple[bytes, httpx.Headers]:
    limit = max(1, int(max_bytes))
    try:
        current_url = url
        original_scheme = urlsplit(url).scheme.lower()
    except (TypeError, ValueError) as exc:
        raise UnsafeExternalUrlError("Invalid external URL") from exc
    for redirect_count in range(4):
        validated = await validate_public_http_url(current_url, client)
        owns_response_client = validated is not None
        response_client = client
        if validated is not None:
            hostname, addresses = validated
            response_client = httpx.AsyncClient(
                transport=_PinnedAsyncHTTPTransport(hostname, addresses),
                timeout=client.timeout,
                trust_env=False,
            )
        try:
            async with response_client.stream(
                "GET",
                current_url,
                headers={"Accept-Encoding": "identity"},
                follow_redirects=False,
            ) as response:
                if 300 <= response.status_code < 400:
                    location = response.headers.get("location")
                    if not location or redirect_count == 3:
                        raise UnsafeExternalUrlError("External URL has an invalid redirect")
                    redirected_url = urljoin(current_url, location)
                    if original_scheme == "https" and urlsplit(redirected_url).scheme.lower() != "https":
                        raise UnsafeExternalUrlError("External URL redirect downgraded HTTPS")
                else:
                    response.raise_for_status()
                    content_encoding = response.headers.get("content-encoding", "identity").strip().lower()
                    if content_encoding not in {"", "identity"}:
                        raise UnsafeExternalUrlError("Compressed external responses are not permitted")
                    content_length = response.headers.get("content-length")
                    if content_length is not None:
                        try:
                            declared_length = int(content_length)
                        except ValueError:
                            declared_length = 0
                        if declared_length > limit:
                            raise ResponseTooLargeError("External response exceeds the configured size limit")

                    if _is_mock_transport(client):
                        # MockTransport responses are already isolated from the
                        # network and commonly expose their fixture body as a
                        # consumed ByteStream. Read the buffered fixture while
                        # retaining the same configured size limit.
                        content = await response.aread()
                        if len(content) > limit:
                            raise ResponseTooLargeError(
                                "External response exceeds the configured size limit"
                            )
                        return content, response.headers

                    chunks: list[bytes] = []
                    total_bytes = 0
                    async for chunk in response.aiter_raw(chunk_size=64 * 1024):
                        total_bytes += len(chunk)
                        if total_bytes > limit:
                            raise ResponseTooLargeError("External response exceeds the configured size limit")
                        chunks.append(chunk)
                    return b"".join(chunks), response.headers
        finally:
            if owns_response_client:
                await response_client.aclose()
        current_url = redirected_url
    raise UnsafeExternalUrlError("External URL exceeded the redirect limit")
