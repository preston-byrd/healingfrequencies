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


def test_spoofed_cf_header_ignored_when_peer_is_public():
    """If the pod is somehow reached directly by a public peer (e.g.
    local dev without a proxy), we ignore all forwarded headers so a
    client can't spoof their own IP via CF-Connecting-IP."""
    req = _mk_request(
        peer="73.209.144.15",  # public peer — not behind our proxy
        headers={
            "CF-Connecting-IP": "1.1.1.1",  # would-be spoof
            "X-Forwarded-For": "9.9.9.9",
        },
    )
    assert _client_ip(req) == "73.209.144.15"


def test_no_headers_and_private_peer_returns_peer():
    req = _mk_request(peer="10.0.0.5", headers={})
    assert _client_ip(req) == "10.0.0.5"


def test_ipv6_client_ip_supported():
    req = _mk_request(
        peer="10.0.0.5",
        headers={"CF-Connecting-IP": "2606:4700:4700::1111"},
    )
    assert _client_ip(req) == "2606:4700:4700::1111"
