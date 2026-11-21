"""Small synthetic knowledge graph for offline tests; no personal reading."""
import json

from desktop.knowledge import fingerprint, PILOT_FILE
from desktop.store import private_write


def populate(store):
    article = store.add_item(kind="article", external_id="knowledge-article",
        title='A careful review <img src=x onerror="bad()">', connection_id="raindrop",
        body_html="<h2>Review</h2><p>Read this source carefully.</p><p>Check several outcomes, not just speed.</p>")
    video = store.add_item(kind="video", external_id="knowledge-video", title="Memory in practice",
        connection_id="youtube", summary_html="<p>Keep an index of useful lessons.</p>")
    store.add_item(kind="article", external_id="outside-pilot", title="An unrelated reading", body_html="<p>Outside the pilot.</p>")
    citations = []
    for identifier, item_id, content, paragraph, quote in [
        ("review-source", article, "original", 2, "Check several outcomes, not just speed."),
        ("memory-source", video, "summary", 0, "Keep an index of useful lessons.")]:
        item = store.query("SELECT * FROM items WHERE id=?", (item_id,), one=True)
        citations.append({"id": identifier, "item_id": item_id, "content": content,
            "content_hash": fingerprint(item["body_html"] if content == "original" else item["summary_html"]),
            "paragraph": paragraph, "quote": quote})
    manifest = {"version": 1,
        "topics": [{"id": "work", "title": "Work", "question": "How do we check work?", "concept_ids": ["review"]},
                   {"id": "knowledge", "title": "Knowledge", "question": "How do we keep lessons?", "concept_ids": ["review", "memory"]}],
        "concepts": [{"id": "review", "title": "Verification", "description": "Check different outcomes.", "insight_ids": ["check"]},
                     {"id": "memory", "title": "An index before retrieval", "description": "Find the relevant lesson.", "insight_ids": ["index"]}],
        "insights": [{"id": "check", "origin": "ai_draft", "text": "The author recommends checking several outcomes.", "citation_ids": ["review-source"]},
                     {"id": "index", "origin": "ai_draft", "text": "The saved summary describes an index of lessons.", "citation_ids": ["memory-source"]}],
        "citations": citations}
    write(store, manifest)
    return manifest


def write(store, manifest):
    private_write(store.root / PILOT_FILE, json.dumps(manifest))
