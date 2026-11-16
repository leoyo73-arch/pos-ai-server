# 도구를 등록한 AI(에이전트)와 터미널에서 대화해 보는 테스트 스크립트
# 실행 전: python make_sales_data.py  (매출 DB 만들기)
import os
import time
from datetime import date

from dotenv import load_dotenv
from google import genai
from google.genai import errors, types

from tools import AGENT_TOOLS, approve_action, list_waiting_actions

load_dotenv()
MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
client = genai.Client()

WEEKDAYS = "월화수목금토일"
today = date.today()
AGENT_PROMPT = f"""너는 카페 사장님을 돕는 POS 매출·취소 비서야.
오늘은 {today} ({WEEKDAYS[today.weekday()]}요일)이야. '어제', '이번 주' 같은 말은 이 날짜로 계산해.

규칙:
- 매출, 거래, 환불에 관한 사실은 반드시 도구로 조회한 결과로만 답해. 추측하지 마.
- 날짜는 YYYY-MM-DD 형식으로 도구에 넘겨.
- 결제 취소와 손님 안내는 도구가 '승인 대기'로만 등록해. 등록했다면 '사장님 승인이 필요하다'고 분명히 말하고, 이미 처리했다고 말하지 마.
- 대상이 여러 건이면 거래번호, 금액, 상태를 번호 목록으로 보여 줘.
- 한국어 존댓말로 짧게 답하고, 굵게(**)와 제목(#) 같은 마크다운은 쓰지 마."""

config = types.GenerateContentConfig(
    system_instruction=AGENT_PROMPT,
    tools=AGENT_TOOLS,   # ← 함수를 그대로 넘기면 SDK가 타입 힌트·독스트링을 읽어 AI에 등록
    temperature=0,
)
chat = client.chats.create(model=MODEL, config=config)  # 대화 기록을 기억하는 채팅 세션


def ask(message: str):
    for attempt in range(3):
        try:
            return chat.send_message(message)
        except errors.APIError as e:
            if e.code not in (429, 500, 503) or attempt == 2:
                raise
            print(f"  (Gemini 붐빔 {e.code}, 재시도…)")
            time.sleep(3 * (attempt + 1))


print("POS 비서 테스트  |  종료: q  |  승인 대기 처리: /승인")
while True:
    q = input("\n사장님> ").strip()
    if not q:
        continue
    if q.lower() in ("q", "quit", "exit"):
        break

    if q == "/승인":  # 앱의 승인 버튼 역할. AI가 아니라 사람이 결정한다.
        waiting = list_waiting_actions()
        if not waiting:
            print("  승인 대기 중인 요청이 없습니다.")
        for a in waiting:
            kind = "결제 취소" if a["kind"] == "CANCEL" else "손님 안내"
            ans = input(f"  [{a['id']}] {kind} | {a['transaction_id']} | {a['detail']}  승인할까요? (y/n) ")
            print("   →", approve_action(a["id"], ans.strip().lower() == "y"))
        continue

    resp = ask(q)
    # AI가 어떤 도구를 어떤 값으로 불렀는지 보여 주기 (디버깅·시연용)
    for content in resp.automatic_function_calling_history or []:
        for part in content.parts or []:
            if part.function_call:
                args = ", ".join(f"{k}={v!r}" for k, v in (part.function_call.args or {}).items())
                print(f"  [도구 호출] {part.function_call.name}({args})")
    print("비서>", resp.text)
