# LangGraph로 만든 매출·취소 비서
#   계획(plan) → 도구 실행(tools) → 결과 확인(check) → 다시 계획 … → 보고(report)
# 설치: pip install langgraph langchain-google-genai
# 실행 전: python make_sales_data.py
import functools
import json
import os
import time
from datetime import date
from typing import Literal

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, SystemMessage, ToolMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from tools import AGENT_TOOLS, approve_action, list_waiting_actions

load_dotenv()
MODEL = os.getenv("GEMINI_AGENT_MODEL") or os.getenv("GEMINI_MODEL", "gemini-flash-latest")
MAX_STEPS = 5                                   # 한 질문에서 '계획'을 최대 몇 번 할지 (무한 반복 방지)
RISKY_TOOLS = {"send_notice", "cancel_payment"}  # 승인이 필요한 도구


# ── 도구 준비 ─────────────────────────────────────────
def as_text(func):
    """도구 결과(dict)를 한글이 깨지지 않는 JSON 문자열로 바꿔 AI에게 전달"""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        return json.dumps(func(*args, **kwargs), ensure_ascii=False)
    return wrapper


TOOLS = [as_text(f) for f in AGENT_TOOLS]


def build_prompt() -> str:
    today = date.today()
    weekday = "월화수목금토일"[today.weekday()]
    return f"""너는 카페 사장님을 돕는 POS 매출·취소 비서야.
오늘은 {today} ({weekday}요일)이야. '어제', '이번 주' 같은 말은 이 날짜로 계산해.

기간 해석:
- '이번 주', '요즘', '최근'처럼 기간이 모호하면 오늘을 포함한 최근 7일로 조회하고, 어떤 기간인지 답변에 밝혀.
- 오늘은 아직 영업 중이라 하루치가 다 쌓이지 않았어. 오늘이 포함된 기간을 비교할 때는 그 점을 함께 말해.

규칙:
- 매출, 거래, 환불에 관한 사실은 반드시 도구로 조회한 결과로만 답해. 추측하지 마.
- 날짜는 YYYY-MM-DD 형식으로 도구에 넘겨.
- 한 번의 조회로 답할 수 있으면 도구를 여러 번 부르지 마.
- 결제 취소와 손님 안내는 도구가 '승인 대기'로만 등록해. 등록했다면 '사장님 승인이 필요하다'고 분명히 말하고, 이미 처리했다고 말하지 마.
- 대상이 여러 건이면 거래번호, 금액, 상태를 번호 목록으로 보여 줘.
- 한국어 존댓말로 짧게 답하고, 굵게(**)와 제목(#) 같은 마크다운은 쓰지 마."""


llm = ChatGoogleGenerativeAI(
    model=MODEL,
    temperature=0,
    google_api_key=os.getenv("GEMINI_API_KEY"),
    max_retries=2,
).bind_tools(TOOLS)   # ← AI에 도구 등록


# ── 상태: 그래프의 노드들이 함께 읽고 쓰는 데이터 ─────────
class AgentState(MessagesState):   # messages(대화 기록)는 MessagesState가 기본 제공
    steps: int                # 이번 질문에서 계획한 횟수
    approval_needed: bool     # 승인 대기로 등록된 작업이 있는지
    errors: int               # 도구가 오류를 돌려준 횟수
    final: str                # 사장님께 보여 줄 최종 답변


def text_of(msg) -> str:
    """AI 메시지에서 글자만 꺼내기 (Gemini는 내용을 여러 조각으로 줄 때가 있음)"""
    content = msg.content
    if isinstance(content, str):
        return content
    return "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content)


# ── 노드 ①: 계획 — AI가 다음에 할 일을 정한다 ──────────────
def plan(state: AgentState) -> dict:
    response = llm.invoke([SystemMessage(build_prompt())] + state["messages"])
    return {"messages": [response], "steps": state["steps"] + 1}


# 계획 다음 갈림길: 도구를 쓰겠다면 '도구 실행'으로, 아니면 '보고'로
def after_plan(state: AgentState) -> Literal["tools", "report"]:
    last = state["messages"][-1]
    if getattr(last, "tool_calls", None) and state["steps"] < MAX_STEPS:
        return "tools"
    return "report"


# ── 노드 ②: 도구 실행 — LangGraph가 제공하는 ToolNode 사용 ────
tool_node = ToolNode(TOOLS)


# ── 노드 ③: 결과 확인 — 방금 실행한 도구 결과를 코드로 점검 ─────
def check(state: AgentState) -> dict:
    results = []
    for msg in reversed(state["messages"]):
        if not isinstance(msg, ToolMessage):
            break
        results.append(msg)

    approval = state["approval_needed"] or any(
        m.name in RISKY_TOOLS and "승인 대기" in str(m.content) for m in results
    )
    errors = state["errors"] + sum("오류" in str(m.content) for m in results)
    if errors > state["errors"]:
        print(f"      (도구 오류 {errors - state['errors']}건 — AI가 다시 계획합니다)")
    return {"approval_needed": approval, "errors": errors}


# ── 노드 ④: 보고 — 최종 답변을 만들고 안전 문구를 보장 ──────────
def report(state: AgentState) -> dict:
    last = state["messages"][-1]

    # 단계 한도를 넘겨 멈춘 경우: 답 없이 남은 도구 요청을 지워 다음 대화가 꼬이지 않게 한다
    if getattr(last, "tool_calls", None):
        text = "요청을 처리하는 단계가 너무 많아져 중간에 멈췄어요. 조금 더 구체적으로 말씀해 주세요."
        return {"messages": [RemoveMessage(id=last.id), AIMessage(content=text)], "final": text}

    text = text_of(last)
    # AI가 승인 안내를 빠뜨려도, 승인 대기 작업이 있으면 코드가 반드시 덧붙인다
    if state["approval_needed"] and "승인" not in text:
        text += "\n\n※ 요청은 아직 처리되지 않았고, 사장님 승인 후에 실행됩니다. (/승인)"
    return {"final": text}


# ── 그래프 조립 ──────────────────────────────────────────
builder = StateGraph(AgentState)
builder.add_node("plan", plan)
builder.add_node("tools", tool_node)
builder.add_node("check", check)
builder.add_node("report", report)

builder.add_edge(START, "plan")
builder.add_conditional_edges("plan", after_plan, {"tools": "tools", "report": "report"})
builder.add_edge("tools", "check")
builder.add_edge("check", "plan")
builder.add_edge("report", END)

graph = builder.compile(checkpointer=MemorySaver())   # 대화 기록을 기억 ("그 손님들" 같은 지시용)


# ── 터미널 테스트 ────────────────────────────────────────
NODE_NAMES = {"plan": "계획", "tools": "도구 실행", "check": "결과 확인", "report": "보고"}
CONFIG = {"configurable": {"thread_id": "demo"}}


def run(question: str) -> str:
    final = ""
    initial = {"messages": [HumanMessage(question)], "steps": 0,
               "approval_needed": False, "errors": 0, "final": ""}
    for update in graph.stream(initial, CONFIG, stream_mode="updates"):
        for node, value in update.items():
            print(f"  → [{NODE_NAMES.get(node, node)}]", flush=True)
            if node == "report" and value:
                final = value["final"]
    return final


if __name__ == "__main__":
    print(f"POS 비서 (LangGraph, {MODEL})  |  종료: q  |  승인: /승인  |  구조도: /그래프")
    while True:
        q = input("\n사장님> ").strip()
        if not q:
            continue
        if q.lower() in ("q", "quit", "exit"):
            break
        if q == "/그래프":
            print(graph.get_graph().draw_mermaid())
            continue
        if q == "/승인":
            waiting = list_waiting_actions()
            if not waiting:
                print("  승인 대기 중인 요청이 없습니다.")
            for a in waiting:
                kind = "결제 취소" if a["kind"] == "CANCEL" else "손님 안내"
                print(f"\n  [{a['id']}] {kind}")
                print(f"      거래: {a['transaction_id']} | {a['paid_at']} | {a['amount']:,}원 | {a['items']}")
                print(f"      {'사유' if a['kind'] == 'CANCEL' else '문구'}: {a['detail']}")
                ans = input("      승인할까요? (y/n) ")
                print("      →", approve_action(a["id"], ans.strip().lower() == "y"))
            continue

        start = time.perf_counter()
        try:
            answer = run(q)
        except Exception as e:
            if "per day" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                raise SystemExit("오늘 무료 사용량을 다 썼어요. .env의 모델을 바꾸거나 초기화 후 다시 실행하세요.")
            print(f"  오류: {type(e).__name__}: {str(e)[:200]}")
            continue
        print(f"  (응답 {time.perf_counter() - start:.1f}초)")
        print("비서>", answer)
