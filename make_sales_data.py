# 가상 카페 매장의 최근 30일 매출 데이터를 만들어 data/sales.db(SQLite)에 저장하는 스크립트
# - 모든 데이터는 가짜입니다. (전화번호도 010-0000-xxxx 형식의 가짜 번호)
# - 환불 규칙은 data/manual.md의 '결제를 취소하면 손님에게 언제 환불되나요?'와 맞춰 두었습니다.
# - 다시 실행하면 기존 DB를 지우고 새로 만듭니다.
import os
import random
import sqlite3
from datetime import datetime, timedelta

DB_PATH = "data/sales.db"
DAYS = 30      # 오늘 포함 최근 며칠치 거래를 만들지
SEED = 42      # 같은 날 실행하면 항상 같은 데이터가 나오게 고정
CANCEL_RATE = 0.07

MENU = [
    ("아메리카노", 4500), ("카페라떼", 5000), ("바닐라라떼", 5500), ("콜드브루", 5000),
    ("녹차라떼", 5500), ("크루아상", 3800), ("치즈케이크", 6500), ("샌드위치", 7000),
]
METHODS = ["NFC", "BLUETOOTH", "QR"]          # NFC 카드 / 블루투스 단말기 카드 / QR 간편결제
METHOD_WEIGHTS = [0.55, 0.25, 0.20]
HOURS = list(range(8, 21))                    # 영업시간 08~21시
HOUR_WEIGHTS = [3, 6, 5, 4, 9, 10, 6, 4, 4, 5, 6, 4, 2]  # 출근·점심 시간에 몰리게
STAFF = ["대표", "직원1", "직원2"]

SCHEMA = """
CREATE TABLE transactions (
    id             TEXT PRIMARY KEY,   -- 거래번호 예: T20261114-0007
    created_at     TEXT NOT NULL,      -- 결제 시각 (YYYY-MM-DD HH:MM:SS)
    amount         INTEGER NOT NULL,   -- 결제 금액(원)
    method         TEXT NOT NULL,      -- NFC / BLUETOOTH / QR
    items          TEXT NOT NULL,      -- 주문 내역 요약
    staff          TEXT NOT NULL,      -- 결제한 사람
    customer_phone TEXT,               -- 모바일 영수증을 받은 손님 번호(가짜), 없으면 NULL
    status         TEXT NOT NULL,      -- APPROVED(승인) / CANCELED(취소)
    canceled_at    TEXT,               -- 취소 시각
    refund_status  TEXT,               -- 취소 건만: DONE(환불 완료) / PENDING(환불 대기)
    refund_due     TEXT                -- 환불 완료(예정)일
);
CREATE INDEX idx_created ON transactions(created_at);
CREATE INDEX idx_status ON transactions(status, refund_status);
"""

FMT = "%Y-%m-%d %H:%M:%S"


def add_business_days(d: datetime, n: int) -> datetime:
    """주말을 건너뛰고 영업일 n일 뒤 (공휴일은 단순화를 위해 고려하지 않음)"""
    while n > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def make_order() -> tuple[str, int]:
    picks = random.choices(MENU, k=random.choices([1, 2, 3, 4], weights=[45, 35, 15, 5])[0])
    counts: dict[str, int] = {}
    for name, _ in picks:
        counts[name] = counts.get(name, 0) + 1
    items = ", ".join(f"{n} {c}" for n, c in counts.items())
    amount = sum(price for _, price in picks)
    return items, amount


def apply_refund(tx: dict, canceled: datetime, now: datetime) -> None:
    """매뉴얼의 환불 규칙: 당일 취소·QR은 즉시 환불, 그 외 카드는 영업일 3~5일 뒤"""
    paid = datetime.strptime(tx["created_at"], FMT)
    tx["status"] = "CANCELED"
    tx["canceled_at"] = canceled.strftime(FMT)
    if tx["method"] == "QR" or canceled.date() == paid.date():
        due = canceled
    else:
        due = add_business_days(canceled, random.randint(3, 5))
    tx["refund_due"] = due.strftime("%Y-%m-%d")
    tx["refund_status"] = "DONE" if due <= now else "PENDING"


def generate(now: datetime) -> list[dict]:
    random.seed(f"{SEED}-{now.date()}")
    txs = []
    for back in range(DAYS - 1, -1, -1):
        day = (now - timedelta(days=back)).date()
        n = random.randint(14, 24) if day.weekday() >= 5 else random.randint(10, 18)
        times = sorted(
            datetime(day.year, day.month, day.day, h, random.randint(0, 59), random.randint(0, 59))
            for h in random.choices(HOURS, weights=HOUR_WEIGHTS, k=n)
        )
        times = [t for t in times if t <= now]  # 오늘은 지금 시각까지만
        for i, t in enumerate(times, 1):
            items, amount = make_order()
            txs.append({
                "id": f"T{t:%Y%m%d}-{i:04d}",
                "created_at": t.strftime(FMT),
                "amount": amount,
                "method": random.choices(METHODS, weights=METHOD_WEIGHTS)[0],
                "items": items,
                "staff": random.choice(STAFF),
                "customer_phone": f"010-0000-{random.randint(0, 9999):04d}" if random.random() < 0.5 else None,
                "status": "APPROVED",
                "canceled_at": None,
                "refund_status": None,
                "refund_due": None,
            })

    # 일반 취소: 대부분 당일, 일부는 며칠 뒤
    for tx in txs:
        if random.random() >= CANCEL_RATE:
            continue
        paid = datetime.strptime(tx["created_at"], FMT)
        if random.random() < 0.6:
            canceled = paid + timedelta(minutes=random.randint(1, 90))
        else:
            canceled = paid + timedelta(days=random.randint(1, 7), minutes=random.randint(0, 600))
        if canceled <= now:
            apply_refund(tx, canceled, now)

    # 데모 시나리오 보장: '어제 취소했는데 아직 환불 안 된 카드 결제'를 3건 만든다
    yesterday = (now - timedelta(days=1)).date()
    candidates = [
        tx for tx in txs
        if tx["status"] == "APPROVED" and tx["method"] != "QR"
        and 2 <= (yesterday - datetime.strptime(tx["created_at"], FMT).date()).days <= 10
    ]
    for tx in random.sample(candidates, 3):
        canceled = datetime(yesterday.year, yesterday.month, yesterday.day,
                            random.randint(10, 19), random.randint(0, 59))
        apply_refund(tx, canceled, now)
        tx["refund_status"] = "PENDING"   # 영업일 3~5일 뒤라 아직 대기 중
        tx["customer_phone"] = tx["customer_phone"] or f"010-0000-{random.randint(0, 9999):04d}"
    return txs


def main():
    now = datetime.now().replace(microsecond=0)
    txs = generate(now)

    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    con = sqlite3.connect(DB_PATH)
    con.executescript(SCHEMA)
    con.executemany(
        "INSERT INTO transactions VALUES (:id, :created_at, :amount, :method, :items, :staff, "
        ":customer_phone, :status, :canceled_at, :refund_status, :refund_due)",
        txs,
    )
    con.commit()

    # 만든 데이터 요약
    q = lambda sql: con.execute(sql).fetchone()
    total, sales = q("SELECT COUNT(*), SUM(amount) FROM transactions WHERE status='APPROVED'")
    canceled = q("SELECT COUNT(*) FROM transactions WHERE status='CANCELED'")[0]
    pending = q("SELECT COUNT(*) FROM transactions WHERE refund_status='PENDING'")[0]
    y_pending = q(
        "SELECT COUNT(*) FROM transactions WHERE refund_status='PENDING' "
        "AND date(canceled_at) = date('now', 'localtime', '-1 day')"
    )[0]
    print(f"저장 완료: {DB_PATH}")
    print(f"- 기간: 최근 {DAYS}일 (기준 {now:%Y-%m-%d %H:%M})")
    print(f"- 승인 거래: {total}건, 매출 {sales:,}원")
    print(f"- 취소 거래: {canceled}건 (환불 대기 {pending}건, 그중 어제 취소 {y_pending}건)")
    con.close()


if __name__ == "__main__":
    main()
 