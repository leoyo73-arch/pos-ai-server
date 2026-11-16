import json
import os

import chromadb
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()
EMBED_MODEL = os.getenv("GEMINI_EMBED_MODEL", "gemini-embedding-001")
client = genai.Client()
collection = chromadb.PersistentClient(path="chroma_db").get_collection("manual")
tests = json.load(open("data/tests.json", encoding="utf-8"))


def top1(question: str):
    vec = client.models.embed_content(
        model=EMBED_MODEL,
        contents=question,
        config=types.EmbedContentConfig(task_type="RETRIEVAL_QUERY"),
    ).embeddings[0].values
    res = collection.query(query_embeddings=[vec], n_results=1)
    return res["metadatas"][0][0]["title"], res["distances"][0][0]


print("=== 매뉴얼에 있는 질문 (1등이 정답 문항이어야 함) ===")
hit, in_dists = 0, []
for t in tests["in_manual"]:
    title, dist = top1(t["question"])
    ok = title == t["source"]
    hit += ok
    in_dists.append(dist)
    print(f"{t['id']}  {'O' if ok else 'X'}  거리 {dist:.3f}  |  {title}")

print("\n=== 매뉴얼에 없는 질문 (거리가 커야 함) ===")
out_dists = []
for t in tests["out_of_manual"]:
    title, dist = top1(t["question"])
    out_dists.append(dist)
    print(f"{t['id']}     거리 {dist:.3f}  |  {title}")

print(f"\n검색 정답률: {hit} / {len(tests['in_manual'])}")
print(f"있는 질문 거리: 최소 {min(in_dists):.3f}, 최대 {max(in_dists):.3f}")
print(f"없는 질문 거리: 최소 {min(out_dists):.3f}, 최대 {max(out_dists):.3f}")