"""Backend test for the Feb 2026 webhook idempotency race fix.

The prior `_fulfill_payment` implementation had a classic check-then-act
race:

    if tx.get("fulfilled"):
        return
    ... extend pro_until by N days ...
    ... mark fulfilled=True ...

Two concurrent `checkout.session.completed` webhook retries could both
read `fulfilled: false`, both call `_fulfill_payment`, and both extend
`pro_until` — silently double-granting the user's Pro period.

The fix flipped the ordering to an ATOMIC claim:

    result = db.payment_transactions.update_one(
        {"session_id": sid, "fulfilled": {"$ne": True}},
        {"$set": {"fulfilled": True, "fulfilled_at": ...}}
    )
    if result.modified_count == 0: return
    ... extend pro_until by N days ...

Under concurrency exactly one caller wins the update, the others return
immediately. This test proves it by firing many concurrent calls against
a seeded one-time-mode transaction and asserting pro_until was extended
exactly ONCE.
"""
import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone, timedelta

import pytest

sys.path.insert(0, "/app/backend")
import server as server_mod  # noqa: E402


def _run(coro_factory):
    """Fresh event loop + rebound Motor client for asyncio isolation."""
    from motor.motor_asyncio import AsyncIOMotorClient
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        new_client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        new_db = new_client[os.environ["DB_NAME"]]
        server_mod.client = new_client
        server_mod.db = new_db
        return loop.run_until_complete(coro_factory())
    finally:
        loop.close()


def test_concurrent_fulfill_only_one_grants_pro_days():
    """Fire 10 concurrent _fulfill_payment calls against the same one-time
    tx (mode != subscription, days=30). Exactly one must succeed and the
    user's pro_until must be extended by exactly 30 days — never 60, 90,
    or more, no matter how many retries pile up."""
    uid = f"webhook-race-user-{uuid.uuid4().hex[:8]}"
    sid = f"cs_test_{uuid.uuid4().hex[:24]}"

    async def _go():
        db = server_mod.db
        # Seed a fresh Free user
        await db.users.insert_one({
            "id": uid,
            "email": f"{uid}@example.com",
            "name": "Race Test",
            "plan": "free",
        })
        # Seed a pending one-time payment_transactions row for 30 days
        await db.payment_transactions.insert_one({
            "session_id": sid,
            "user_id": uid,
            "email": f"{uid}@example.com",
            "plan": "pro_30d",
            "amount": 500,
            "days": 30,
            "mode": "onetime",
            "status": "pending",
            "fulfilled": False,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })

        # Read the tx once — this is what the webhook handler does before
        # calling _fulfill_payment. Every concurrent caller uses this
        # SAME stale dict so we're simulating the retry race exactly.
        tx = await db.payment_transactions.find_one({"session_id": sid})
        assert tx and not tx.get("fulfilled")

        # Fire N concurrent fulfillments — should be exactly one grant.
        N = 10
        results = await asyncio.gather(
            *[server_mod._fulfill_payment(tx) for _ in range(N)],
            return_exceptions=True,
        )
        # None of the concurrent calls should raise — they should silently
        # no-op when the atomic claim is lost.
        for r in results:
            assert not isinstance(r, Exception), f"unexpected exception: {r}"

        # User should now be Pro, extended by exactly 30 days from ~now.
        user = await db.users.find_one({"id": uid})
        assert user["plan"] == "pro"
        pro_until = datetime.fromisoformat(user["pro_until"])
        # Compare in tz-aware naive-safe form
        if pro_until.tzinfo is None:
            pro_until = pro_until.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        # Should be ~30 days from now. Allow a 60-second wall-clock jitter.
        delta = pro_until - now
        assert timedelta(days=29, hours=23) <= delta <= timedelta(days=30, minutes=1), (
            f"pro_until should be exactly ~30 days out (single grant), "
            f"got delta={delta} (indicates a double-grant race)"
        )
        # And the tx should be marked fulfilled exactly once.
        tx_final = await db.payment_transactions.find_one({"session_id": sid})
        assert tx_final.get("fulfilled") is True
        assert tx_final.get("fulfilled_at")

        # Cleanup
        await db.users.delete_one({"id": uid})
        await db.payment_transactions.delete_one({"session_id": sid})

    _run(_go)


def test_fulfill_no_op_when_already_fulfilled():
    """Sanity: calling _fulfill_payment on an already-fulfilled tx must
    return quickly without touching the user doc."""
    uid = f"webhook-idem-user-{uuid.uuid4().hex[:8]}"
    sid = f"cs_test_{uuid.uuid4().hex[:24]}"

    async def _go():
        db = server_mod.db
        # User already Pro with a specific pro_until (from a prior fulfilment)
        fixed_until = (datetime.now(timezone.utc) + timedelta(days=15)).isoformat()
        await db.users.insert_one({
            "id": uid,
            "email": f"{uid}@example.com",
            "name": "Idem Test",
            "plan": "pro",
            "pro_until": fixed_until,
        })
        await db.payment_transactions.insert_one({
            "session_id": sid,
            "user_id": uid,
            "email": f"{uid}@example.com",
            "plan": "pro_30d",
            "amount": 500,
            "days": 30,
            "mode": "onetime",
            "status": "paid",
            "fulfilled": True,
            "fulfilled_at": datetime.now(timezone.utc).isoformat(),
            "created_at": datetime.now(timezone.utc).isoformat(),
        })

        tx = await db.payment_transactions.find_one({"session_id": sid})
        # A retry arrives — must be a silent no-op.
        await server_mod._fulfill_payment(tx)

        user = await db.users.find_one({"id": uid})
        # pro_until must NOT have been extended
        assert user["pro_until"] == fixed_until, (
            "already-fulfilled tx must be a no-op — pro_until must not extend"
        )

        await db.users.delete_one({"id": uid})
        await db.payment_transactions.delete_one({"session_id": sid})

    _run(_go)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
