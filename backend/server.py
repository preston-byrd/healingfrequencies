from dotenv import load_dotenv
from pathlib import Path

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

import os
import logging
import uuid
import bcrypt
import jwt
from datetime import datetime, timezone, timedelta
from typing import List, Optional

from fastapi import FastAPI, APIRouter, HTTPException, Request, Response, Depends
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field, EmailStr


# --- Setup --------------------------------------------------------------------
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

JWT_ALGORITHM = "HS256"
JWT_SECRET = os.environ["JWT_SECRET"]

app = FastAPI()
api = APIRouter(prefix="/api")


# --- Auth helpers -------------------------------------------------------------
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def create_access_token(user_id: str, email: str) -> str:
    payload = {
        "sub": user_id, "email": email, "type": "access",
        "exp": datetime.now(timezone.utc) + timedelta(days=7),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def set_auth_cookie(response: Response, token: str):
    response.set_cookie(
        key="access_token", value=token, httponly=True,
        secure=True, samesite="none", max_age=7 * 24 * 3600, path="/",
    )


async def get_current_user(request: Request) -> dict:
    token = request.cookies.get("access_token")
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user = await db.users.find_one({"id": payload["sub"]})
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        user.pop("password_hash", None)
        user.pop("_id", None)
        return user
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


# --- Models -------------------------------------------------------------------
class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6)
    name: Optional[str] = None


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class SessionIn(BaseModel):
    name: str
    frequency: float
    waveform: str = "sine"
    binaural: float = 0
    duration_minutes: int = 10
    ambient: dict = Field(default_factory=dict)  # {rain: 0..1, ocean: 0..1, forest: 0..1}
    breathwork: bool = False


class Session(SessionIn):
    id: str
    user_id: str
    created_at: str


# --- Auth routes --------------------------------------------------------------
@api.post("/auth/register")
async def register(body: RegisterIn, response: Response):
    email = body.email.lower()
    existing = await db.users.find_one({"email": email})
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")
    user = {
        "id": str(uuid.uuid4()),
        "email": email,
        "name": body.name or email.split("@")[0],
        "password_hash": hash_password(body.password),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.users.insert_one(user)
    token = create_access_token(user["id"], email)
    set_auth_cookie(response, token)
    return {"id": user["id"], "email": email, "name": user["name"], "token": token}


@api.post("/auth/login")
async def login(body: LoginIn, response: Response):
    email = body.email.lower()
    user = await db.users.find_one({"email": email})
    if not user or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token = create_access_token(user["id"], email)
    set_auth_cookie(response, token)
    return {"id": user["id"], "email": email, "name": user.get("name", ""), "token": token}


@api.post("/auth/logout")
async def logout(response: Response):
    response.delete_cookie("access_token", path="/")
    return {"ok": True}


@api.get("/auth/me")
async def me(user: dict = Depends(get_current_user)):
    return user


# --- Sessions (favorites) -----------------------------------------------------
@api.get("/sessions")
async def list_sessions(user: dict = Depends(get_current_user)):
    items = await db.sessions.find({"user_id": user["id"]}, {"_id": 0}).sort("created_at", -1).to_list(200)
    return items


@api.post("/sessions")
async def create_session(body: SessionIn, user: dict = Depends(get_current_user)):
    doc = body.model_dump()
    doc.update({
        "id": str(uuid.uuid4()),
        "user_id": user["id"],
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    await db.sessions.insert_one(doc)
    doc.pop("_id", None)
    return doc


@api.delete("/sessions/{sid}")
async def delete_session(sid: str, user: dict = Depends(get_current_user)):
    res = await db.sessions.delete_one({"id": sid, "user_id": user["id"]})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Not found")
    return {"ok": True}


# --- Streak / Check-in --------------------------------------------------------
def _today_utc_date() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _date_minus(d_iso: str, days: int) -> str:
    from datetime import date as _date
    y, m, dd = map(int, d_iso.split("-"))
    return _date.fromordinal(_date(y, m, dd).toordinal() - days).isoformat()


class CheckinIn(BaseModel):
    minutes: float = 0


@api.get("/streak")
async def get_streak(user: dict = Depends(get_current_user)):
    doc = await db.streaks.find_one({"user_id": user["id"]}, {"_id": 0})
    if not doc:
        return {
            "current_streak": 0, "longest_streak": 0, "last_check_in": None,
            "total_sessions": 0, "total_minutes": 0,
            "checked_in_today": False,
        }
    doc["checked_in_today"] = (doc.get("last_check_in") == _today_utc_date())
    # If streak is stale (skipped a day), reflect that without writing
    last = doc.get("last_check_in")
    today = _today_utc_date()
    if last and last != today and last != _date_minus(today, 1):
        doc["current_streak"] = 0
    return doc


@api.post("/streak/checkin")
async def checkin(body: CheckinIn, user: dict = Depends(get_current_user)):
    today = _today_utc_date()
    existing = await db.streaks.find_one({"user_id": user["id"]})
    minutes = max(0.0, float(body.minutes or 0))

    if not existing:
        doc = {
            "user_id": user["id"],
            "current_streak": 1,
            "longest_streak": 1,
            "last_check_in": today,
            "total_sessions": 1,
            "total_minutes": minutes,
        }
        await db.streaks.insert_one(doc)
        doc.pop("_id", None)
        doc["checked_in_today"] = True
        return doc

    last = existing.get("last_check_in")
    current = int(existing.get("current_streak", 0))
    longest = int(existing.get("longest_streak", 0))
    total_sessions = int(existing.get("total_sessions", 0)) + 1
    total_minutes = float(existing.get("total_minutes", 0)) + minutes

    if last == today:
        # Already checked in today — just bump totals.
        pass
    elif last == _date_minus(today, 1):
        current += 1
    else:
        current = 1

    longest = max(longest, current)
    update = {
        "current_streak": current,
        "longest_streak": longest,
        "last_check_in": today,
        "total_sessions": total_sessions,
        "total_minutes": total_minutes,
    }
    await db.streaks.update_one({"user_id": user["id"]}, {"$set": update})
    update["user_id"] = user["id"]
    update["checked_in_today"] = True
    return update


# --- App setup ----------------------------------------------------------------
@api.get("/")
async def root():
    return {"message": "Healing Frequencies API"}


app.include_router(api)

origins = [o.strip() for o in os.environ.get("CORS_ORIGINS", "*").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@app.on_event("startup")
async def startup():
    await db.users.create_index("email", unique=True)
    await db.sessions.create_index([("user_id", 1), ("created_at", -1)])
    await db.streaks.create_index("user_id", unique=True)
    # seed admin
    admin_email = os.environ.get("ADMIN_EMAIL", "admin@example.com").lower()
    admin_password = os.environ.get("ADMIN_PASSWORD", "admin123")
    existing = await db.users.find_one({"email": admin_email})
    if not existing:
        await db.users.insert_one({
            "id": str(uuid.uuid4()),
            "email": admin_email,
            "name": "Admin",
            "password_hash": hash_password(admin_password),
            "role": "admin",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        logger.info(f"Seeded admin: {admin_email}")
    elif not verify_password(admin_password, existing["password_hash"]):
        await db.users.update_one(
            {"email": admin_email},
            {"$set": {"password_hash": hash_password(admin_password)}},
        )


@app.on_event("shutdown")
async def shutdown():
    client.close()
