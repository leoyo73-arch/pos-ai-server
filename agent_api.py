# 비서(에이전트)와 승인 처리를 앱에 열어 주는 API
# main.py에 아래 두 줄만 추가하면 연결됩니다.
#     from agent_api import router as agent_router
#     app.include_router(agent_router)
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agent_graph import run
from tools import approve_action, list_waiting_actions

router = APIRouter()


class AgentRequest(BaseModel):
    message: str
    session_id: str = "app"     # 앱 실행마다 다른 값 → 대화 기록을 따로 기억


class PendingAction(BaseModel):
    id: int
    kind: str                   # CANCEL(결제 취소) / NOTICE(손님 안내)
    transaction_id: str
    paid_at: str
    amount: int
    items: str
    detail: str | None = None   # 취소 사유 또는 안내 문구


class AgentResponse(BaseModel):
    answer: str
    pending_actions: list[PendingAction] = []   # 앱이 확인 창을 띄울 승인 대기 목록


class DecisionRequest(BaseModel):
    approve: bool


class DecisionResponse(BaseModel):
    ok: bool
    message: str


def _pending() -> list[PendingAction]:
    return [PendingAction(**a) for a in list_waiting_actions()]


# LangGraph 실행은 동기 함수라 'async def'가 아닌 'def'로 둔다.
# 그러면 FastAPI가 별도 스레드에서 실행해 서버 전체가 멈추지 않는다.
@router.post("/agent")
def agent(req: AgentRequest) -> AgentResponse:
    try:
        answer = run(req.message, thread_id=req.session_id)
    except Exception as e:
        text = str(e)
        if "RESOURCE_EXHAUSTED" in text or "per day" in text:
            raise HTTPException(status_code=503, detail="오늘 AI 사용량을 모두 썼어요. 잠시 후 다시 시도해 주세요.")
        raise HTTPException(status_code=502, detail=f"비서 응답 실패: {text[:200]}")
    return AgentResponse(answer=answer, pending_actions=_pending())


@router.get("/actions")
def actions() -> list[PendingAction]:
    return _pending()


# 승인·거절은 사람이 앱 버튼으로만 한다. 이 API는 AI 도구로 등록되어 있지 않다.
@router.post("/actions/{action_id}")
def decide(action_id: int, req: DecisionRequest) -> DecisionResponse:
    result = approve_action(action_id, req.approve)
    if "오류" in result:
        return DecisionResponse(ok=False, message=result["오류"])
    if not req.approve:
        return DecisionResponse(ok=True, message="요청을 거절했어요. 실행되지 않습니다.")
    kind = "결제 취소" if result["종류"] == "CANCEL" else "손님 안내 발송"
    return DecisionResponse(ok=True, message=f"{result['거래번호']} {kind} 완료")
