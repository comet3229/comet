"""사용자 PC에서 실행하는 로컬 앱: 문서 폴더 동기화 + 채팅 UI (http://127.0.0.1:8501)."""
import json
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from remote import Remote
from store import Store

SUPPORTED = {".pdf", ".pptx", ".docx", ".txt", ".md"}
DOCS_DIR = Path(os.getenv("COMET_DOCS_DIR", Path.home() / "Documents" / "comet")).expanduser()
DATA_DIR = Path(os.getenv("COMET_DATA_DIR", Path.home() / ".comet")).expanduser()
TOP_K = int(os.getenv("COMET_TOP_K", "5"))

remote = Remote(os.getenv("COMET_SERVER", "http://minipc:8000"), os.getenv("COMET_TOKEN", ""))
store = Store(DATA_DIR / "index.db")
app = FastAPI(title="Comet client")


def sync() -> dict:
    """문서 폴더를 스캔해 바뀐 파일만 서버로 보내 인덱싱한다."""
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    known = store.doc_state()
    seen, added, failed = set(), [], []
    for path in sorted(DOCS_DIR.rglob("*")):
        if path.suffix.lower() not in SUPPORTED or not path.is_file() or path.name.startswith("~$"):
            continue
        rel = str(path.relative_to(DOCS_DIR))
        seen.add(rel)
        st = path.stat()
        if known.get(rel) == (st.st_mtime, st.st_size):
            continue
        try:
            chunks = remote.parse(path)
            vectors = remote.embed([c["text"] for c in chunks]) if chunks else []
            store.upsert(rel, st.st_mtime, st.st_size, chunks, vectors)
            added.append(rel)
        except Exception as e:
            failed.append({"path": rel, "error": str(e)})
    removed = [p for p in known if p not in seen]
    for p in removed:
        store.remove(p)
    return {"docs_dir": str(DOCS_DIR), "indexed": added, "removed": removed, "failed": failed}


class Ask(BaseModel):
    question: str
    history: list[dict] = []


@app.get("/")
def index():
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.get("/api/status")
def status():
    try:
        server = remote.health()
    except Exception as e:
        server = {"ok": False, "error": str(e)}
    return {"server": server, "docs_dir": str(DOCS_DIR), "docs": store.docs()}


@app.post("/api/sync")
def api_sync():
    return sync()


@app.post("/api/ask")
def ask(req: Ask):
    """NDJSON 스트림: 먼저 {"sources": [...]}, 이후 {"token": "..."}."""
    def stream():
        try:
            hits = store.search(remote.embed([req.question])[0], TOP_K)
            yield json.dumps({"sources": hits}, ensure_ascii=False) + "\n"
            contexts = [{"text": h["text"], "source": h["source"], "page": h["page"]} for h in hits]
            for token in remote.chat(req.question, contexts, req.history):
                yield json.dumps({"token": token}, ensure_ascii=False) + "\n"
        except Exception as e:
            yield json.dumps({"error": str(e)}, ensure_ascii=False) + "\n"

    return StreamingResponse(stream(), media_type="application/x-ndjson")


if __name__ == "__main__":
    import uvicorn

    # 로컬에서만 접근 가능하도록 127.0.0.1 에 바인딩
    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("COMET_PORT", "8501")))
