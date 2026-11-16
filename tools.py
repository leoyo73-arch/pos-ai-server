# 매출·취소 비서(에이전트)가 쓰는 도구 5개
# - AI에 등록하는 함수는 '타입 힌트'와 '독스트링'이 곧 AI가 읽는 사용 설명서입니다.
# - 돈이 움직이거나 손님에게 나가는 작업(cancel_payment, send_notice)은 바로 실행하지 않고
#   '승인 대기'로만 등록합니다. 실제 실행은 사장님 승인 후 approve_action()이 합니다.
#   approve_action은 AI 도구로 등록하지 않으므로 AI가 스스로 승인할 수 없습니다.
import sqlite3
from datetime import date, datetime, timedelta

from make_sales_data import FMT, add_business_days

DB_PATH = "data/sales.db"

METHOD_NAMES = {"NFC": "NFC 카드", "BLUETOOTH": "블루투스 단말기", "QR": "QR 간편결제"}
PENDING_SCHEMA = """
CREATE TABLE IF NOT EXISTS pending_actions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    kind           TEXT NOT NULL,        -- CANCEL / NOTICE
    transaction_id TEXT NOT NULL,
    detail         TEXT,                 -- 취소 사유 또는 안내 문구
    status         TEXT NOT NULL,        -- WAITING / DONE / REJECTED
    created_at     TEXT NOT NULL,
    done_at        TEXT
);
CREATE TABLE IF NOT EXISTS notices (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id TEXT NOT NULL,
    phone          TEXT NOT NULL,
    message        TEXT NOT NULL,
    sent_at        TEXT NOT NULL
);
"""


def _db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.executescript(PENDING_SCHEMA)
    return con


def _now() -> str:
    return datetime.now().strftime(FMT)


def _brief(row: sqlite3.Row) -> dict:
    """AI에게 돌려줄 거래 정보 (손님 번호는 뒷자리만)"""
    phone = row["customer_phone"]
    return {
        "거래번호": row["id"],
        "결제시각": row["created_at"],
        "금액": row["amount"],
        "결제수단": METHOD_NAMES.get(row["method"], row["method"]),
        "주문": row["items"],
        "상태": "취소" if row["status"] == "CANCELED" else "승인",
        "취소시각": row["canceled_at"],
        "환불상태": {"DONE": "환불 완료", "PENDING": "환불 대기"}.get(row["refund_status"]),
        "환불예정일": row["refund_due"],
        "손님번호": f"***-****-{phone[-4:]}" if phone else None,
    }


# ─────────────────────────────────────────────
# 조회 도구: 바로 실행 (사용자 확인 필요 없음)
# ─────────────────────────────────────────────
def get_sales_summary(start_date: str, end_date: str) -> dict:
    """기간별 매출 합계와 건수를 알려 준다. 같은 길이의 직전 기간과 비교한 증감률도 함께 준다.

    Args:
        start_date: 시작 날짜 (YYYY-MM-DD, 이 날 포함)
        end_date: 끝 날짜 (YYYY-MM-DD, 이 날 포함)
    """
    s, e = date.fromisoformat(start_date), date.fromisoformat(end_date)
    days = (e - s).days + 1
    prev_s, prev_e = s - timedelta(days=days), s - timedelta(days=1)

    sql = """SELECT COUNT(*) AS cnt, COALESCE(SUM(amount), 0) AS total
             FROM transactions WHERE status = 'APPROVED' AND date(created_at) BETWEEN ? AND ?"""
    with _db() as con:
        cur = con.execute(sql, (start_date, end_date)).fetchone()
        prev = con.execute(sql, (prev_s.isoformat(), prev_e.isoformat())).fetchone()
        by_method = con.execute(
            """SELECT method, COUNT(*) AS cnt, SUM(amount) AS total FROM transactions
               WHERE status = 'APPROVED' AND date(created_at) BETWEEN ? AND ? GROUP BY method""",
            (start_date, end_date),
        ).fetchall()
        canceled = con.execute(
            "SELECT COUNT(*) FROM transactions WHERE status = 'CANCELED' AND date(created_at) BETWEEN ? AND ?",
            (start_date, end_date),
        ).fetchone()[0]

    change = round((cur["total"] - prev["total"]) / prev["total"] * 100, 1) if prev["total"] else None
    return {
        "기간": f"{start_date} ~ {end_date}",
        "매출합계": cur["total"],
        "승인건수": cur["cnt"],
        "취소건수": canceled,
        "결제수단별": {METHOD_NAMES[r["method"]]: {"건수": r["cnt"], "금액": r["total"]} for r in by_method},
        "직전기간": f"{prev_s} ~ {prev_e}",
        "직전기간매출": prev["total"],
        "증감률(%)": change,
    }


def list_transactions(
    start_date: str,
    end_date: str,
    status: str = "",
    refund_status: str = "",
    method: str = "",
    canceled_on: str = "",
    limit: int = 20,
) -> dict:
    """조건에 맞는 거래 목록을 최신순으로 조회한다.

    Args:
        start_date: 결제일 시작 (YYYY-MM-DD, 포함)
        end_date: 결제일 끝 (YYYY-MM-DD, 포함)
        status: 'APPROVED'(승인) 또는 'CANCELED'(취소). 비우면 전체.
        refund_status: 'PENDING'(환불 대기) 또는 'DONE'(환불 완료). 비우면 전체.
        method: 'NFC', 'BLUETOOTH', 'QR' 중 하나. 비우면 전체.
        canceled_on: 이 날짜(YYYY-MM-DD)에 취소된 거래만. 비우면 조건 없음.
        limit: 최대 몇 건까지 (기본 20, 최대 50)
    """
    sql = "SELECT * FROM transactions WHERE date(created_at) BETWEEN ? AND ?"
    args: list = [start_date, end_date]
    for col, val in (("status", status), ("refund_status", refund_status), ("method", method)):
        if val:
            sql += f" AND {col} = ?"   # 컬럼 이름은 코드에 고정, 값만 ?로 받아 SQL 인젝션 방지
            args.append(val.upper())
    if canceled_on:
        sql += " AND date(canceled_at) = ?"
        args.append(canceled_on)
    sql += " ORDER BY created_at DESC LIMIT ?"
    args.append(max(1, min(int(limit), 50)))

    with _db() as con:
        rows = con.execute(sql, args).fetchall()
    return {"건수": len(rows), "거래": [_brief(r) for r in rows]}


def get_refund_status(transaction_id: str) -> dict:
    """거래번호로 취소·환불 상태를 확인한다. 환불 대기면 예정일도 알려 준다.

    Args:
        transaction_id: 거래번호 (예: T20261112-0004)
    """
    with _db() as con:
        row = con.execute("SELECT * FROM transactions WHERE id = ?", (transaction_id,)).fetchone()
    if not row:
        return {"오류": f"거래번호 {transaction_id}를 찾을 수 없습니다."}
    return _brief(row)


# ─────────────────────────────────────────────
# 위험 도구: 바로 실행하지 않고 '승인 대기'로만 등록
# ─────────────────────────────────────────────
def send_notice(transaction_id: str, message: str) -> dict:
    """손님에게 안내 문자를 보내도록 요청한다. 바로 발송되지 않고 사장님 승인을 기다린다.

    Args:
        transaction_id: 안내할 거래의 거래번호
        message: 손님에게 보낼 안내 문구 (공손하게, 2~3문장)
    """
    with _db() as con:
        row = con.execute("SELECT customer_phone FROM transactions WHERE id = ?", (transaction_id,)).fetchone()
        if not row:
            return {"오류": f"거래번호 {transaction_id}를 찾을 수 없습니다."}
        if not row["customer_phone"]:
            return {"오류": "이 거래에는 손님 휴대폰 번호가 없어 안내를 보낼 수 없습니다."}
        cur = con.execute(
            "INSERT INTO pending_actions (kind, transaction_id, detail, status, created_at) "
            "VALUES ('NOTICE', ?, ?, 'WAITING', ?)",
            (transaction_id, message, _now()),
        )
    return {"결과": "승인 대기", "요청번호": cur.lastrowid, "안내": "사장님이 승인하면 발송됩니다."}


def cancel_payment(transaction_id: str, reason: str) -> dict:
    """결제 취소를 요청한다. 바로 취소되지 않고 사장님 승인을 기다린다.

    Args:
        transaction_id: 취소할 거래번호
        reason: 취소 사유 (예: 손님 요청, 금액 오입력)
    """
    with _db() as con:
        row = con.execute("SELECT status, created_at FROM transactions WHERE id = ?", (transaction_id,)).fetchone()
        if not row:
            return {"오류": f"거래번호 {transaction_id}를 찾을 수 없습니다."}
        if row["status"] == "CANCELED":
            return {"오류": "이미 취소된 거래입니다."}
        paid = datetime.strptime(row["created_at"], FMT)
        if (datetime.now() - paid).days > 90:
            return {"오류": "결제일로부터 90일이 지나 앱에서 취소할 수 없습니다. 고객센터로 문의해 주세요."}
        cur = con.execute(
            "INSERT INTO pending_actions (kind, transaction_id, detail, status, created_at) "
            "VALUES ('CANCEL', ?, ?, 'WAITING', ?)",
            (transaction_id, reason, _now()),
        )
    return {"결과": "승인 대기", "요청번호": cur.lastrowid, "안내": "사장님이 승인하면 취소됩니다."}


# AI에 등록할 도구 목록
AGENT_TOOLS = [get_sales_summary, list_transactions, get_refund_status, send_notice, cancel_payment]


# ─────────────────────────────────────────────
# 승인 처리: 앱의 승인 버튼에서만 호출 (AI 도구 아님)
# ─────────────────────────────────────────────
def list_waiting_actions() -> list[dict]:
    with _db() as con:
        return [dict(r) for r in con.execute("SELECT * FROM pending_actions WHERE status = 'WAITING'")]


def approve_action(action_id: int, approve: bool = True) -> dict:
    with _db() as con:
        act = con.execute("SELECT * FROM pending_actions WHERE id = ?", (action_id,)).fetchone()
        if not act or act["status"] != "WAITING":
            return {"오류": "처리할 수 없는 요청입니다."}
        if not approve:
            con.execute("UPDATE pending_actions SET status='REJECTED', done_at=? WHERE id=?", (_now(), action_id))
            return {"결과": "거절됨"}

        tx = con.execute("SELECT * FROM transactions WHERE id = ?", (act["transaction_id"],)).fetchone()
        now = datetime.now()
        if act["kind"] == "CANCEL":
            paid = datetime.strptime(tx["created_at"], FMT)
            # 매뉴얼의 환불 규칙: 당일·QR은 즉시, 그 외 카드는 영업일 3일 뒤
            due = now if (tx["method"] == "QR" or paid.date() == now.date()) else add_business_days(now, 3)
            con.execute(
                "UPDATE transactions SET status='CANCELED', canceled_at=?, refund_status=?, refund_due=? WHERE id=?",
                (now.strftime(FMT), "DONE" if due <= now else "PENDING", due.strftime("%Y-%m-%d"), tx["id"]),
            )
        else:  # NOTICE: 데모에서는 실제 문자 대신 notices 테이블에 기록
            con.execute(
                "INSERT INTO notices (transaction_id, phone, message, sent_at) VALUES (?, ?, ?, ?)",
                (tx["id"], tx["customer_phone"], act["detail"], _now()),
            )
        con.execute("UPDATE pending_actions SET status='DONE', done_at=? WHERE id=?", (_now(), action_id))
    return {"결과": "처리 완료", "종류": act["kind"], "거래번호": act["transaction_id"]}
