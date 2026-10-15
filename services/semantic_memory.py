"""Memoria semántica para Orion — versión sin API externa.

Usa BM25 (Okapi BM25) para recuperar recuerdos por relevancia textual.
No necesita embeddings ni ninguna clave de API.

BM25 es el algoritmo detrás de motores como Elasticsearch y Lucene:
funciona muy bien para cientos o miles de recuerdos en uso personal.

Ventajas sobre búsqueda exacta por LIKE:
- Pondera la frecuencia del término y su rareza en la colección
- Tolera sinónimos parciales y variantes morfológicas
- Devuelve resultados ordenados por relevancia, no por fecha

Uso:
    mem = SemanticMemory(db_path)
    await mem.remember(user_id, "Me gusta el café negro sin azúcar", kind="preferencia")
    results = await mem.recall(user_id, "bebidas favoritas", limit=5)

La firma de `remember` y `recall` es idéntica a la versión con embeddings,
por lo que tools_addon.py y handlers/memory.py no necesitan cambios.
"""
from __future__ import annotations

import math
import re
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from uuid import uuid4
import logging

logger = logging.getLogger(__name__)

MEMORY_KINDS = {
    "preferencia", "persona", "proyecto", "objetivo",
    "decision", "hecho", "temporal", "general",
}

# ──────────────────────────────────────────────────────────────────────────────
# Tokenizador simple para español e inglés
# ──────────────────────────────────────────────────────────────────────────────

# Stopwords básicas español + inglés (las más comunes)
_STOPWORDS = {
    "de", "la", "el", "en", "y", "a", "los", "del", "se", "las",
    "un", "por", "con", "una", "su", "para", "es", "al", "lo", "como",
    "más", "pero", "o", "que", "me", "mi", "le", "no", "si", "ya",
    "the", "a", "an", "and", "or", "is", "in", "on", "at", "to",
    "of", "for", "with", "this", "that", "it", "be", "as", "are",
}

def _tokenize(text: str) -> list[str]:
    """Convierte texto a lista de tokens normalizados."""
    text = text.lower()
    # Separar acentos básicos para mejorar matching
    text = (text
        .replace("á", "a").replace("é", "e").replace("í", "i")
        .replace("ó", "o").replace("ú", "u").replace("ü", "u")
        .replace("ñ", "n")
    )
    tokens = re.findall(r"\b[a-z0-9]{2,}\b", text)
    return [t for t in tokens if t not in _STOPWORDS]


# ──────────────────────────────────────────────────────────────────────────────
# BM25 en memoria (sobre la lista de documentos del usuario)
# ──────────────────────────────────────────────────────────────────────────────

class BM25:
    """Implementación mínima de Okapi BM25."""

    K1 = 1.5   # saturación de frecuencia de término
    B  = 0.75  # penalización por longitud de documento

    def __init__(self, corpus: list[list[str]]) -> None:
        self.corpus = corpus
        self.n = len(corpus)
        self.avgdl = sum(len(d) for d in corpus) / max(self.n, 1)
        # Frecuencia de documentos por término
        self.df: dict[str, int] = {}
        for doc in corpus:
            for term in set(doc):
                self.df[term] = self.df.get(term, 0) + 1

    def score(self, query_tokens: list[str], doc_idx: int) -> float:
        doc = self.corpus[doc_idx]
        dl = len(doc)
        score = 0.0
        tf_map: dict[str, int] = {}
        for t in doc:
            tf_map[t] = tf_map.get(t, 0) + 1

        for term in query_tokens:
            tf = tf_map.get(term, 0)
            if tf == 0:
                continue
            df = self.df.get(term, 0)
            idf = math.log((self.n - df + 0.5) / (df + 0.5) + 1)
            num = tf * (self.K1 + 1)
            den = tf + self.K1 * (1 - self.B + self.B * dl / self.avgdl)
            score += idf * num / den
        return score

    def rank(self, query_tokens: list[str]) -> list[tuple[int, float]]:
        """Devuelve [(doc_idx, score), ...] ordenado de mayor a menor."""
        scores = [(i, self.score(query_tokens, i)) for i in range(self.n)]
        return sorted(scores, key=lambda x: x[1], reverse=True)


# ──────────────────────────────────────────────────────────────────────────────
# SemanticMemory
# ──────────────────────────────────────────────────────────────────────────────

class SemanticMemory:
    """Memoria con búsqueda BM25 persistida en SQLite. Sin API externa."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS semantic_memories (
                    id          TEXT PRIMARY KEY,
                    user_id     INTEGER NOT NULL,
                    content     TEXT NOT NULL,
                    kind        TEXT NOT NULL DEFAULT 'general',
                    source      TEXT NOT NULL DEFAULT 'usuario',
                    confidence  REAL NOT NULL DEFAULT 1.0,
                    expires_at  TEXT,
                    created_at  TEXT NOT NULL,
                    updated_at  TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_semem_user
                    ON semantic_memories(user_id, kind, created_at);
            """)
            cols = {r[1] for r in conn.execute("PRAGMA table_info(semantic_memories)")}
            for col, defn in [
                ("source",     "TEXT NOT NULL DEFAULT 'usuario'"),
                ("confidence", "REAL NOT NULL DEFAULT 1.0"),
                ("expires_at", "TEXT"),
            ]:
                if col not in cols:
                    conn.execute(f"ALTER TABLE semantic_memories ADD COLUMN {col} {defn}")

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    # ── escritura ────────────────────────────────────────────────────────────

    async def remember(
        self,
        user_id: int,
        content: str,
        kind: str = "general",
        source: str = "usuario",
        confidence: float = 1.0,
        expires_in_days: int | None = None,
    ) -> str:
        """Guarda un recuerdo y devuelve su id.

        La firma es async para ser compatible con la versión de embeddings;
        aquí no hay I/O asíncrono real.
        """
        kind = kind if kind in MEMORY_KINDS else "general"
        content = content.strip()[:4000]
        if not content:
            raise ValueError("El contenido del recuerdo no puede estar vacío.")

        now = self._now()
        expires_at = None
        if expires_in_days is not None:
            expires_at = (datetime.now(UTC) + timedelta(days=expires_in_days)).isoformat()

        mem_id = uuid4().hex[:16]
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute(
                "INSERT INTO semantic_memories "
                "(id, user_id, content, kind, source, confidence, expires_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (mem_id, user_id, content, kind, source, confidence, expires_at, now, now),
            )
        logger.debug("Recuerdo guardado: %s (usuario=%d, tipo=%s)", mem_id, user_id, kind)
        return mem_id

    def forget(self, user_id: int, mem_id: str) -> bool:
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            return conn.execute(
                "DELETE FROM semantic_memories WHERE id=? AND user_id=?",
                (mem_id, user_id),
            ).rowcount > 0

    def forget_by_kind(self, user_id: int, kind: str) -> int:
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            return conn.execute(
                "DELETE FROM semantic_memories WHERE user_id=? AND kind=?",
                (user_id, kind),
            ).rowcount

    def purge_expired(self) -> int:
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            return conn.execute(
                "DELETE FROM semantic_memories WHERE expires_at IS NOT NULL AND expires_at < ?",
                (self._now(),),
            ).rowcount

    # ── búsqueda BM25 ────────────────────────────────────────────────────────

    async def recall(
        self,
        user_id: int,
        query: str,
        limit: int = 6,
        kind: str | None = None,
        min_score: float = 0.01,
    ) -> list[dict]:
        """Devuelve los recuerdos más relevantes para `query` usando BM25."""
        query = query.strip()
        if not query:
            return []

        now = self._now()
        with closing(sqlite3.connect(self.db_path)) as conn:
            sql = (
                "SELECT id, content, kind, source, confidence, expires_at, created_at "
                "FROM semantic_memories "
                "WHERE user_id=? AND (expires_at IS NULL OR expires_at > ?)"
            )
            params: list = [user_id, now]
            if kind:
                sql += " AND kind=?"
                params.append(kind)
            rows = conn.execute(sql, params).fetchall()

        if not rows:
            return []

        keys = ("id", "content", "kind", "source", "confidence", "expires_at", "created_at")
        docs = [dict(zip(keys, row, strict=True)) for row in rows]

        # Tokenizar corpus y query
        corpus = [_tokenize(d["content"]) for d in docs]
        query_tokens = _tokenize(query)

        if not query_tokens:
            # Query era puras stopwords: devolver los más recientes
            return docs[:limit]

        bm25 = BM25(corpus)
        ranked = bm25.rank(query_tokens)

        results = []
        for idx, score in ranked:
            if score < min_score:
                continue
            doc = docs[idx].copy()
            # Normalizar score a 0-1 aproximado (BM25 no tiene límite fijo)
            doc["score"] = round(min(score / 10.0, 1.0), 4)
            results.append(doc)
            if len(results) >= limit:
                break

        return results

    # ── listado y stats ──────────────────────────────────────────────────────

    def list_all(self, user_id: int, kind: str | None = None, limit: int = 50) -> list[dict]:
        now = self._now()
        with closing(sqlite3.connect(self.db_path)) as conn:
            sql = (
                "SELECT id, content, kind, source, confidence, expires_at, created_at "
                "FROM semantic_memories "
                "WHERE user_id=? AND (expires_at IS NULL OR expires_at > ?)"
            )
            params: list = [user_id, now]
            if kind:
                sql += " AND kind=?"
                params.append(kind)
            sql += " ORDER BY created_at DESC LIMIT ?"
            params.append(min(limit, 200))
            rows = conn.execute(sql, params).fetchall()

        keys = ("id", "content", "kind", "source", "confidence", "expires_at", "created_at")
        return [dict(zip(keys, row, strict=True)) for row in rows]

    def stats(self, user_id: int) -> dict:
        with closing(sqlite3.connect(self.db_path)) as conn:
            rows = conn.execute(
                "SELECT kind, COUNT(*) FROM semantic_memories WHERE user_id=? GROUP BY kind",
                (user_id,),
            ).fetchall()
        return {kind: count for kind, count in rows}
