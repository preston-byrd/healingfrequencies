"""Seed a Pro user staged to earn all 6 HB milestones for UI testing."""
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
from pymongo import MongoClient

MONGO = MongoClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
DB = MONGO[os.environ.get("DB_NAME", "test_database")]

EMAIL = "mileui@example.com"
PW = "Mile9!Mile9!"


def _bands(offsets):
    base = {"sub": -30, "low": -25, "lowmid": -20, "mid": -18, "uppermid": -22, "presence": -28}
    keys = [("sub", 20, 60), ("low", 60, 250), ("lowmid", 250, 500),
            ("mid", 500, 2000), ("uppermid", 2000, 4000), ("presence", 4000, 8000)]
    return [{"key": k, "label": k.capitalize(), "lo": lo, "hi": hi,
             "db": base[k] + offsets.get(k, 0)} for k, lo, hi in keys]


def main():
    # Clean any prior version
    old = DB.users.find_one({"email": EMAIL})
    if old:
        uid = old["id"]
        DB.resonance_profiles.delete_many({"user_id": uid})
        DB.hb_milestones.delete_many({"user_id": uid})
        DB.streaks.delete_many({"user_id": uid})
        DB.users.delete_one({"id": uid})

    uid = str(uuid.uuid4())
    DB.users.insert_one({
        "id": uid,
        "email": EMAIL,
        "name": "Milestone Tester",
        "password_hash": bcrypt.hashpw(PW.encode(), bcrypt.gensalt()).decode(),
        "role": "user",
        "pro_until": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    now = datetime.now(timezone.utc)
    DB.resonance_profiles.insert_many([
        # eigenmode baseline (first_eigenmode + resonance_90)
        {"id": f"eigen-{uuid.uuid4().hex[:6]}", "user_id": uid,
         "created_at": (now - timedelta(days=40)).isoformat(),
         "is_eigenmode": True, "resonance_score": 100,
         "bands": _bands({}), "confirmed_gaps": []},
        # First non-baseline: confirmed low gap + heavy drift all bands
        {"id": f"s1-{uuid.uuid4().hex[:6]}", "user_id": uid,
         "created_at": (now - timedelta(days=20)).isoformat(),
         "is_eigenmode": False, "resonance_score": 55,
         "bands": _bands({"sub": -5, "low": -6, "lowmid": -4, "mid": -5,
                          "uppermid": -4, "presence": -6}),
         "confirmed_gaps": [{"key": "low", "label": "Low", "lo": 60, "hi": 250}]},
        # Latest: everything better + low aligned (< 2 dB)
        {"id": f"s2-{uuid.uuid4().hex[:6]}", "user_id": uid,
         "created_at": now.isoformat(),
         "is_eigenmode": False, "resonance_score": 88,
         "bands": _bands({"sub": -1, "low": -0.5, "lowmid": -0.5, "mid": -1,
                          "uppermid": -1, "presence": -1.5}),
         "confirmed_gaps": []},
    ])

    DB.streaks.insert_one({
        "user_id": uid, "current": 30, "longest": 30,
        "last_checkin_date": now.date().isoformat(),
        "updated_at": now.isoformat(),
    })
    print(f"Seeded uid={uid} email={EMAIL} pw={PW}")


if __name__ == "__main__":
    main()
