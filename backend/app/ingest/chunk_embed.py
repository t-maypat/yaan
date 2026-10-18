"""S3 Chunk + embed. Built in Phase 4.

A message or thread = 1 chunk; meetings split by speaker turn (<= ~800 chars); docs
by heading/paragraph. Embeddings on Chunk nodes only (384-d local model); full-text
and vector indexes inside FalkorDB. Spec: docs/CONTEXT.md §9.3 (S3), §3.6 gotchas 7-8.

A chunk's `text` is always a verbatim span of one field of its artifact (`field`,
`start_char`, `end_char`), so a quote taken from a chunk is an exact substring of the
artifact. The embedding sees a short context header first (kind, title or channel,
product); the stored text doesn't. Decisions: D030 (chunking), D031 (embeddings).
"""

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from app.config import get_settings
from app.ingest.normalize import Artifact

MAX_CHARS = 800  # CONTEXT §9.3: meeting chunks of at most ~800 chars; used for every kind
TRANSCRIPT_MARK = "\nTranscript\n"
EMBED_BATCH = 64


@dataclass(frozen=True)
class Chunk:
    id: str
    artifact_id: str
    idx: int
    field: str  # which artifact field the span is in: text, title, feedback, reviews[i].comment
    start_char: int
    end_char: int
    text: str
    kind: str
    product_id: str | None
    ts_ms: int | None
    context: str  # header for the embedding only


def spans(text: str, start: int = 0, max_chars: int = MAX_CHARS) -> Iterator[tuple[int, int]]:
    """Cut text[start:] into pieces of at most max_chars, at a sentence end or a space."""
    end = len(text)
    while start < end:
        while start < end and text[start].isspace():
            start += 1
        if start >= end:
            return
        stop = min(start + max_chars, end)
        if stop < end:
            cut = max(text.rfind(". ", start, stop), text.rfind("\n", start, stop))
            if cut <= start:
                cut = text.rfind(" ", start, stop)
            if cut > start:
                stop = cut + 1
        piece_end = stop
        while piece_end > start and text[piece_end - 1].isspace():
            piece_end -= 1
        yield start, piece_end
        start = stop


def line_groups(text: str, start: int, max_chars: int = MAX_CHARS) -> Iterator[tuple[int, int]]:
    """Group consecutive lines (speaker turns, doc sections) up to max_chars per chunk.

    A line longer than max_chars is cut by `spans` on its own.
    """
    group_start = group_end = None
    pos = start
    for line in text[start:].split("\n"):
        line_start, line_end = pos, pos + len(line)
        pos = line_end + 1
        if not line.strip():
            continue
        if line_end - line_start > max_chars:
            if group_start is not None:
                yield group_start, group_end
                group_start = None
            yield from spans(text[:line_end], line_start, max_chars)
            continue
        if group_start is not None and line_end - group_start > max_chars:
            yield group_start, group_end
            group_start = None
        if group_start is None:
            group_start = line_start
        group_end = line_end
    if group_start is not None:
        yield group_start, group_end


def field_spans(a: Artifact) -> Iterator[tuple[str, str, Iterator[tuple[int, int]]]]:
    """(field name, field text, spans) for every searchable field of an artifact (D030)."""
    if a.kind == "message":
        if not a.system:  # bot notices (joined / created) only add noise to search
            yield "text", a.text, spans(a.text)
    elif a.kind == "meeting":
        body = a.text.find(TRANSCRIPT_MARK)
        start = body + len(TRANSCRIPT_MARK) if body >= 0 else 0
        yield "text", a.text, line_groups(a.text, start)
    elif a.kind == "doc":
        yield "text", a.text, line_groups(a.text, 0)
        if a.meta.get("feedback"):
            yield "feedback", a.meta["feedback"], line_groups(a.meta["feedback"], 0)
    elif a.kind == "pr":
        yield "title", a.title or "", spans(a.title or "")
        yield "text", a.text, spans(a.text)
        for i, review in enumerate(a.meta["reviews"]):
            if review["comment"]:
                yield f"reviews[{i}].comment", review["comment"], spans(review["comment"])
    else:  # meeting_chat, link
        yield "text", a.text, spans(a.text)


def context_header(a: Artifact) -> str:
    product = "" if a.kind == "link" else f" · {a.product_hint}"
    if a.kind == "message":
        return f"Slack #{a.meta['channel_name']}{product}"
    if a.kind == "pr":
        return f"PR #{a.meta['number']} {a.meta['repo']}: {a.title}{product}"
    if a.kind == "doc":
        return f"{a.meta['doc_type']} {a.title}{product}"
    if a.kind == "meeting":
        return f"Meeting {a.title} ({a.meta['document_type']}){product}"
    return f"{a.kind.replace('_', ' ')}{product}"


def chunk_artifact(a: Artifact) -> list[Chunk]:
    header = context_header(a)
    chunks = []
    for field, text, pieces in field_spans(a):
        for start, end in pieces:
            chunks.append(
                Chunk(
                    id=f"chk:{a.id}:{len(chunks)}",
                    artifact_id=a.id,
                    idx=len(chunks),
                    field=field,
                    start_char=start,
                    end_char=end,
                    text=text[start:end],
                    kind=a.kind,
                    product_id=None if a.kind == "link" else a.product_hint,
                    ts_ms=a.ts_ms,
                    context=header,
                )
            )
    return chunks


def chunk_products(products: dict[str, list[Artifact]]) -> list[Chunk]:
    """Chunks of every artifact; an artifact shared by products (a link) is chunked once."""
    seen: set[str] = set()
    chunks = []
    for artifacts in products.values():
        for a in artifacts:
            if a.id not in seen:
                seen.add(a.id)
                chunks += chunk_artifact(a)
    return chunks


# --- embeddings (D031) ----------------------------------------------------------------------
@lru_cache
def _model():
    from fastembed import TextEmbedding  # imported late: loading ONNX takes seconds

    s = get_settings()
    return TextEmbedding(s.lore_embed_model, cache_dir=str(s.models_dir))


@lru_cache
def _cache():
    from diskcache import Cache

    return Cache(str(get_settings().cache_dir / "embeddings"))


def embed_key(kind: str, text: str) -> str:
    model = get_settings().lore_embed_model
    return hashlib.sha256(f"{model}\n{kind}\n{text}".encode()).hexdigest()


def embed_passages(texts: list[str]) -> list[list[float]]:
    """Passage vectors (384-d, unit length), from the disk cache when seen before."""
    return _embed(texts, "passage")


def embed_query(text: str) -> list[float]:
    """A search query's vector; bge models add their query instruction here."""
    return _embed([text], "query")[0]


def _embed(texts: list[str], kind: str) -> list[list[float]]:
    cache = _cache()
    keys = [embed_key(kind, t) for t in texts]
    vectors: list[Any] = [cache.get(k) for k in keys]
    # Similar lengths batch together: each batch is padded to its longest text, so sorting
    # cut the CPU time a lot (mixed 20-800 char chunks embedded at ~6/s unsorted).
    todo = sorted((i for i, v in enumerate(vectors) if v is None), key=lambda i: len(texts[i]))
    if todo:
        model = _model()
        fresh_texts = [texts[i] for i in todo]
        if kind == "query":
            fresh = model.query_embed(fresh_texts)
        else:
            fresh = model.passage_embed(fresh_texts, batch_size=EMBED_BATCH)
        for i, vec in zip(todo, fresh, strict=True):
            vectors[i] = [float(x) for x in vec]
            cache.set(keys[i], vectors[i])
    dim = get_settings().lore_embed_dim
    if any(len(v) != dim for v in vectors):
        raise ValueError(f"embedding size differs from LORE_EMBED_DIM={dim}")
    return vectors


CHUNK_BATCH_ROWS = 100  # each row carries 384 floats; keeps a write statement small


def load(chunks: list[Chunk], graph: str | None = None) -> dict[str, Any]:
    """Embed and upsert chunks (their artifacts must be loaded already: stage structural)."""
    from app import db

    result = db.run(
        "upsert_chunks",
        {"rows": chunk_rows(chunks)},
        graph=graph,
        readonly=False,
        batch_rows=CHUNK_BATCH_ROWS,
    )
    linked = db.run("count_linked_chunks", {"ids": [c.id for c in chunks]}, graph=graph).rows
    if linked[0]["n"] != len(chunks):
        raise RuntimeError(
            f"{len(chunks) - linked[0]['n']} of {len(chunks)} chunks found no artifact: "
            "run the structural stage first"
        )
    return {"rows": len(chunks), **result.stats}


def chunk_rows(chunks: list[Chunk]) -> list[dict[str, Any]]:
    """Rows for upsert_chunks.cypher, each with its embedding."""
    vectors = embed_passages([f"{c.context}\n{c.text}" for c in chunks])
    return [
        {
            "id": c.id,
            "artifact_id": c.artifact_id,
            "idx": c.idx,
            "field": c.field,
            "start_char": c.start_char,
            "end_char": c.end_char,
            "text": c.text,
            "kind": c.kind,
            "product_id": c.product_id,
            "ts_ms": c.ts_ms,
            "embedding": vec,
        }
        for c, vec in zip(chunks, vectors, strict=True)
    ]
