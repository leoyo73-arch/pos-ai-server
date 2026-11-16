# 컨테이너가 시작될 때 실행: 필요한 데이터를 준비하고 서버를 켠다
import os
import subprocess
import sys

# 1) 매뉴얼 벡터 DB가 없을 때만 만든다 (임베딩 호출이 들어가서 매번 하면 낭비)
if not os.path.exists("chroma_db/chroma.sqlite3"):
    print("매뉴얼 벡터 DB를 만듭니다…", flush=True)
    subprocess.run([sys.executable, "ingest.py"], check=True)

# 2) 가상 매출 데이터는 매번 새로: '오늘·어제' 기준이 실행한 날짜에 맞춰진다
subprocess.run([sys.executable, "make_sales_data.py"], check=True)

# 3) 서버 실행 (run은 운영 모드: 자동 재시작 없음, 모든 주소에서 접속 허용)
os.execvp("fastapi", ["fastapi", "run", "main.py", "--port", "8000"])
