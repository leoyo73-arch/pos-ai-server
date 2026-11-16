# tests.json 25문항으로 최종 답변 정답률을 측정하고 results.md로 저장하는 스크립트
import asyncio
import json
import statistics
import time

from google.genai import errors, types
from pydantic import BaseModel

from main import FALLBACK_MODEL, MODEL, answer_with_rag, client, is_no_answer

DELAY = 4             # 무료 등급 분당 한도를 넘지 않도록 질문 사이에 쉬는 시간(초)
QUESTION_TIMEOUT = 60  # 한 문항이 이보다 오래 걸리면 실패로 기록하고 다음 문항으로


class Verdict(BaseModel):
    correct: bool
    reason: str


JUDGE_PROMPT = """너는 고객 상담 답변을 채점하는 평가자야.
[정답 요점]의 핵심 정보가 [실제 답변]에 모두 들어 있고, 틀린 정보가 없으면 correct=true야.
표현이 달라도 뜻이 같으면 맞은 거야. 요점이 하나라도 빠지거나 틀린 숫자·메뉴가 있으면 false야.
reason에는 판단 이유를 한 문장으로 써."""


async def judge(question: str, expected: str, answer: str) -> Verdict | None:
    """AI로 채점. 모든 모델이 실패하면 None(직접 채점 필요)을 돌려준다."""
    contents = f"[질문]\n{question}\n\n[정답 요점]\n{expected}\n\n[실제 답변]\n{answer}"
    # 채점은 가벼운 모델로도 충분해서 예비 모델(flash-lite)을 먼저 시도
    models = [m for m in (FALLBACK_MODEL, MODEL) if m]

    for model in models:
        for attempt in range(3):
            try:
                res = await client.aio.models.generate_content(
                    model=model,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=JUDGE_PROMPT,
                        temperature=0,
                        response_mime_type="application/json",
                        response_schema=Verdict,
                    ),
                )
                return res.parsed
            except errors.APIError as e:
                if e.code not in (429, 500, 503):
                    raise
                print(f"    채점 재시도 ({model}, {e.code})", flush=True)
                await asyncio.sleep(5 * (attempt + 1))
            except Exception:
                break  # 시간 초과 등은 다음 모델로

    return None


def cell(text: str) -> str:
    """표 칸에 넣기 좋게 줄바꿈과 | 문자를 정리"""
    return str(text).replace("|", "/").replace("\n", " ").strip()


async def main():
    tests = json.load(open("data/tests.json", encoding="utf-8"))
    rows, times = [], []
    in_ok = out_ok = unjudged = 0

    for kind in ("in_manual", "out_of_manual"):
        for t in tests[kind]:
            print(f"{t['id']} 질문 중… {t['question']}", flush=True)

            # ① 답변 받기 (실패해도 전체가 멈추지 않게)
            start = time.perf_counter()
            try:
                res = await asyncio.wait_for(answer_with_rag(t["question"]), timeout=QUESTION_TIMEOUT)
            except Exception as e:
                elapsed = time.perf_counter() - start
                print(f"{t['id']} X  실패({type(e).__name__}) — 다음 문항으로 넘어갑니다")
                rows.append(
                    f"| {t['id']} | X | {elapsed:.1f} | {cell(t['question'])} "
                    f"| (응답 실패) | {cell(e)[:80]} |"
                )
                await asyncio.sleep(DELAY)
                continue
            elapsed = time.perf_counter() - start
            times.append(elapsed)

            # ② 채점
            if kind == "in_manual":
                v = await judge(t["question"], t["expected"], res.answer)
                if v is None:
                    ok, mark, reason = False, "?", "채점 실패 — 직접 확인 필요"
                    unjudged += 1
                else:
                    ok, reason = v.correct, v.reason
                    mark = "O" if ok else "X"
                    in_ok += ok
            else:
                ok = is_no_answer(res.answer)
                mark = "O" if ok else "X"
                reason = "고객센터 안내로 거절함" if ok else "매뉴얼에 없는데 답을 지어냄"
                out_ok += ok

            print(f"{t['id']} {mark} {elapsed:5.1f}초 | {t['question']}")
            rows.append(
                f"| {t['id']} | {mark} | {elapsed:.1f} | {cell(t['question'])} "
                f"| {cell(res.answer)} | {cell(reason)} |"
            )
            await asyncio.sleep(DELAY)

    # ③ 요약과 저장
    n_in, n_out = len(tests["in_manual"]), len(tests["out_of_manual"])
    time_line = (
        f"- 평균 응답 시간: {statistics.mean(times):.1f}초 (중앙값 {statistics.median(times):.1f}초)"
        if times else "- 평균 응답 시간: 측정된 응답 없음"
    )
    summary = (
        f"- 모델: {MODEL}\n"
        f"- 상담 정답률: {in_ok} / {n_in}"
        + (f" (직접 채점 필요 {unjudged}개)" if unjudged else "") + "\n"
        f"- 없는 내용 거절: {out_ok} / {n_out}\n"
        + time_line
    )
    print("\n" + summary)

    with open("results.md", "w", encoding="utf-8") as f:
        f.write(f"# 정답률 측정 결과\n\n{summary}\n\n")
        f.write("| ID | 판정 | 응답(초) | 질문 | 실제 답변 | 판정 이유 |\n")
        f.write("| --- | --- | --- | --- | --- | --- |\n")
        f.write("\n".join(rows) + "\n")
    print("results.md 저장 완료")


if __name__ == "__main__":
    asyncio.run(main())
