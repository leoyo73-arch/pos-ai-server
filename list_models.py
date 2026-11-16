from dotenv import load_dotenv
from google import genai

load_dotenv()
client = genai.Client()  # 변수에 담아 두어야 연결이 유지돼요

for m in client.models.list():
    if "flash" in m.name:
        print(m.name)