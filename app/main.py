import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.db import init_db, close_db
from app.routers import entries, health, auth, reminders, social

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Ninaivugal API…")
    await init_db()
    logger.info("Database ready.")
    yield
    logger.info("Shutting down.")
    await close_db()


app = FastAPI(
    title="Ninaivugal API",
    description="நினைவுகள் — Multilingual AI Diary.",
    version="2.0.0",
    lifespan=lifespan,
)

_raw_origins = os.getenv("ALLOWED_ORIGINS", "*")
_origins = [o.strip() for o in _raw_origins.split(",")] if _raw_origins != "*" else ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(entries.router)
app.include_router(auth.router)
app.include_router(reminders.router)
app.include_router(social.router)


@app.get("/", tags=["root"])
async def root():
    return {"message": "நினைவுகள் — Ninaivugal API v2.0", "docs": "/docs"}
