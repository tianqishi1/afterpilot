"""Versioned policy KB + BM25 retrieval baseline (char-bigrams for CJK).
pgvector upgrade path documented in ADR-003; retrieval contract stays the same."""
import math
import re
from collections import Counter

from sqlalchemy import select, update

from .db import session_scope
from .models import KbDoc


def tokenize(text: str) -> list[str]:
    text = text.lower()
    words = re.findall(r"[a-z0-9]+", text)
    cjk = re.findall(r"[\u4e00-\u9fff]", text)
    bigrams = ["".join(p) for p in zip(cjk, cjk[1:])]
    return words + cjk + bigrams


def upsert(slug: str, title: str, body: str) -> int:
    with session_scope() as s:
        cur = s.execute(select(KbDoc).where(KbDoc.slug == slug, KbDoc.active == True)).scalar_one_or_none()  # noqa: E712
        ver = (cur.version + 1) if cur else 1
        if cur:
            s.execute(update(KbDoc).where(KbDoc.id == cur.id).values(active=False))
        s.add(KbDoc(slug=slug, version=ver, title=title, body=body, active=True))
    rebuild()
    return ver


def rebuild() -> None:
    global _INDEX
    with session_scope() as s:
        docs = s.execute(select(KbDoc).where(KbDoc.active == True)).scalars().all()  # noqa: E712
    chunks = []
    for d in docs:
        for para in [p.strip() for p in d.body.split("\n") if p.strip()]:
            chunks.append({"slug": d.slug, "version": d.version, "title": d.title,
                           "text": para, "tf": Counter(tokenize(para))})
    df: Counter = Counter()
    for c in chunks:
        df.update(set(c["tf"]))
    _INDEX = {"chunks": chunks, "df": df, "n": len(chunks),
              "avgdl": sum(sum(c["tf"].values()) for c in chunks) / max(1, len(chunks))}


_INDEX: dict = {"chunks": [], "df": Counter(), "n": 0, "avgdl": 1}


def _index():
    return _INDEX


def search(query: str, k: int = 3) -> list[dict]:
    idx = _INDEX
    if not idx["chunks"]:
        rebuild()
        idx = _INDEX
    q = Counter(tokenize(query))
    scores = []
    for c in idx["chunks"]:
        dl = sum(c["tf"].values())
        score = 0.0
        for term, qf in q.items():
            tf = c["tf"].get(term, 0)
            if not tf:
                continue
            idf = math.log(1 + (idx["n"] - idx["df"][term] + 0.5) / (idx["df"][term] + 0.5))
            score += idf * (tf * 2.2) / (tf + 1.2 * (0.25 + 0.75 * dl / idx["avgdl"]))
        if score > 0:
            scores.append((score, c))
    scores.sort(key=lambda x: -x[0])
    return [{"slug": c["slug"], "version": c["version"], "title": c["title"],
             "snippet": c["text"][:400], "score": round(s, 3)} for s, c in scores[:k]]


def seed(path) -> None:
    import yaml
    data = yaml.safe_load(open(path, encoding="utf-8"))
    for doc in data["docs"]:
        upsert(doc["slug"], doc["title"], doc["body"])