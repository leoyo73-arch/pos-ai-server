import os
import sys

import chromadb
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()
EMBED_MODEL = os.getenv("GEMINI_EMBED_MODEL", "gemini-embedding-001")
client = genai.Client()
collection = chromadb.PersistentClient(path="chroma_db").get_collection("manual")

question = sys.argv[1] if len(sys.argv) > 1 else "손님이 환불해 달래요"

q_vec = client.models.embed_content(
    model=EMBED_MODEL,
    contents=question,
    config=types.EmbedContentConfig(task_type="RETRIEVAL_QUERY"),
).embeddings[0].values

res = collection.query(query_embeddings=[q_vec], n_results=3)

print(f"질문: {question}\n")
for title, dist in zip(
    [m["title"] for m in res["metadatas"][0]], res["distances"][0]
):
    print(f"  거리 {dist:.3f}  |  {title}")