import asyncio
import os

import chromadb
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from google import genai
from google.genai import errors, types
from pydantic import BaseModel

load_dotenv()

MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
FALLBACK_MODEL = os.getenv("GEMINI_FALLBACK_MODEL")
EMBED_MODEL = os.getenv("GEMINI_EMBED_MODEL", "gemini-embedding-001")
client = genai.Client()

# ── RAG 설정 ──────────────────────────────────────────
TOP_K = 3              # 근거로 넘길 문항 수
MAX_DISTANCE = 0.40    # 1등 거리가 이보다 멀면 '근거 없음' (eval_search.py 결과로 조정)
NO_ANSWER = "해당 내용은 매뉴얼에서 찾지 못했어요. 앱의 '설정 > 고객센터 > 1:1 문의'로 문의해 주세요."

def is_no_answer(text: str) -> bool:
    """AI가 기호·띄어쓰기를 조금 바꿔도 '근거 없음' 답변인지 판별"""
    return "매뉴얼에서찾지못했" in text.replace(" ", "")

try:
    collection = chromadb.PersistentClient(path="chroma_db").get_collection("manual")
except Exception:
    raise RuntimeError("chroma_db가 없어요. 먼저 'python ingest.py'를 실행하세요.")

SYSTEM_PROMPT = f"""너는 소상공인용 POS 앱 '스마트포스'의 상담 도우미야.
상대는 카페·식당을 운영하는 사장님이고, IT에 익숙하지 않아.

규칙:
- 반드시 [매뉴얼 근거]에 있는 내용으로만 답해. 네가 원래 알고 있는 지식으로 보충하지 마.
- [매뉴얼 근거]에 질문의 답이 없으면 지어내지 말고 정확히 이렇게 답해: {NO_ANSWER}
- 메뉴 이름, 숫자, 기간은 근거에 적힌 그대로 써.
- 결제 취소·환불을 대신 처리해 준다고 약속하지 마.

답변 형식:
- 한국어 존댓말로, 짧고 쉽게.
- 따라 해야 하는 순서가 있으면 1. 2. 3. 번호 목록으로.
- 굵게(**)와 제목(#) 같은 마크다운은 쓰지 마.
- 메뉴 경로는 '설정 > 고객센터 > 1:1 문의'처럼 근거에 적힌 그대로 > 기호와 작은따옴표를 써.

예시)
질문: 밤에 상담원이랑 통화할 수 있어요?
답변: 전화 상담은 평일 오전 9시부터 오후 6시까지만 운영해서 밤에는 통화가 어려워요. 대신 '설정 > 고객센터 > 1:1 문의'에 남기시면 24시간 접수됩니다."""

RETRY_CODES = {429, 500, 503}
SERVER_TIMEOUT = 40

app = FastAPI()

from agent_api import router as agent_router
app.include_router(agent_router)


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    answer: str
    sources: list[str] = []   # 참고한 매뉴얼 문항 제목 (앱에서 근거 표시용)


# ── ① ② 검색 ──────────────────────────────────────────
async def search_manual(question: str) -> list[dict]:
    res = await client.aio.models.embed_content(
        model=EMBED_MODEL,
        contents=question,
        config=types.EmbedContentConfig(task_type="RETRIEVAL_QUERY"),
    )
    found = collection.query(query_embeddings=[res.embeddings[0].values], n_results=TOP_K)

    results = [
        {"title": meta["title"], "text": doc, "distance": dist}
        for doc, meta, dist in zip(
            found["documents"][0], found["metadatas"][0], found["distances"][0]
        )
    ]
    # 서버 터미널에서 무엇을 찾았는지 확인하기 위한 로그
    print(f"[검색] {question}")
    for r in results:
        print(f"   {r['distance']:.3f} | {r['title']}")
    return results


# ── ③ 근거를 넣어 답변 생성 ─────────────────────────────
def build_prompt(question: str, docs: list[dict]) -> str:
    context = "\n\n".join(d["text"] for d in docs)
    return f"[매뉴얼 근거]\n{context}\n\n[사장님 질문]\n{question}"


async def ask_gemini(contents: str) -> str:
    models = [m for m in (MODEL, FALLBACK_MODEL) if m]
    last_error = None
    for model in models:
        for attempt in range(3):
            try:
                res = await client.aio.models.generate_content(
                    model=model,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT,
                        temperature=0.2,
                    ),
                )
                return res.text or NO_ANSWER
            except errors.APIError as e:
                last_error = e
                if e.code not in RETRY_CODES:
                    raise
                await asyncio.sleep(2 ** attempt)
    raise last_error


async def answer_with_rag(question: str) -> ChatResponse:
    docs = await search_manual(question)

    # 1차 방어: 가장 가까운 문항도 너무 멀면 AI를 부르지 않는다
    if not docs or docs[0]["distance"] > MAX_DISTANCE:
        print("   → 근거 없음 (기준선 초과)")
        return ChatResponse(answer=NO_ANSWER, sources=[])

    # 2차 방어: 프롬프트의 규칙에 따라 AI가 근거로 답할 수 있는지 판단
    answer = await ask_gemini(build_prompt(question, docs))
    sources = [] if is_no_answer(answer) else [d["title"] for d in docs]
    return ChatResponse(answer=answer, sources=sources)


# ── ④ API ─────────────────────────────────────────────
@app.post("/chat")
async def chat(req: ChatRequest) -> ChatResponse:
    try:
        return await asyncio.wait_for(answer_with_rag(req.message), timeout=SERVER_TIMEOUT)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="AI 응답이 지연되고 있어요. 잠시 후 다시 시도해 주세요.")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"답변 생성 실패: {e}")