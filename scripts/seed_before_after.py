"""Seed / cleanup helper for BeforeAfterMap testing."""
import asyncio, os, sys, uuid
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
load_dotenv('/app/backend/.env')
from motor.motor_asyncio import AsyncIOMotorClient

ADMIN_EMAIL = 'admin@example.com'
BANDS_BASE = [
    {"key": "sub",      "label": "Sub-bass grounding", "lo": 20,   "hi": 60,    "db": -35.0},
    {"key": "low",      "label": "Lower grounding",    "lo": 60,   "hi": 250,   "db": -28.0},
    {"key": "lowmid",   "label": "Warm-body",          "lo": 250,  "hi": 500,   "db": -22.0},
    {"key": "mid",      "label": "Mid-harmonic",       "lo": 500,  "hi": 2000,  "db": -20.0},
    {"key": "uppermid", "label": "Upper-mid clarity",  "lo": 2000, "hi": 4000,  "db": -25.0},
    {"key": "presence", "label": "Presence / brilliance","lo":4000,"hi": 8000,  "db": -30.0},
]
# For latest, shift some bands to be aligned and some to drift
BANDS_LATEST_DELTA = {"sub": +6.0, "low": +0.5, "lowmid": -0.3, "mid": +1.0, "uppermid": -5.0, "presence": +0.2}


async def main(mode: str):
    c = AsyncIOMotorClient(os.environ['MONGO_URL'])
    db = c[os.environ['DB_NAME']]
    u = await db.users.find_one({'email': ADMIN_EMAIL}, {'_id': 0, 'id': 1})
    uid = u['id']

    if mode == 'clean':
        r = await db.resonance_profiles.delete_many({'user_id': uid})
        print(f'deleted {r.deleted_count}')
        return

    if mode in ('eigen', 'both'):
        # Clean first
        await db.resonance_profiles.delete_many({'user_id': uid})
        eigen_ts = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc).isoformat()
        eigen_doc = {
            "id": str(uuid.uuid4()),
            "user_id": uid,
            "created_at": eigen_ts,
            "version": 1,
            "sample_rate": 44100,
            "duration": 20,
            "fft_size": 4096,
            "spectrum": [0.0]*32,
            "dominant": [],
            "dips": [],
            "bands": BANDS_BASE,
            "underrepresented": [],
            "confirmed_gaps": [],
            "generated_at": eigen_ts,
            "is_eigenmode": True,
            "resonance_score": 100,
        }
        await db.resonance_profiles.insert_one(eigen_doc)
        print(f'seeded eigenmode {eigen_doc["id"]} at {eigen_ts}')

    if mode == 'both':
        latest_ts = datetime(2026, 8, 5, 12, 0, 0, tzinfo=timezone.utc).isoformat()
        latest_bands = [dict(b, db=b['db'] + BANDS_LATEST_DELTA[b['key']]) for b in BANDS_BASE]
        latest_doc = {
            "id": str(uuid.uuid4()),
            "user_id": uid,
            "created_at": latest_ts,
            "version": 1,
            "sample_rate": 44100,
            "duration": 20,
            "fft_size": 4096,
            "spectrum": [0.0]*32,
            "dominant": [],
            "dips": [],
            "bands": latest_bands,
            "underrepresented": [],
            "confirmed_gaps": [],
            "generated_at": latest_ts,
            "is_eigenmode": False,
            "resonance_score": 82,
        }
        await db.resonance_profiles.insert_one(latest_doc)
        print(f'seeded latest {latest_doc["id"]} at {latest_ts}')

    n = await db.resonance_profiles.count_documents({'user_id': uid})
    print(f'total profiles for admin: {n}')


if __name__ == '__main__':
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else 'eigen'))
