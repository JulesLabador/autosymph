"""Tests for the verify_review verdict parser.


The verdict parser must recognize:
- decision="approve"           → Signal.COMPLETE  (or COMPLETE_LOW_RISK if fallback says so)
- decision="reject"            → Signal.FAIL
- decision="reject_structify"  → Signal.STRUCTIFY  (regression — was silently dropped → FAIL → cycle)
- unknown decision             → Signal.FAIL  (logged warning)
- no verdict block             → Signal.FAIL
- malformed JSON in block      → Signal.FAIL

The contradiction check (heading says REJECTED but decision says approve) must still flip to FAIL.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from autosymph.orchestrator import Orchestrator
from autosymph.state_machine import Signal


def _orch_with_comments(comment_bodies: list[str]) -> Orchestrator:
    """Construct a bare Orchestrator stub whose `linear.fetch_recent_comments`
    returns objects with a `.body` attribute matching the supplied bodies."""
    orch = Orchestrator.__new__(Orchestrator)  # bypass __init__
    fake_comments = [SimpleNamespace(body=b) for b in comment_bodies]
    orch.linear = SimpleNamespace(fetch_recent_comments=AsyncMock(return_value=fake_comments))
    return orch


def _orch_with_dated_comments(items: list[tuple[str, str]]) -> Orchestrator:
    """Like _orch_with_comments but each item is (body, created_at_iso)."""
    orch = Orchestrator.__new__(Orchestrator)
    fake_comments = [SimpleNamespace(body=b, created_at=ts) for b, ts in items]
    orch.linear = SimpleNamespace(fetch_recent_comments=AsyncMock(return_value=fake_comments))
    return orch


@pytest.mark.asyncio
async def test_approve_returns_complete():
    body = """## Verify Review — APPROVED

All items pass.

<!-- autosymph:verify_review {"decision":"approve"} -->"""
    orch = _orch_with_comments([body])
    sig = await orch._extract_verify_review_verdict("ISS-1", "ISS-1", Signal.COMPLETE)
    assert sig == Signal.COMPLETE


@pytest.mark.asyncio
async def test_approve_preserves_complete_low_risk():
    body = """## Verify Review — APPROVED

<!-- autosymph:verify_review {"decision":"approve"} -->"""
    orch = _orch_with_comments([body])
    sig = await orch._extract_verify_review_verdict("ISS-1", "ISS-1", Signal.COMPLETE_LOW_RISK)
    assert sig == Signal.COMPLETE_LOW_RISK


@pytest.mark.asyncio
async def test_reject_returns_fail():
    body = """## Verify Review — REJECTED

Issues found.

<!-- autosymph:verify_review {"decision":"reject","reasons":["missing_screenshot"]} -->"""
    orch = _orch_with_comments([body])
    sig = await orch._extract_verify_review_verdict("ISS-1", "ISS-1", Signal.COMPLETE)
    assert sig == Signal.FAIL


@pytest.mark.asyncio
async def test_reject_structify_returns_structify():
    """Regression: this was the ISSUE-374 bug. Decision was silently dropped → FAIL → cycle."""
    body = """## Verify Review — REJECTED (structify)

Infrastructure gaps detected: idb broken, auth.md missing.

<!-- autosymph:verify_review {"decision":"reject_structify","reasons":["idb_python314_libexpat","auth_md_missing"]} -->"""
    orch = _orch_with_comments([body])
    sig = await orch._extract_verify_review_verdict("ISS-1", "ISS-1", Signal.COMPLETE)
    assert sig == Signal.STRUCTIFY, (
        f"reject_structify must return Signal.STRUCTIFY (got {sig.value}). "
        "If this fails, the ISSUE-374 cycle bug is back: orchestrator will route to FAIL → verify."
    )


@pytest.mark.asyncio
async def test_unknown_decision_returns_fail():
    body = """## Verify Review

<!-- autosymph:verify_review {"decision":"reject_with_extreme_prejudice"} -->"""
    orch = _orch_with_comments([body])
    sig = await orch._extract_verify_review_verdict("ISS-1", "ISS-1", Signal.COMPLETE)
    assert sig == Signal.FAIL


@pytest.mark.asyncio
async def test_no_verdict_block_returns_fail():
    body = """## Verify Review

The agent forgot to emit a verdict block."""
    orch = _orch_with_comments([body])
    sig = await orch._extract_verify_review_verdict("ISS-1", "ISS-1", Signal.COMPLETE)
    assert sig == Signal.FAIL


@pytest.mark.asyncio
async def test_malformed_json_returns_fail():
    body = """## Verify Review

<!-- autosymph:verify_review {decision:approve} -->"""  # missing quotes — invalid JSON
    orch = _orch_with_comments([body])
    sig = await orch._extract_verify_review_verdict("ISS-1", "ISS-1", Signal.COMPLETE)
    assert sig == Signal.FAIL


@pytest.mark.asyncio
async def test_contradiction_heading_rejected_decision_approve_returns_fail():
    body = """## Verify Review — REJECTED

But the agent emitted an approve decision (contradiction).

<!-- autosymph:verify_review {"decision":"approve"} -->"""
    orch = _orch_with_comments([body])
    sig = await orch._extract_verify_review_verdict("ISS-1", "ISS-1", Signal.COMPLETE)
    assert sig == Signal.FAIL


@pytest.mark.asyncio
async def test_picks_first_verdict_block_across_comments():
    """If older comments lack a verdict and newer ones have one, the parser
    should find it. fetch_recent_comments is assumed to return newest-first."""
    older = "Some old comment with no verdict block"
    newer = """## Verify Review — APPROVED

<!-- autosymph:verify_review {"decision":"approve"} -->"""
    orch = _orch_with_comments([newer, older])  # newest-first order
    sig = await orch._extract_verify_review_verdict("ISS-1", "ISS-1", Signal.COMPLETE)
    assert sig == Signal.COMPLETE


@pytest.mark.asyncio
async def test_stale_verdict_from_prior_run_is_ignored():
    """Regression: ISSUE-385 / ISSUE-383 cycle bug.

    A reject_structify verdict from run 1 must NOT be re-matched on run 4 if
    run 4's agent failed to post a fresh verdict block. Without filtering,
    the parser walks back through history and re-fires the old verdict on
    every later run, trapping the issue in a verify→verify_review→investigating
    loop forever.
    """
    stale_reject = """## Verify Review — REJECTED (structify)

Infrastructure gap from run 1.

<!-- autosymph:verify_review {"decision":"reject_structify","reasons":["screenshots_not_uploaded"]} -->"""
    # Run 4 dispatched at 2026-04-29T15:10:00Z. Stale comment is from 2026-04-28.
    orch = _orch_with_dated_comments([
        (stale_reject, "2026-04-28T22:56:00.000Z"),
        ("Some unrelated comment from run 3", "2026-04-29T14:48:00.000Z"),
    ])
    sig = await orch._extract_verify_review_verdict(
        "ISS-1", "ISS-1", Signal.COMPLETE,
        min_created_at="2026-04-29T15:10:00.000Z",
    )
    assert sig == Signal.FAIL, (
        f"stale verdict from prior run must be ignored (got {sig.value}). "
        "If this fails, the ISSUE-385 cycle bug is back."
    )


@pytest.mark.asyncio
async def test_fresh_verdict_after_stale_one_is_picked():
    """The newest verdict wins, even if older verdicts exist for the same issue."""
    stale_reject = """## REJECTED (run 1)
<!-- autosymph:verify_review {"decision":"reject_structify","reasons":["a"]} -->"""
    fresh_approve = """## APPROVED (run 4)
<!-- autosymph:verify_review {"decision":"approve"} -->"""
    orch = _orch_with_dated_comments([
        (stale_reject, "2026-04-28T22:56:00.000Z"),
        (fresh_approve, "2026-04-29T15:18:00.000Z"),
    ])
    sig = await orch._extract_verify_review_verdict(
        "ISS-1", "ISS-1", Signal.COMPLETE,
        min_created_at="2026-04-29T15:10:00.000Z",
    )
    assert sig == Signal.COMPLETE


@pytest.mark.asyncio
async def test_stale_filter_handles_z_and_offset_timestamps():
    """Linear uses Z timestamps while local dispatch can use +00:00 timestamps."""
    stale_reject = """## REJECTED (run 1)
<!-- autosymph:verify_review {"decision":"reject_structify","reasons":["a"]} -->"""
    fresh_approve = """## APPROVED (run 4)
<!-- autosymph:verify_review {"decision":"approve"} -->"""
    orch = _orch_with_dated_comments([
        (stale_reject, "2026-04-29T15:09:59.000Z"),
        (fresh_approve, "2026-04-29T15:10:01.000Z"),
    ])
    sig = await orch._extract_verify_review_verdict(
        "ISS-1", "ISS-1", Signal.COMPLETE,
        min_created_at="2026-04-29T15:10:00+00:00",
    )
    assert sig == Signal.COMPLETE


@pytest.mark.asyncio
async def test_newest_verdict_wins_when_unfiltered():
    """Even without min_created_at, comments are sorted newest-first when
    timestamps are present, so a recent approve trumps an older reject."""
    older_reject = """<!-- autosymph:verify_review {"decision":"reject"} -->"""
    newer_approve = """<!-- autosymph:verify_review {"decision":"approve"} -->"""
    # Pass them out-of-order to verify the parser sorts.
    orch = _orch_with_dated_comments([
        (older_reject, "2026-04-28T10:00:00.000Z"),
        (newer_approve, "2026-04-29T10:00:00.000Z"),
    ])
    sig = await orch._extract_verify_review_verdict("ISS-1", "ISS-1", Signal.COMPLETE)
    assert sig == Signal.COMPLETE


@pytest.mark.asyncio
async def test_fetch_failure_returns_fail():
    """Linear API hiccup must not crash — degrade to FAIL safely."""
    orch = Orchestrator.__new__(Orchestrator)
    orch.linear = SimpleNamespace(fetch_recent_comments=AsyncMock(side_effect=RuntimeError("network down")))
    sig = await orch._extract_verify_review_verdict("ISS-1", "ISS-1", Signal.COMPLETE)
    assert sig == Signal.FAIL
