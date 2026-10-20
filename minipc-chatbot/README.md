# Comet — 미니PC 문서 챗봇

PDF · PPT · Word 문서를 근거로 답하는 챗봇.
**문서와 인덱스는 사용자 PC에만 저장**하고, 무거운 연산(문서 파싱 · 임베딩 · LLM)은 **미니PC**가 맡습니다.
두 기기는 **Tailscale**(또는 고정 주소 하나)로 연결합니다.

```
┌──────────── 사용자 PC ────────────┐        Tailscale         ┌──────────── 미니PC ────────────┐
│ ~/Documents/comet  (원본 문서)    │   http://minipc:8000     │ server (FastAPI)               │
│ ~/.comet/index.db  (청크+벡터)    │ ───────────────────────▶ │  /parse  파일→텍스트 청크      │
│ client (127.0.0.1:8501 웹 UI)     │  Bearer 토큰 인증         │  /embed  텍스트→벡터           │
│  - 폴더 동기화, 벡터 검색          │ ◀─────────────────────── │  /chat   근거+질문→답변(스트림)│
└───────────────────────────────────┘                          │ Ollama (qwen2.5, bge-m3)       │
                                                               └────────────────────────────────┘
```

- 서버는 **상태가 없습니다(stateless)**. 업로드된 파일은 메모리에서 파싱한 뒤 바로 버리고, 디스크에 저장하지 않습니다.
- 검색(벡터 유사도)은 사용자 PC에서 처리합니다. 서버에는 질문과 관련 있는 청크 몇 개만 전송됩니다.
- 지원 형식: `.pdf` `.pptx` `.docx` `.txt` `.md` (구형 `.ppt` `.doc`는 새 형식으로 저장한 뒤 사용하세요)

## 1. 미니PC 설정

```bash
# Tailscale 설치 및 로그인
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up            # 기기 이름을 예를 들어 'minipc'로 설정

git clone <this repo> && cd comet/minipc-chatbot
cp .env.example .env         # COMET_TOKEN 을 긴 랜덤 문자열로 변경 (openssl rand -hex 32)
docker compose up -d

# 모델 내려받기 (한 번만)
docker compose exec ollama ollama pull qwen2.5:7b
docker compose exec ollama ollama pull bge-m3
```

확인: `curl http://minipc:8000/health` → `"ollama": true`

**모델 선택 가이드** (CPU만 있는 미니PC 기준)

| RAM | 채팅 모델 | 비고 |
|---|---|---|
| 8GB | `qwen2.5:3b` / `gemma3:4b` | 빠르지만 답변 품질은 보통 |
| 16GB | `qwen2.5:7b` (기본) | 한국어 품질과 속도의 균형 |
| 32GB+ | `qwen2.5:14b` / `gemma3:12b` | 느리지만 정확도 높음 |

임베딩 모델 `bge-m3`는 다국어(한국어 포함) 검색 품질이 좋습니다. 임베딩 모델을 바꾸면 클라이언트 인덱스(`~/.comet/index.db`)를 지우고 다시 동기화해야 합니다.

**보안:** `.env`의 `BIND_ADDR`를 미니PC의 Tailscale IP(`tailscale ip -4`, 100.x.y.z)로 지정하면 tailnet 밖에서는 접속할 수 없습니다. 공유기 포트포워딩은 하지 마세요.

## 2. 사용자 PC 설정

```bash
sudo tailscale up            # 미니PC와 같은 계정으로 로그인 (Windows/Mac은 앱 설치)
cd comet/minipc-chatbot/client
python -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

export COMET_SERVER=http://minipc:8000          # MagicDNS 이름 또는 100.x.y.z
export COMET_TOKEN=<미니PC .env와 같은 값>
export COMET_DOCS_DIR=~/Documents/comet         # 챗봇이 읽을 문서 폴더
python app.py
```

브라우저에서 http://127.0.0.1:8501 을 열고 **문서 동기화**를 누른 뒤 질문하면 됩니다.
문서를 추가 · 수정 · 삭제한 뒤 다시 동기화하면 바뀐 파일만 처리합니다.

## Tailscale 대신 쓸 수 있는 방법

- **Cloudflare Tunnel**: `cloudflared tunnel --url http://localhost:8000` → 도메인 하나로 외부에서 접속. 이때는 `COMET_TOKEN`이 꼭 필요하고, Cloudflare Access를 함께 쓰기를 권장합니다.
- **같은 공유기 안에서만 사용**: `COMET_SERVER=http://192.168.x.x:8000`

## 서버 API

모든 요청(`/health` 제외)에는 `Authorization: Bearer <COMET_TOKEN>` 헤더가 필요합니다.

| 메서드 | 경로 | 입력 | 출력 |
|---|---|---|---|
| GET | `/health` | – | 모델 상태 |
| POST | `/parse` | multipart `file` | `{"chunks": [{"page", "text"}]}` |
| POST | `/embed` | `{"texts": [...]}` | `{"vectors": [[...]]}` |
| POST | `/chat` | `{"question", "contexts": [{"text","source","page"}], "history"}` | 텍스트 스트림 |

## 개발

```bash
pip install -r server/requirements.txt -r client/requirements.txt pytest
pytest tests
```

## 다음 단계 아이디어

- 스캔한 PDF OCR 처리 (미니PC에 `tesseract` / `paddleocr` 추가)
- 폴더 감시(watchdog)로 자동 동기화
- 하이브리드 검색(BM25 + 벡터), 재순위화(reranker)
- 여러 사용자 PC가 미니PC 하나를 공유 (사용자별 토큰 발급)
