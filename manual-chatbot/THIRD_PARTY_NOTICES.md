# 사용한 오픈소스와 라이선스

사내 배포 전에 각 프로젝트의 최신 라이선스를 다시 확인하고, 정식 배포본에는 각 라이선스 원문을 함께 넣으세요.

| 구성 요소 | 용도 | 라이선스 | 받는 곳 |
|---|---|---|---|
| llama.cpp | AI 실행 엔진 | MIT | github.com/ggml-org/llama.cpp |
| Qwen3-4B-Instruct-2507 (GGUF) | 답변 모델 | Apache 2.0 | huggingface.co/Qwen, GGUF 변환본 unsloth |
| BGE-M3 (GGUF) | 의미 검색 모델 | MIT | huggingface.co/BAAI/bge-m3, GGUF 변환본 gpustack |
| Python | 실행 환경 | PSF License | python.org (uv 가 python-build-standalone 으로 설치) |
| uv | 파이썬 설치 도구 | MIT / Apache 2.0 | github.com/astral-sh/uv |
| FastAPI | 웹 서버 | MIT | |
| Uvicorn | 웹 서버 | BSD-3-Clause | |
| HTTPX | 엔진 통신 | BSD-3-Clause | |
| NumPy | 계산 | BSD-3-Clause | |
| pypdfium2 / PDFium | PDF 글자 추출 | Apache 2.0 또는 BSD-3-Clause / BSD-3-Clause | |

일부러 쓰지 않은 것: PyMuPDF(AGPL), Gemma·Llama 모델(자체 사용 제한 라이선스), EXAONE(비상업용), PyQt(GPL).
