"""Who is calling, for per-client limits (docs/day6-design.md sections 3.7 and 6.3).

Behind a proxy that the deployment trusts (TRUST_PROXY=true on Azure Container Apps), the
client is the last hop of X-Forwarded-For: the address the proxy itself appended, which a client
cannot forge (anything a client sends comes before it). Otherwise it is the TCP peer.
"""

UNKNOWN = "unknown"


def client_ip(peer: str | None, forwarded_for: str | None, *, trust_proxy: bool) -> str:
    if trust_proxy and forwarded_for:
        hops = [hop.strip() for hop in forwarded_for.split(",") if hop.strip()]
        if hops:
            return hops[-1]
    return peer or UNKNOWN
