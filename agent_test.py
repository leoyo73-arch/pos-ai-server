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

기간 해석:
- '이번 주', '요즘', '최근'처럼 기간이 모호하면 오늘을 포함한 최근 7일로 조회하고, 어떤 기간인지 답변에 밝혀.
- 오늘은 아직 영업 중이라 하루치가 다 쌓이지 않았어. 오늘이 포함된 기간을 다른 기간과 비교할 때는 그 점을 함께 말해.

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
    thinking_config=types.ThinkingConfig(thinking_level="low"),
)
chat = client.chats.create(model=MODEL, config=config)  # 대화 기록을 기억하는 채팅 세션


def ask(message: str):
    for attempt in range(3):
        try:
            return chat.send_message(message)
        except errors.APIError as e:
            if e.code == 429 and "per day" in str(e):
                raise SystemExit("오늘 무료 사용량을 다 썼어요. .env의 GEMINI_MODEL을 다른 모델로 바꾸거나, 초기화 후 다시 실행하세요.")
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
            print(f"\n  [{a['id']}] {kind}")
            print(f"      거래: {a['transaction_id']} | {a['paid_at']} | {a['amount']:,}원 | {a['items']}")
            print(f"      {'사유' if a['kind'] == 'CANCEL' else '문구'}: {a['detail']}")
            ans = input("      승인할까요? (y/n) ")
            print("      →", approve_action(a["id"], ans.strip().lower() == "y"))
        continue

    start = time.perf_counter()
    resp = ask(q)   # 도구 호출 기록은 tools.py의 logged()가 실행 중에 찍어 줌
    print(f"  (응답 {time.perf_counter() - start:.1f}초)")
    print("비서>", resp.text)
