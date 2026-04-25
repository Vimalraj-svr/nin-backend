"""
Per-user semantic memory retrieval using MongoDB + numpy cosine similarity.
Replaces the global FAISS index — each user's memories are isolated.
"""
import logging
import numpy as np
from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger(__name__)


async def retrieve_similar(
    db: AsyncIOMotorDatabase,
    user_id: str,
    embedding: list,
    k: int = 3,
    exclude_id: str = None,
) -> list[str]:
    """
    Return up to k past diary snippets most semantically similar to `embedding`
    for the given user. Returns [] if no past entries exist.
    """
    query = {"user_id": user_id, "embedding": {"$exists": True, "$ne": None}}
    if exclude_id:
        query["id"] = {"$ne": exclude_id}

    cursor = db["entries"].find(
        query,
        {"id": 1, "embedding": 1, "content_original": 1, "content_english": 1, "mood_summary": 1},
    )
    docs = await cursor.to_list(length=500)

    if not docs:
        return []

    query_vec = np.array(embedding, dtype=np.float32)
    scored = []
    for doc in docs:
        try:
            vec = np.array(doc["embedding"], dtype=np.float32)
            sim = float(np.dot(query_vec, vec))  # embeddings are L2-normalized
            scored.append((sim, doc))
        except Exception:
            continue

    scored.sort(key=lambda x: x[0], reverse=True)

    snippets = []
    for _, doc in scored[:k]:
        text = doc.get("content_original") or doc.get("content_english") or ""
        mood = doc.get("mood_summary", "")
        snippet = text[:200].replace("\n", " ")
        snippets.append(f"[{mood[:50]}] {snippet}" if mood else snippet)

    return snippets


async def get_recent_entries_for_chat(
    db: AsyncIOMotorDatabase,
    user_id: str,
    limit: int = 20,
) -> list[dict]:
    """
    Return the most recent `limit` entries for a user, with date + full content,
    suitable for building chat context.
    """
    from app.services.encryption_service import decrypt_if_present
    cursor = db["entries"].find(
        {"user_id": user_id},
        {"id": 1, "created_at": 1, "content_original": 1, "content_english": 1,
         "title_original": 1, "title_english": 1, "mood_summary": 1},
    ).sort("created_at", -1).limit(limit)
    docs = await cursor.to_list(length=limit)

    result = []
    for doc in docs:
        content = decrypt_if_present(doc.get("content_original")) or \
                  decrypt_if_present(doc.get("content_english")) or ""
        title = decrypt_if_present(doc.get("title_original")) or \
                decrypt_if_present(doc.get("title_english")) or "Untitled"
        mood = decrypt_if_present(doc.get("mood_summary")) or ""
        date = doc.get("created_at")
        date_str = date.strftime("%d %b %Y") if hasattr(date, "strftime") else str(date)[:10]
        result.append({
            "id": doc.get("id", ""),
            "date": date_str,
            "title": title,
            "content": content,
            "mood": mood,
        })
    return result
