import os
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

load_dotenv()

MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017/ninaivugal")

_client: AsyncIOMotorClient = None
_db: AsyncIOMotorDatabase = None


def get_mongo_db() -> AsyncIOMotorDatabase:
    return _db


async def init_db():
    global _client, _db
    _client = AsyncIOMotorClient(MONGODB_URI)
    db_name = MONGODB_URI.split("/")[-1].split("?")[0] or "ninaivugal"
    _db = _client[db_name]

    # Ensure indexes
    await _db["entries"].create_index([("user_id", 1), ("created_at", -1)])
    await _db["users"].create_index([("email", 1)], unique=True)


async def close_db():
    if _client:
        _client.close()
