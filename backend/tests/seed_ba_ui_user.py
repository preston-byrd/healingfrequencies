"""Seed a Pro user with 1 eigenmode + 1 non-baseline session for UI test of Before/After map."""
import os, uuid, sys
from datetime import datetime, timedelta, timezone
import bcrypt
from pymongo import MongoClient

email = sys.argv[1] if len(sys.argv) > 1 else "ui_ba@example.com"
password = "BaTest9!"
uid = str(uuid.uuid4())

client = MongoClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
db = client[os.environ.get("DB_NAME", "test_database")]

# Cleanup any prior seed with same email
prior = list(db.users.find({"email": email}, {"id": 1}))
for p in prior:
    db.resonance_profiles.delete_many({"user_id": p["id"]})
db.users.delete_many({"email": email})

ph = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
db.users.insert_one({
    "id": uid, "email": email, "name": "BA UI",
    "password_hash": ph, "role": "user",
    "pro_until": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
    "stripe_subscription_status": "active",
    "created_at": datetime.now(timezone.utc).isoformat(),
})

def bands(offsets):
    base = {"sub": -30, "low": -25, "lowmid": -20, "mid": -18, "uppermid": -22, "presence": -28}
    keys = [("sub",20,60),("low",60,250),("lowmid",250,500),("mid",500,2000),("uppermid",2000,4000),("presence",4000,8000)]
    return [{"key":k,"label":k.capitalize(),"lo":lo,"hi":hi,"db":base[k]+offsets.get(k,0)} for k,lo,hi in keys]

now = datetime.now(timezone.utc)
db.resonance_profiles.insert_many([
    {"id": f"eigen-{uuid.uuid4().hex[:8]}", "user_id": uid,
     "created_at": (now - timedelta(days=30)).isoformat(),
     "is_eigenmode": True, "resonance_score": 100,
     "bands": bands({}), "spectrum": [], "confirmed_gaps": []},
    {"id": f"s1-{uuid.uuid4().hex[:6]}", "user_id": uid,
     "created_at": now.isoformat(),
     "is_eigenmode": False, "resonance_score": 70,
     "bands": bands({"low": -1, "mid": -5, "presence": -3}),
     "spectrum": [], "confirmed_gaps": []},
])
print(f"SEEDED: {email} / {password}  uid={uid}")
