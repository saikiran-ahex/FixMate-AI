from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent


@dataclass(slots=True)
class Settings:
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_chat_model: str = os.getenv("OPENAI_CHAT_MODEL", "gpt-5-mini")
    openai_embedding_model: str = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
    embedding_dimensions: int = int(os.getenv("OPENAI_EMBEDDING_DIMENSIONS", "1536"))
    qdrant_url: str = os.getenv("QDRANT_URL", "http://qdrant:6333")
    qdrant_api_key: str = os.getenv("QDRANT_API_KEY", "")
    qdrant_collection: str = os.getenv("QDRANT_COLLECTION", "fixmate_knowledge")
    qdrant_top_k: int = int(os.getenv("QDRANT_TOP_K", "8"))
    rag_chunk_size: int = int(os.getenv("RAG_CHUNK_SIZE", "1200"))
    rag_chunk_overlap: int = int(os.getenv("RAG_CHUNK_OVERLAP", "200"))
    rag_rerank_top_k: int = int(os.getenv("RAG_RERANK_TOP_K", "8"))
    rag_context_chunks: int = int(os.getenv("RAG_CONTEXT_CHUNKS", "4"))
    rag_chunk_preview_chars: int = int(os.getenv("RAG_CHUNK_PREVIEW_CHARS", "900"))
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    database_url: str = os.getenv("DATABASE_URL", "postgresql://fixmate:fixmate@postgres:5432/fixmate")
    uploads_dir: str = os.getenv("UPLOADS_DIR", str(BASE_DIR / "uploads"))
    summary_batch_size: int = int(os.getenv("SUMMARY_BATCH_SIZE", "20"))
    admin_username: str = os.getenv("ADMIN_USERNAME", "admin")
    admin_password: str = os.getenv("ADMIN_PASSWORD", "change-me-admin")

    @property
    def has_openai(self) -> bool:
        return bool(self.openai_api_key)
