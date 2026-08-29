"""Regression: _client_ip prefers trusted proxy headers (CF-Connecting-IP,
True-Client-IP, X-Real-IP) over the X-Forwarded-For fallback.

On solarisound.com every user's Recent Activity row was showing the
SAME IP (`34.160.64.205` — the GCP LB) because the old logic took the
right-most public IP from XFF and the right-most public entry on our
multi-hop topology is our own outer load balancer, not the client.

Cloudflare fronts solarisound.com and adds `CF-Connecting-IP` with the
real client IP — that's what we should trust first. This test locks in
the priority order so future changes can't silently regress the audit
log back to LB-IP-for-everyone.
"""

from __future__ import annotations

from types import SimpleNamespace

from server import _client_ip


def _mk_request(*, peer: str, headers: dict) -> object:
    """Duck-typed Request stand-in with just what `_client_ip` reads."""
    lower = {k.lower(): v for k, v in headers.items()}
    return SimpleNamespace(
        client=SimpleNamespace(host=peer),
        headers=SimpleNamespace(get=lambda k: lower.get(k.lower())),
    )


def test_cf_connecting_ip_wins_over_lb_forwarded_for():
    """The Cloudflare-Connecting-IP header carries the true client IP
    and MUST be preferred over the outer LB IP that appears in XFF."""
    req = _mk_request(
        peer="10.0.0.5",  # private ingress → trusted proxy path
        headers={
            "CF-Connecting-IP": "73.209.144.15",  # real client
            "X-Forwarded-For": "73.209.144.15, 34.160.64.205",  # LB is right-most
        },
    )
    assert _client_ip(req) == "73.209.144.15"


def test_true_client_ip_used_when_cf_absent():
    req = _mk_request(
        peer="10.0.0.5",
        headers={
            "True-Client-IP": "8.8.8.8",
            "X-Forwarded-For": "34.160.64.205",
        },
    )
    assert _client_ip(req) == "8.8.8.8"


def test_x_real_ip_used_when_cf_and_tcip_absent():
    req = _mk_request(
        peer="10.0.0.5",
        headers={
            "X-Real-IP": "1.1.1.1",
            "X-Forwarded-For": "34.160.64.205",
        },
    )
    assert _client_ip(req) == "1.1.1.1"


def test_falls_back_to_rightmost_public_xff_when_no_trusted_headers():
    """When none of the trusted headers are present, we still return
    the right-most public XFF entry (spoof-resistant) rather than the
    left-most (which a client can prepend to)."""
    req = _mk_request(
        peer="10.0.0.5",
        headers={
            "X-Forwarded-For": "8.8.8.8, 34.160.64.205",
        },
    )
    # Right-most public = 34.160.64.205 — this is the pre-fix behaviour.
    # Kept as a spoof-resistant fallback so a client can't inject a
    # fake left-most and take over someone else's rate-limit bucket.
    assert _client_ip(req) == "34.160.64.205"


def test_cf_connecting_ip_wins_even_when_peer_is_public_lb():
    """HF-039 core fix: on solarisound.com the GCP LB hands the pod a
    PUBLIC peer IP (34.x.x.x). The old logic then returned that LB IP
    verbatim, ignoring CF-Connecting-IP entirely and leaving every audit
    row stamped with the same LB address. Now CF-Connecting-IP wins."""
    req = _mk_request(
        peer="34.160.64.205",  # GCP LB — public but is our infra
        headers={
            "CF-Connecting-IP": "73.209.144.15",  # real client
            "X-Forwarded-For": "73.209.144.15, 34.160.64.205",
        },
    )
    assert _client_ip(req) == "73.209.144.15"


def test_spoofed_cf_header_with_private_value_is_ignored():
    """A malformed / private CF-Connecting-IP header must NOT pollute
    the audit log. The trust check re-runs _is_valid_public_ip so a
    "127.0.0.1" or "10.0.0.1" injected header falls through to XFF."""
    req = _mk_request(
        peer="10.0.0.5",
        headers={
            "CF-Connecting-IP": "10.0.0.99",  # private range — reject
            "X-Forwarded-For": "8.8.8.8, 34.160.64.205",
        },
    )
    # CF header rejected → falls through to XFF right-most public.
    assert _client_ip(req) == "34.160.64.205"


def test_trust_cloudflare_headers_off_reverts_to_legacy_gate(monkeypatch):
    """When TRUST_CLOUDFLARE_HEADERS=false, we fall back to the pre-HF-039
    behaviour: CF headers only trusted when the direct peer is private.
    Provides an escape hatch for deployments not behind Cloudflare."""
    import server
    monkeypatch.setattr(server, "_TRUST_CLOUDFLARE_HEADERS", False)
    # Public peer + CF header → CF header is ignored, peer wins.
    req = _mk_request(
        peer="73.209.144.15",
        headers={"CF-Connecting-IP": "1.1.1.1"},
    )
    assert _client_ip(req) == "73.209.144.15"
    # Private peer + CF header → CF header still trusted (legacy path).
    req2 = _mk_request(
        peer="10.0.0.5",
        headers={"CF-Connecting-IP": "1.1.1.1"},
    )
    assert _client_ip(req2) == "1.1.1.1"


def test_spoofed_cf_header_ignored_when_peer_is_public():
    """When TRUST_CLOUDFLARE_HEADERS is on (default) we DO trust the CF
    header on public peers — that's the HF-039 fix. This test used to
    assert the opposite; kept as the toggle-off variant above."""
    req = _mk_request(
        peer="73.209.144.15",
        headers={
            "CF-Connecting-IP": "1.1.1.1",
            "X-Forwarded-For": "9.9.9.9",
        },
    )
    # With TRUST_CLOUDFLARE_HEADERS=true (default), CF-Connecting-IP wins.
    assert _client_ip(req) == "1.1.1.1"


def test_no_headers_and_private_peer_returns_peer():
    req = _mk_request(peer="10.0.0.5", headers={})
    assert _client_ip(req) == "10.0.0.5"


def test_ipv6_client_ip_supported():
    req = _mk_request(
        peer="10.0.0.5",
        headers={"CF-Connecting-IP": "2606:4700:4700::1111"},
    )
    assert _client_ip(req) == "2606:4700:4700::1111"


# HF-039 Cloudflare-anchored XFF resolution ---------------------------------
# When CF-Connecting-IP is stripped by an intermediate proxy (some CF plans
# don't propagate it, preview infra strips it) we walk XFF right→left and
# treat the entry to the LEFT of a Cloudflare-owned IP as the real client.

def test_xff_left_of_cloudflare_hop_wins():
    """Given `client, cf-edge, our-lb` in XFF, the client (left of the CF
    edge) MUST be returned. Reproduces the exact preview topology where
    the pod sees three-hop XFF and no CF-Connecting-IP."""
    req = _mk_request(
        peer="10.0.0.5",  # private ingress
        headers={
            # 172.71.255.121 is inside 172.64.0.0/13 (Cloudflare range).
            "X-Forwarded-For": "34.170.12.145, 172.71.255.121, 34.36.184.211",
        },
    )
    assert _client_ip(req) == "34.170.12.145"


def test_xff_no_cloudflare_hop_falls_back_to_rightmost():
    """When XFF contains no CF-owned IP, we still return the right-most
    public entry — the spoof-resistant fallback for non-CF topologies."""
    req = _mk_request(
        peer="10.0.0.5",
        headers={"X-Forwarded-For": "1.2.3.4, 8.8.8.8"},
    )
    assert _client_ip(req) == "8.8.8.8"


def test_xff_cloudflare_hop_with_private_left_falls_back():
    """If the entry to the left of the CF hop is somehow private (broken
    proxy chain), skip it and fall back to the right-most-public rule so
    we never surface an internal IP."""
    req = _mk_request(
        peer="10.0.0.5",
        headers={
            "X-Forwarded-For": "10.0.0.42, 172.71.255.121, 8.8.8.8",
        },
    )
    # 10.0.0.42 is private → CF-anchor path bails, right-most-public wins.
    assert _client_ip(req) == "8.8.8.8"


def test_cf_connecting_ip_preferred_over_xff_cloudflare_anchor():
    """When BOTH signals are present (Enterprise CF plans propagate the
    header while also filling XFF), CF-Connecting-IP wins — it's the
    single-hop trusted source."""
    req = _mk_request(
        peer="10.0.0.5",
        headers={
            "CF-Connecting-IP": "73.209.144.15",
            "X-Forwarded-For": "8.8.8.8, 172.71.255.121, 34.36.184.211",
        },
    )
    assert _client_ip(req) == "73.209.144.15"


def test_reserved_range_cf_header_rejected():
    """Cloudflare-family headers carrying a reserved / private IP (broken
    upstream or hostile injection) must be rejected, not surfaced verbatim
    into the audit log."""
    req = _mk_request(
        peer="10.0.0.5",
        headers={
            "CF-Connecting-IP": "203.0.113.42",  # RFC 5737 TEST-NET-3
            "X-Forwarded-For": "8.8.8.8, 172.71.255.121, 34.36.184.211",
        },
    )
    # CF header rejected → CF-anchored XFF walk finds the client (8.8.8.8).
    assert _client_ip(req) == "8.8.8.8"
