"""매뉴얼 챗봇 (테스트판)

docs 폴더의 PDF 매뉴얼을 읽어 두었다가, 질문이 오면 관련 부분을 찾아
노트북 안의 AI 모델(llama.cpp)로 답한다. 문서 내용은 밖으로 나가지 않는다.
"""

import hashlib
import json
import math
import os
import re
import subprocess
import threading
import time
import webbrowser
from collections import Counter, defaultdict
from pathlib import Path

import httpx
import numpy as np
import pypdfium2 as pdfium
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent
DOCS = ROOT / "docs"
DATA = ROOT / "data"
CACHE = DATA / "cache"
LOGS = DATA / "logs"

HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8000"))
GPU_LAYERS = os.environ.get("GPU_LAYERS", "99")  # 0 이면 그래픽 가속 없이 CPU 만 쓴다
CTX = os.environ.get("CTX", "8192")
TOP_K = int(os.environ.get("TOP_K", "6"))
CHAT_PORT, EMBED_PORT = 8081, 8082

CHUNK_SIZE, CHUNK_OVERLAP = 700, 120

STATUS = {"phase": "starting", "message": "준비 중", "embed": False}
PROCS = []


def set_status(phase, message):
    STATUS["phase"], STATUS["message"] = phase, message
    print(f"[{phase}] {message}", flush=True)


# ── AI 엔진 (llama-server) ─────────────────────────────────────────────

def find_server_bin():
    override = os.environ.get("LLAMA_SERVER")
    if override:
        return Path(override)
    for name in ("llama-server.exe", "llama-server"):
        for p in (ROOT / "engine").rglob(name):
            return p
    return None


def find_model(kind):
    files = sorted((ROOT / "models" / kind).glob("*.gguf"))
    return files[0] if files else None


def start_server(bin_path, model, port, extra, log_name):
    LOGS.mkdir(parents=True, exist_ok=True)
    log = open(LOGS / log_name, "w", encoding="utf-8", errors="replace")
    args = [str(bin_path), "-m", str(model), "--host", "127.0.0.1", "--port", str(port),
            "-ngl", GPU_LAYERS, "-np", "1", *extra]
    # 같은 콘솔을 쓰게 두어서, 창을 닫거나 Ctrl+C 하면 함께 꺼지게 한다
    proc = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, cwd=str(bin_path.parent))
    PROCS.append(proc)
    return proc


def wait_ready(proc, port, what, timeout=300):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"{what} 엔진이 꺼졌어요. data/logs 의 기록을 확인해 주세요.")
        try:
            if httpx.get(f"http://127.0.0.1:{port}/health", timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    raise RuntimeError(f"{what} 모델을 {timeout}초 안에 불러오지 못했어요.")


def stop_servers():
    for p in PROCS:
        if p.poll() is None:
            p.terminate()


def embed(texts):
    vecs = []
    for i in range(0, len(texts), 16):
        r = httpx.post(f"http://127.0.0.1:{EMBED_PORT}/v1/embeddings",
                       json={"input": texts[i:i + 16], "model": "embed"}, timeout=300)
        r.raise_for_status()
        vecs.extend(d["embedding"] for d in sorted(r.json()["data"], key=lambda d: d["index"]))
    v = np.asarray(vecs, dtype=np.float32)
    return v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-12)


# ── 문서 읽기 ─────────────────────────────────────────────────────────

def clean(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace(" ", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def extract_pdf(path):
    """쪽마다 (쪽 번호, 글자) 를 돌려준다."""
    pdf = pdfium.PdfDocument(str(path))
    try:
        pages = []
        for i in range(len(pdf)):
            page = pdf[i]
            tp = page.get_textpage()
            pages.append((i + 1, clean(tp.get_text_range())))
            tp.close()
            page.close()
        return pages
    finally:
        pdf.close()


EXTRACTORS = {".pdf": extract_pdf}  # Word 는 여기에 ".docx" 를 더하면 된다


def chunk_page(text):
    out, start, n = [], 0, len(text)
    while start < n:
        end = min(n, start + CHUNK_SIZE)
        if end < n:
            lo = start + CHUNK_SIZE // 2
            cuts = [(text.rfind(sep, lo, end), len(sep)) for sep in ("\n", "다.", ". ", "요.")]
            pos, size = max(cuts)
            if pos > start:
                end = pos + size
        piece = text[start:end].strip()
        if len(piece) >= 20:
            out.append(piece)
        if end >= n:
            break
        start = max(end - CHUNK_OVERLAP, start + 1)
    return out


# ── 낱말 검색 (BM25, 한글은 두 글자씩 끊어서) ───────────────────────────

WORD = re.compile(r"[0-9a-z]+(?:[._-][0-9a-z]+)*|[가-힣]+")


def tokens(text):
    out = []
    for w in WORD.findall(text.lower()):
        if "가" <= w[0] <= "힣" and len(w) > 1:
            out.extend(w[i:i + 2] for i in range(len(w) - 1))
        else:
            out.append(w)
    return out


class BM25:
    def __init__(self, docs, k1=1.2, b=0.75):
        self.k1, self.b = k1, b
        self.post = defaultdict(list)
        self.lens = []
        for i, d in enumerate(docs):
            tf = Counter(tokens(d))
            self.lens.append(sum(tf.values()))
            for t, c in tf.items():
                self.post[t].append((i, c))
        self.n = len(docs)
        self.avg = (sum(self.lens) / self.n) if self.n else 1

    def top(self, query, k):
        scores = defaultdict(float)
        for t in set(tokens(query)):
            plist = self.post.get(t)
            if not plist:
                continue
            idf = math.log(1 + (self.n - len(plist) + 0.5) / (len(plist) + 0.5))
            for i, c in plist:
                norm = c + self.k1 * (1 - self.b + self.b * self.lens[i] / self.avg)
                scores[i] += idf * c * (self.k1 + 1) / norm
        return sorted(scores, key=scores.get, reverse=True)[:k]


# ── 색인 ──────────────────────────────────────────────────────────────

class Index:
    def __init__(self):
        self.lock = threading.Lock()
        self.chunks = []      # {"file", "page", "text"}
        self.files = []       # {"name", "pages", "chunks", "note"}
        self.emb = None
        self.bm25 = BM25([])

    def build(self):
        DOCS.mkdir(exist_ok=True)
        CACHE.mkdir(parents=True, exist_ok=True)
        embed_model = find_model("embed") if STATUS["embed"] else None
        paths = sorted(p for p in DOCS.rglob("*") if p.suffix.lower() in EXTRACTORS and p.is_file())
        chunks, files, embs = [], [], []
        for n, path in enumerate(paths, 1):
            name = path.relative_to(DOCS).as_posix()
            set_status("indexing", f"문서 읽는 중 {n}/{len(paths)}: {name}")
            st = path.stat()
            sig = f"{name}|{st.st_size}|{st.st_mtime_ns}|{embed_model.name if embed_model else '-'}"
            key = CACHE / hashlib.sha1(sig.encode()).hexdigest()
            try:
                if key.with_suffix(".json").exists():
                    meta = json.loads(key.with_suffix(".json").read_text(encoding="utf-8"))
                    vec = np.load(key.with_suffix(".npy")) if embed_model else None
                else:
                    pages = EXTRACTORS[path.suffix.lower()](path)
                    items = [{"page": pg, "text": c} for pg, t in pages for c in chunk_page(t)]
                    meta = {"pages": len(pages), "items": items}
                    vec = embed([f"{name}\n{it['text']}" for it in items]) if embed_model and items else None
                    key.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
                    if vec is not None:
                        np.save(key.with_suffix(".npy"), vec)
            except Exception as e:  # 한 문서가 깨져도 나머지는 계속 읽는다
                files.append({"name": name, "pages": 0, "chunks": 0, "note": f"읽기 실패: {e}"})
                continue
            items = meta["items"]
            note = "" if items else "글자를 찾지 못했어요 (스캔한 PDF 일 수 있어요)"
            files.append({"name": name, "pages": meta["pages"], "chunks": len(items), "note": note})
            chunks.extend({"file": name, **it} for it in items)
            if embed_model and items:
                embs.append(vec)
        with self.lock:
            self.chunks, self.files = chunks, files
            self.emb = np.vstack(embs) if embs else None
            self.bm25 = BM25([f"{c['file']} {c['text']}" for c in chunks])

    def search(self, query, k=TOP_K):
        with self.lock:
            chunks, emb, bm25 = self.chunks, self.emb, self.bm25
        if not chunks:
            return []
        fused = defaultdict(float)
        for rank, i in enumerate(bm25.top(query, 30)):
            fused[i] += 1 / (60 + rank)
        if emb is not None:
            sims = emb @ embed([query])[0]
            for rank, i in enumerate(np.argsort(-sims)[:30]):
                fused[int(i)] += 1 / (60 + rank)
        best = sorted(fused, key=fused.get, reverse=True)[:k]
        return [chunks[i] for i in best]


INDEX = Index()


def boot():
    try:
        bin_path = find_server_bin()
        chat_model = find_model("chat")
        if not bin_path or not chat_model:
            raise RuntimeError("AI 엔진이나 모델 파일이 없어요. setup.bat 을 먼저 실행해 주세요.")
        set_status("loading", "AI 모델 불러오는 중 (처음엔 1분쯤 걸려요)")
        chat = start_server(bin_path, chat_model, CHAT_PORT, ["-c", CTX, "--jinja"], "chat.log")
        embed_model = find_model("embed")
        if embed_model:
            emb = start_server(bin_path, embed_model, EMBED_PORT,
                               ["--embedding", "-c", "2048", "-b", "2048", "-ub", "2048"], "embed.log")
            wait_ready(emb, EMBED_PORT, "검색")
            STATUS["embed"] = True
        wait_ready(chat, CHAT_PORT, "답변")
        reindex()
    except Exception as e:
        set_status("error", str(e))


def reindex():
    try:
        INDEX.build()
        set_status("ready", f"문서 {len(INDEX.files)}개, 조각 {len(INDEX.chunks)}개 준비됨")
    except Exception as e:
        set_status("error", f"문서를 읽다가 문제가 생겼어요: {e}")


# ── 답변 ──────────────────────────────────────────────────────────────

SYSTEM = (
    "너는 사내 매뉴얼 도우미다. 사용자가 준 [자료] 에 있는 내용만 근거로 한국어로 답한다.\n"
    "- 자료에 답이 없으면 '매뉴얼에서 찾지 못했어요.' 라고만 말하고 추측하지 않는다.\n"
    "- 근거가 된 자료 번호를 문장 끝에 [1], [2] 처럼 붙인다.\n"
    "- 절차는 1. 2. 3. 번호 목록으로, 짧고 정확하게 쓴다."
)


def build_prompt(question, hits):
    parts = [f"[{n}] ({h['file']} {h['page']}쪽)\n{h['text']}" for n, h in enumerate(hits, 1)]
    return f"[자료]\n" + "\n\n".join(parts) + f"\n\n[질문]\n{question}"


def answer_stream(question):
    def line(obj):
        return json.dumps(obj, ensure_ascii=False) + "\n"

    try:
        hits = INDEX.search(question)
    except Exception as e:
        yield line({"type": "error", "text": f"검색 중 문제가 생겼어요: {e}"})
        return
    yield line({"type": "sources", "sources": [
        {"n": n, "file": h["file"], "page": h["page"], "text": h["text"]} for n, h in enumerate(hits, 1)]})
    if not hits:
        yield line({"type": "delta", "text": "읽어 둔 문서가 없어요. docs 폴더에 PDF 를 넣고 '다시 읽기' 를 눌러 주세요."})
        yield line({"type": "done"})
        return
    body = {
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": build_prompt(question, hits)}],
        "stream": True, "temperature": 0.2, "max_tokens": 1024,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    try:
        with httpx.stream("POST", f"http://127.0.0.1:{CHAT_PORT}/v1/chat/completions",
                          json=body, timeout=httpx.Timeout(600, connect=5)) as r:
            r.raise_for_status()
            for raw in r.iter_lines():
                if not raw.startswith("data:"):
                    continue
                data = raw[5:].strip()
                if data == "[DONE]":
                    break
                delta = json.loads(data)["choices"][0].get("delta", {}).get("content")
                if delta:
                    yield line({"type": "delta", "text": delta})
        yield line({"type": "done"})
    except Exception as e:
        yield line({"type": "error", "text": f"AI 답변 중 문제가 생겼어요: {e}"})


# ── 웹 화면 ───────────────────────────────────────────────────────────

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


class Ask(BaseModel):
    question: str


@app.get("/")
def home():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/status")
def status():
    return {**STATUS, "files": INDEX.files}


@app.post("/api/reindex")
def api_reindex():
    if STATUS["phase"] in ("starting", "loading", "indexing"):
        raise HTTPException(409, "아직 준비 중이에요.")
    threading.Thread(target=reindex, daemon=True).start()
    return {"ok": True}


@app.post("/api/open-docs")
def open_docs():
    DOCS.mkdir(exist_ok=True)
    if os.name == "nt":
        os.startfile(DOCS)  # 탐색기로 docs 폴더를 연다
        return {"ok": True}
    return {"ok": False, "path": str(DOCS)}


@app.post("/api/ask")
def ask(req: Ask):
    q = req.question.strip()
    if not q:
        raise HTTPException(400, "질문을 입력해 주세요.")
    if STATUS["phase"] != "ready":
        raise HTTPException(409, STATUS["message"])
    return StreamingResponse(answer_stream(q[:2000]), media_type="application/x-ndjson")


@app.get("/file/{name:path}")
def doc_file(name: str):
    path = (DOCS / name).resolve()
    if DOCS.resolve() not in path.parents or not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type="application/pdf" if path.suffix.lower() == ".pdf" else None,
                        content_disposition_type="inline")


if __name__ == "__main__":
    threading.Thread(target=boot, daemon=True).start()
    url = f"http://{'127.0.0.1' if HOST == '0.0.0.0' else HOST}:{PORT}"
    print(f"\n  매뉴얼 챗봇: {url}  (끄려면 이 창을 닫거나 Ctrl+C)\n", flush=True)
    if os.environ.get("NO_BROWSER") != "1":
        threading.Timer(1.5, webbrowser.open, [url]).start()
    try:
        uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
    finally:
        stop_servers()
