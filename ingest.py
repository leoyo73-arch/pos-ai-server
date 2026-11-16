# manual.md를 문항 단위로 잘라 임베딩한 뒤 Chroma에 저장하는 스크립트
# 매뉴얼을 고칠 때마다 다시 실행하면 됩니다.
import os
import re

import chromadb
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()
EMBED_MODEL = os.getenv("GEMINI_EMBED_MODEL", "gemini-embedding-001")
client = genai.Client()


def split_manual(path: str) -> list[dict]:
    """manual.md를 '### Q.' 단위로 자른다. 위쪽 '## 카테고리'도 함께 기억한다."""
    chunks = []
    category = ""
    current = None

    for line in open(path, encoding="utf-8"):
        line = line.rstrip()
        if line.startswith("## "):
            category = line[3:].strip()
        elif line.startswith("### Q."):
            if current:
                chunks.append(current)
            title = line[len("### Q."):].strip()
            current = {"category": category, "title": title, "body": []}
        elif current and line and line != "---":
            current["body"].append(line)

    if current:
        chunks.append(current)

    for i, c in enumerate(chunks):
        c["id"] = f"faq-{i + 1:02d}"
        # 저장할 글: 카테고리와 질문을 앞에 붙여 검색이 잘 되게 한다
        c["text"] = f"[{c['category']}] Q. {c['title']}\n" + "\n".join(c["body"])
    return chunks


def embed_documents(texts: list[str]) -> list[list[float]]:
    res = client.models.embed_content(
        model=EMBED_MODEL,
        contents=texts,
        config=types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT"),
    )
    return [e.values for e in res.embeddings]


def main():
    chunks = split_manual("data/manual.md")
    print(f"조각 수: {len(chunks)}")

    embeddings = embed_documents([c["text"] for c in chunks])
    print(f"벡터 길이: {len(embeddings[0])}")

    db = chromadb.PersistentClient(path="chroma_db")
    # 다시 실행할 때 옛 데이터가 남지 않도록 지우고 새로 만든다
    try:
        db.delete_collection("manual")
    except Exception:
        pass
    collection = db.create_collection("manual", metadata={"hnsw:space": "cosine"})

    collection.add(
        ids=[c["id"] for c in chunks],
        documents=[c["text"] for c in chunks],
        embeddings=embeddings,
        metadatas=[{"category": c["category"], "title": c["title"]} for c in chunks],
    )
    print("저장 완료: chroma_db 폴더")


if __name__ == "__main__":
    main()