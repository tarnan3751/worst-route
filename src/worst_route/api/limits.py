"""Who a client is (`docs/reference/api.md`, **Public limits**).

`client_of(scope, trust_proxy)` names the client of a request: its peer's address, or behind a
reverse proxy (`ApiSettings.trust_proxy`) the last address of `X-Forwarded-For`, the one that
proxy appended (the earlier ones are whatever the client sent, so never trusted), without a
port a proxy may append (`a.b.c.d:port`, `[v6]:port`). An IPv6 client is named by its /64, the
block one subscriber usually holds. `client_hash` is what the
logs show instead: a sha256 of the name under a salt drawn when the process starts, so a
client's lines can be followed within one run of the server and its address is never written.
"""

import hashlib
import ipaddress
import secrets

from starlette.datastructures import Headers
from starlette.types import Scope

_SALT = secrets.token_bytes(16)


def client_of(scope: Scope, trust_proxy: bool) -> str:
    """The client of the request `scope` describes (an ASGI HTTP scope)."""
    client = scope.get("client")
    host = client[0] if client else "unknown"
    if trust_proxy:
        forwarded = ",".join(Headers(scope=scope).getlist("x-forwarded-for"))
        last = forwarded.rsplit(",", 1)[-1].strip()
        if last:
            host = _without_port(last)
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            return str(address.ipv4_mapped)
        return str(ipaddress.IPv6Network(f"{address}/64", strict=False))
    return str(address)


def client_hash(client: str) -> str:
    """The client as the logs show it: 12 hex digits, stable within this process."""
    return hashlib.sha256(_SALT + client.encode()).hexdigest()[:12]


def _without_port(host: str) -> str:
    """`host` without a port: `a.b.c.d:port` (one colon) and `[v6]:port` (brackets); a bare
    IPv6 address has more than one colon and no brackets."""
    if host.startswith("[") and "]" in host:
        return host[1 : host.index("]")]
    if host.count(":") == 1:
        return host.split(":", 1)[0]
    return host
