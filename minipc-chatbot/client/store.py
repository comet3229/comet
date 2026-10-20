"""사용자 PC에 저장되는 로컬 인덱스(SQLite + 벡터)."""
import sqlite3
from pathlib import Path

import numpy as np


class Store:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS docs (path TEXT PRIMARY KEY, mtime REAL, size INTEGER);
            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY, path TEXT, page INTEGER, text TEXT, vec BLOB);
            CREATE INDEX IF NOT EXISTS chunks_path ON chunks(path);
            """
        )
        self._cache = None

    def doc_state(self) -> dict[str, tuple[float, int]]:
        return {p: (m, s) for p, m, s in self.db.execute("SELECT path, mtime, size FROM docs")}

    def docs(self) -> list[dict]:
        rows = self.db.execute(
            "SELECT d.path, COUNT(c.id) FROM docs d LEFT JOIN chunks c ON c.path = d.path GROUP BY d.path ORDER BY d.path"
        )
        return [{"path": p, "chunks": n} for p, n in rows]

    def upsert(self, path: str, mtime: float, size: int, chunks: list[dict], vectors: list[list[float]]):
        with self.db:
            self.db.execute("DELETE FROM chunks WHERE path = ?", (path,))
            self.db.executemany(
                "INSERT INTO chunks (path, page, text, vec) VALUES (?, ?, ?, ?)",
                [(path, c["page"], c["text"], np.asarray(v, dtype=np.float32).tobytes())
                 for c, v in zip(chunks, vectors)],
            )
            self.db.execute("INSERT OR REPLACE INTO docs VALUES (?, ?, ?)", (path, mtime, size))
        self._cache = None

    def remove(self, path: str):
        with self.db:
            self.db.execute("DELETE FROM chunks WHERE path = ?", (path,))
            self.db.execute("DELETE FROM docs WHERE path = ?", (path,))
        self._cache = None

    def _matrix(self):
        if self._cache is None:
            rows = self.db.execute("SELECT path, page, text, vec FROM chunks").fetchall()
            if rows:
                m = np.stack([np.frombuffer(r[3], dtype=np.float32) for r in rows])
                m /= np.linalg.norm(m, axis=1, keepdims=True) + 1e-9
            else:
                m = np.zeros((0, 0), dtype=np.float32)
            self._cache = ([r[:3] for r in rows], m)
        return self._cache

    def search(self, query_vec: list[float], k: int = 5) -> list[dict]:
        meta, m = self._matrix()
        if not meta:
            return []
        q = np.asarray(query_vec, dtype=np.float32)
        q /= np.linalg.norm(q) + 1e-9
        scores = m @ q
        top = np.argsort(-scores)[:k]
        return [{"source": meta[i][0], "page": meta[i][1], "text": meta[i][2], "score": float(scores[i])}
                for i in top]
