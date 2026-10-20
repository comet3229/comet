"""미니PC 연산 서버: 문서 파싱, 임베딩, LLM 답변 생성. 사용자 문서를 디스크에 저장하지 않는다."""
import json
import os
import secrets

import httpx
from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import parsers

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
CHAT_MODEL = os.getenv("CHAT_MODEL", "qwen2.5:7b")
EMBED_MODEL = os.getenv("EMBED_MODEL", "bge-m3")
API_TOKEN = os.getenv("COMET_TOKEN", "")
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "100"))

SYSTEM_PROMPT = (
    "너는 사용자가 제공한 문서를 근거로 답하는 어시스턴트다. "
    "아래 [자료]에 있는 내용만 사용해 한국어로 답하고, 근거가 된 자료 번호를 [1]처럼 표시해라. "
    "자료에 답이 없으면 모른다고 말해라."
)

app = FastAPI(title="Comet mini PC server")


def check_token(authorization: str = Header(default="")):
    if API_TOKEN and not secrets.compare_digest(authorization, f"Bearer {API_TOKEN}"):
        raise HTTPException(401, "invalid token")


class EmbedRequest(BaseModel):
    texts: list[str]


class Context(BaseModel):
    text: str
    source: str = ""
    page: int | None = None


class ChatRequest(BaseModel):
    question: str
    contexts: list[Context] = []
    history: list[dict] = []


@app.get("/health")
async def health():
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{OLLAMA_URL}/api/tags")
            models = [m["name"] for m in r.json().get("models", [])]
    except httpx.HTTPError:
        models = None
    return {"ok": True, "ollama": models is not None, "models": models,
            "chat_model": CHAT_MODEL, "embed_model": EMBED_MODEL}


@app.post("/parse", dependencies=[Depends(check_token)])
async def parse(file: UploadFile = File(...)):
    data = await file.read()
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, "file too large")
    try:
        chunks = parsers.parse(file.filename or "", data)
    except ValueError as e:
        raise HTTPException(415, str(e))
    except Exception as e:  # 손상된 파일 등
        raise HTTPException(422, f"parse failed: {e}")
    return {"chunks": chunks}


@app.post("/embed", dependencies=[Depends(check_token)])
async def embed(req: EmbedRequest):
    async with httpx.AsyncClient(timeout=300) as client:
        r = await client.post(f"{OLLAMA_URL}/api/embed", json={"model": EMBED_MODEL, "input": req.texts})
    if r.status_code != 200:
        raise HTTPException(502, f"ollama embed error: {r.text}")
    return {"vectors": r.json()["embeddings"]}


def build_messages(req: ChatRequest) -> list[dict]:
    refs = "\n\n".join(
        f"[{i}] ({c.source}{f' p.{c.page}' if c.page else ''})\n{c.text}"
        for i, c in enumerate(req.contexts, start=1)
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        *req.history[-6:],
        {"role": "user", "content": f"[자료]\n{refs or '(없음)'}\n\n[질문]\n{req.question}"},
    ]


@app.post("/chat", dependencies=[Depends(check_token)])
async def chat(req: ChatRequest):
    payload = {"model": CHAT_MODEL, "messages": build_messages(req), "stream": True}

    async def stream():
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("POST", f"{OLLAMA_URL}/api/chat", json=payload) as r:
                if r.status_code != 200:
                    yield f"[오류] LLM 서버 응답 {r.status_code}"
                    return
                async for line in r.aiter_lines():
                    if line:
                        yield json.loads(line).get("message", {}).get("content", "")

    return StreamingResponse(stream(), media_type="text/plain; charset=utf-8")
