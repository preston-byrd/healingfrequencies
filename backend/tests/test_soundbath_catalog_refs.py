"""Regression: every soundbath track in the CATALOG has a `ref` that
maps to a real preset in the frontend `soundBathEngine.PRESETS`. When
these keys drift out of sync (as they did with `aurora` vs
`aurora_bath`), the Harmonic-Blueprint Journey player silently no-ops
soundbath tracks — the player runs, the clock ticks, but no bath audio
ever plays.

The frontend file is the source of truth for allowed keys, so we
parse it here rather than hardcoding a list that would drift again.
"""

from __future__ import annotations

import re
from pathlib import Path


_FRONTEND_PRESETS = Path("/app/frontend/src/lib/soundBathEngine.js")


def _extract_preset_keys() -> set[str]:
    """Pick out the top-level keys inside `BATH_PRESETS = { ... }` in
    the frontend engine. Handles the current formatting (two-space
    indent, key: {) — will need adjusting if that file style changes.
    """
    src = _FRONTEND_PRESETS.read_text()
    # Find the BATH_PRESETS object body — it's the const declared at the
    # top of the file. We scan from that identifier until the matching
    # closing brace at column 1.
    start = src.find("BATH_PRESETS")
    assert start >= 0, "BATH_PRESETS not found in soundBathEngine.js"
    body_start = src.find("{", start)
    assert body_start >= 0
    depth = 0
    body_end = body_start
    for i in range(body_start, len(src)):
        c = src[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                body_end = i
                break
    body = src[body_start + 1 : body_end]
    # Match ONLY top-level keys (indented by exactly two spaces, then
    # identifier, then colon, then optional whitespace, then `{`).
    return set(re.findall(r"^  ([a-z_][a-z0-9_]*)\s*:\s*\{", body, flags=re.MULTILINE))


def test_soundbath_catalog_refs_match_frontend_presets():
    from server import HARMONIC_JOURNEY_CATALOG as CATALOG  # deferred so pytest collection works

    valid_keys = _extract_preset_keys()
    assert valid_keys, "Failed to parse BATH_PRESETS from soundBathEngine.js"

    bad = []
    for track in CATALOG:
        if track.get("type") != "soundbath":
            continue
        ref = track.get("ref")
        if ref not in valid_keys:
            bad.append((track.get("id"), ref))
    assert not bad, (
        "One or more soundbath tracks reference a preset key that does "
        "NOT exist in soundBathEngine.PRESETS. The Journey player will "
        "silently no-op these — no audio will ever play. Fix the ref in "
        "server.py HARMONIC_JOURNEY_CATALOG to match a real frontend key. "
        f"Broken tracks: {bad}. Valid keys: {sorted(valid_keys)}"
    )


def test_soundbath_catalog_contains_aurora_grounding_solfeggio():
    """Sanity: these three bath presets are the ones the Eigenmode
    Journey composer commonly picks, so they must be in the catalog
    with a valid ref."""
    from server import HARMONIC_JOURNEY_CATALOG as CATALOG

    valid_keys = _extract_preset_keys()
    ids = {t["id"]: t for t in CATALOG if t.get("type") == "soundbath"}
    for want_id, want_ref in (
        ("bath-aurora", "aurora_bath"),
        ("bath-grounding", "grounding_bath"),
        ("bath-solfeggio", "solfeggio_wash"),
    ):
        assert want_id in ids, f"missing catalog entry: {want_id}"
        assert ids[want_id]["ref"] == want_ref, (
            f"{want_id} ref must be '{want_ref}' (a real key in "
            f"soundBathEngine.PRESETS); got '{ids[want_id]['ref']}'"
        )
        assert want_ref in valid_keys, f"{want_ref} not in frontend PRESETS"
