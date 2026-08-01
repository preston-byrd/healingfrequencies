"""Seed a Pro user for Phase 12c/12d UI verification."""
import os, uuid, bcrypt, sys
from datetime import datetime, timedelta, timezone
from pymongo import MongoClient

EMAIL = "ui_impact_test@example.com"
PW = "Impact9!"

client = MongoClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
db = client[os.environ.get("DB_NAME", "test_database")]

# Cleanup any prior
existing = db.users.find_one({"email": EMAIL})
if existing:
    uid = existing["id"]
    db.resonance_profiles.delete_many({"user_id": uid})
    db.wellness_journey.delete_many({"user_id": uid})
    db.hb_monthly_reports.delete_many({"user_id": uid})
    db.users.delete_one({"id": uid})

uid = str(uuid.uuid4())
now = datetime.now(timezone.utc)
db.users.insert_one({
    "id": uid, "email": EMAIL, "name": "ImpactUI",
    "password_hash": bcrypt.hashpw(PW.encode(), bcrypt.gensalt()).decode(),
    "role": "user",
    "pro_until": (now + timedelta(days=30)).isoformat(),
    "stripe_subscription_status": "active",
    "created_at": now.isoformat(),
})

def bands(offsets):
    base = {"sub": -30, "low": -25, "lowmid": -20, "mid": -18, "uppermid": -22, "presence": -28}
    keys = [("sub",20,60),("low",60,250),("lowmid",250,500),("mid",500,2000),("uppermid",2000,4000),("presence",4000,8000)]
    return [{"key":k,"label":k.capitalize(),"lo":lo,"hi":hi,"db":base[k]+offsets.get(k,0)} for k,lo,hi in keys]

# 1 eigenmode (60 days ago)
# 1 non-eigenmode last month
# 3 non-eigenmode this month (>=2 for monthly report)
db.resonance_profiles.insert_many([
    {"id": f"eig-{uuid.uuid4().hex[:6]}", "user_id": uid, "created_at": (now - timedelta(days=60)).isoformat(),
     "is_eigenmode": True, "resonance_score": 100, "bands": bands({}), "confirmed_gaps": []},
    {"id": f"pm-{uuid.uuid4().hex[:6]}", "user_id": uid, "created_at": (now - timedelta(days=32)).isoformat(),
     "is_eigenmode": False, "resonance_score": 60, "bands": bands({"low": -6, "mid": -5}), "confirmed_gaps": []},
    {"id": f"c1-{uuid.uuid4().hex[:6]}", "user_id": uid, "created_at": (now - timedelta(days=15)).isoformat(),
     "is_eigenmode": False, "resonance_score": 70, "bands": bands({"low": -3, "mid": -5}), "confirmed_gaps": []},
    {"id": f"c2-{uuid.uuid4().hex[:6]}", "user_id": uid, "created_at": (now - timedelta(days=5)).isoformat(),
     "is_eigenmode": False, "resonance_score": 78, "bands": bands({"low": -2, "mid": -4}), "confirmed_gaps": []},
    {"id": f"c3-{uuid.uuid4().hex[:6]}", "user_id": uid, "created_at": (now - timedelta(days=1)).isoformat(),
     "is_eigenmode": False, "resonance_score": 82, "bands": bands({"low": -1, "mid": -4}), "confirmed_gaps": []},
])

# Rated wellness_journey entries: 3x 432 Hz (2 clear + 1 subtle), 2x 528 Hz (2 subtle)
rows = []
for _ in range(2):
    rows.append({"id": uuid.uuid4().hex, "user_id": uid, "frequency": 432.0, "hb_recommended": True,
                 "impact_rating": "clear_shift", "created_at": (now - timedelta(days=3)).isoformat(),
                 "preset_label": "432 Hz Earth", "duration_actual_seconds": 900})
rows.append({"id": uuid.uuid4().hex, "user_id": uid, "frequency": 432.0, "hb_recommended": True,
             "impact_rating": "subtle_difference", "created_at": (now - timedelta(days=2)).isoformat(),
             "preset_label": "432 Hz Earth", "duration_actual_seconds": 500})
for _ in range(2):
    rows.append({"id": uuid.uuid4().hex, "user_id": uid, "frequency": 528.0, "hb_recommended": True,
                 "impact_rating": "subtle_difference", "created_at": (now - timedelta(days=4)).isoformat(),
                 "preset_label": "528 Hz Cellular", "duration_actual_seconds": 500})
# 1 unrated HB-recommended, ≥26h old (triggers prompt)
rows.append({"id": "prompt-me", "user_id": uid, "frequency": 396.0, "hb_recommended": True,
             "created_at": (now - timedelta(hours=26)).isoformat(),
             "preset_label": "396 Hz Grounding", "duration_actual_seconds": 900})
db.wellness_journey.insert_many(rows)
print(f"Seeded {EMAIL} / {PW} uid={uid}")
