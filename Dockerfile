FROM python:3.13-slim

# 컨테이너 기본 시간대는 UTC라서 '오늘·어제' 계산이 9시간 어긋남 → 한국 시간으로
RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*
ENV TZ=Asia/Seoul \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 패키지 설치를 코드 복사보다 먼저 → 코드만 바꿨을 때는 이 단계를 다시 안 함(빌드가 빨라짐)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000
CMD ["python", "start.py"]
