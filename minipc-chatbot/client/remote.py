"""미니PC 서버 API 클라이언트."""
from pathlib import Path

import httpx


class Remote:
    def __init__(self, base_url: str, token: str = ""):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.client = httpx.Client(base_url=base_url.rstrip("/"), headers=headers, timeout=600)

    def health(self) -> dict:
        return self.client.get("/health").raise_for_status().json()

    def parse(self, path: Path) -> list[dict]:
        with path.open("rb") as f:
            r = self.client.post("/parse", files={"file": (path.name, f)})
        return r.raise_for_status().json()["chunks"]

    def embed(self, texts: list[str], batch: int = 32) -> list[list[float]]:
        out = []
        for i in range(0, len(texts), batch):
            r = self.client.post("/embed", json={"texts": texts[i:i + batch]})
            out += r.raise_for_status().json()["vectors"]
        return out

    def chat(self, question: str, contexts: list[dict], history: list[dict]):
        body = {"question": question, "contexts": contexts, "history": history}
        with self.client.stream("POST", "/chat", json=body) as r:
            r.raise_for_status()
            yield from r.iter_text()
