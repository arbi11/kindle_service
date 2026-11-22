"""Read-only knowledge pilot. Private knowledge lives beside the local library.

The three layers are topics, reusable concepts, and suggested insights. Evidence
is attached through citations, each bound to a particular saved text version.
This module does not call a model, write reading state, or follow source URLs.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from .content import sanitize
from .store import READING_CONNECTION_SQL

PILOT_FILE = "knowledge/pilot.json"
PASSAGE_TAGS = {"p", "h1", "h2", "h3", "h4", "li", "pre", "blockquote", "figcaption"}


def normalize(text):
    return " ".join(text.split())


def passages(body):
    """Leaf reading blocks, in document order; mirrored by the reader UI."""
    from lxml import html
    root = html.fragment_fromstring(sanitize(body) or "<p></p>", create_parent="div")
    return [normalize(node.text_content()) for node in root.iterdescendants()
            if node.tag in PASSAGE_TAGS and not node.xpath(".//p|.//li|.//pre|.//blockquote")
            and normalize(node.text_content())]


def fingerprint(body):
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def validate(manifest):
    """Validate the bounded graph before any references reach the UI."""
    if not isinstance(manifest, dict) or type(manifest.get("version")) is not int or manifest["version"] != 1:
        raise ValueError("This knowledge pilot uses an unsupported format.")
    groups = {}
    for group in ("topics", "concepts", "insights", "citations"):
        records = manifest.get(group)
        if not isinstance(records, list) or len(records) > 2000:
            raise ValueError("Invalid knowledge " + group + ".")
        index = {}
        for record in records:
            if not isinstance(record, dict) or not isinstance(record.get("id"), str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,79}", record["id"]):
                raise ValueError("Invalid knowledge identifier.")
            if record["id"] in index:
                raise ValueError("Duplicate knowledge identifier.")
            index[record["id"]] = record
        groups[group] = index

    def text(record, key, maximum=1500):
        value = record.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > maximum:
            raise ValueError("Invalid knowledge " + key + ".")

    def references(record, key, target):
        values = record.get(key)
        if not isinstance(values, list) or not values or any(not isinstance(v, str) for v in values) or len(values) != len(set(values)) or any(v not in groups[target] for v in values):
            raise ValueError("A knowledge link points to a missing or duplicate record.")

    for record in groups["topics"].values():
        text(record, "title", 200)
        text(record, "question")
        references(record, "concept_ids", "concepts")
    for record in groups["concepts"].values():
        text(record, "title", 200)
        text(record, "description")
        references(record, "insight_ids", "insights")
    for record in groups["insights"].values():
        text(record, "text")
        if record.get("origin") != "ai_draft":
            raise ValueError("Pilot suggestions must be labelled as AI drafts.")
        references(record, "citation_ids", "citations")
    for record in groups["citations"].values():
        text(record, "item_id", 100)
        text(record, "quote", 2000)
        if record.get("content") not in ("original", "summary") or type(record.get("paragraph")) is not int or not 0 <= record["paragraph"] < 100000:
            raise ValueError("Invalid saved passage location.")
        if not isinstance(record.get("content_hash"), str) or not re.fullmatch(r"[0-9a-f]{64}", record["content_hash"]):
            raise ValueError("Invalid saved passage version.")
    return groups


def load(store):
    path = store.root / PILOT_FILE
    if not path.is_file():
        return {"available": False}
    with path.open("rb") as stream:
        payload = stream.read(2 * 1024 * 1024 + 1)
    if len(payload) > 2 * 1024 * 1024:
        raise ValueError("This knowledge pilot is too large.")
    try:
        manifest = json.loads(payload)
    except (ValueError, UnicodeError):
        raise ValueError("The local knowledge pilot could not be read.") from None
    groups = validate(manifest)
    ids = list({c["item_id"] for c in groups["citations"].values()})
    items = {r["id"]: r for r in store.query(
        "SELECT * FROM items WHERE id IN (" + ",".join("?" for _ in ids) + ")", ids)} if ids else {}
    blocks = {}
    citations = []
    for source in groups["citations"].values():
        citation = dict(source)
        item = items.get(source["item_id"])
        citation.update(available=False, reason="This saved reading is no longer in the library.",
                        title="Missing reading", author="", connection_id="", kind="", material_label="Saved text", has_original=False)
        if item:
            body = item["summary_html"] if source["content"] == "summary" else item["body_html"]
            label = ("Saved video summary" if item["kind"] == "video" else "AI summary") if source["content"] == "summary" else ("Saved transcript" if item["kind"] == "video" else "Saved article")
            citation.update(title=item["title"], author=item["author"], connection_id=item["connection_id"],
                            kind=item["kind"], material_label=label, published=item["published"], has_original=bool(item["body_html"]))
            if fingerprint(body) != source["content_hash"]:
                citation["reason"] = "The saved text has changed. This passage needs checking again."
            else:
                key = (item["id"], source["content"])
                if key not in blocks:
                    blocks[key] = passages(body)
                paragraph = source["paragraph"]
                if paragraph < len(blocks[key]) and source["quote"] in blocks[key][paragraph]:
                    citation.update(available=True, reason="")
                else:
                    citation["reason"] = "The cited passage could not be located in this saved text."
        citations.append(citation)
    total = store.query("SELECT COUNT(*) total FROM items WHERE is_sample=0", one=True)["total"]
    return {"available": True, "topics": list(groups["topics"].values()),
            "concepts": list(groups["concepts"].values()), "insights": list(groups["insights"].values()),
            "citations": citations, "stats": {"topics": len(groups["topics"]), "concepts": len(groups["concepts"]),
            "insights": len(groups["insights"]), "readings": len(items), "library": total,
            "unavailable_citations": sum(not c["available"] for c in citations)}}


def reading_day(published, zone):
    """Interpret a saved publication time in the reader's configured timezone."""
    try:
        parsed = datetime.fromisoformat(published.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(zone).date().isoformat()
    except (ValueError, TypeError, AttributeError, OverflowError):
        return None


def graph(store, *, connection="all", start="", end=""):
    """Build a source-backed graph; filter evidence before retaining its parents.

    Source connections cover all saved reading. Semantic links come only from the
    explicitly mapped concepts; a source link never implies a semantic finding.
    """
    if not isinstance(connection, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", connection):
        raise ValueError("Choose a valid connection.")
    for value in (start, end):
        if value:
            try:
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                    raise ValueError()
                date.fromisoformat(value)
            except (TypeError, ValueError):
                raise ValueError("Use publication dates in YYYY-MM-DD format.") from None
    if start and end and start > end:
        raise ValueError("The start date must be on or before the end date.")
    zone_name = store.settings()["timezone"]
    zone = ZoneInfo(zone_name)
    readings = store.query(f"""SELECT id,title,author,{READING_CONNECTION_SQL} AS connection_id,kind,published FROM items
        WHERE is_sample=CASE WHEN EXISTS(SELECT 1 FROM items WHERE is_sample=0) THEN 0 ELSE 1 END
        ORDER BY published DESC,id""")
    for item in readings:
        item["published_day"] = reading_day(item["published"], zone)
        item["connection_id"] = item["connection_id"] or "other"
    all_days = [i["published_day"] for i in readings if i["published_day"]]
    chosen = {i["id"]: i for i in readings
              if (connection == "all" or i["connection_id"] == connection)
              and (not start or i["published_day"] is not None and i["published_day"] >= start)
              and (not end or i["published_day"] is not None and i["published_day"] <= end)}
    warning = ""
    try:
        brain = load(store)
    except (ValueError, OSError):
        brain = {"available": False}
        warning = "Concept notes could not be loaded. The graph still shows your saved readings and their connections."
    citations = {c["id"]: c for c in brain.get("citations", []) if c["item_id"] in chosen and c["available"]}
    insights = [{**i, "citation_ids": [cid for cid in i["citation_ids"] if cid in citations]}
                for i in brain.get("insights", []) if any(cid in citations for cid in i["citation_ids"])]
    insight_ids = {i["id"] for i in insights}
    concepts = [{**c, "insight_ids": [i for i in c["insight_ids"] if i in insight_ids]}
                for c in brain.get("concepts", []) if any(i in insight_ids for i in c["insight_ids"])]
    concept_ids = {c["id"] for c in concepts}
    topics = [{**t, "concept_ids": [c for c in t["concept_ids"] if c in concept_ids]}
              for t in brain.get("topics", []) if any(c in concept_ids for c in t["concept_ids"])]
    mapped = {c["item_id"] for c in citations.values()}
    names = {"youtube": "YouTube", "raindrop": "Raindrop", "freedium": "Freedium",
             "substack": "Substack", "websites": "Websites & blogs", "medium": "Medium", "publications": "Publications", "other": "Other reading"}
    nodes, edges = [], []

    def link(source, target, relation):
        edges.append({"id": relation + ":" + source + ":" + target,
                      "from": source, "to": target, "relation": relation})

    for cid in sorted({i["connection_id"] for i in chosen.values()}):
        nodes.append({"id": "connection:" + cid, "type": "connection", "label": names.get(cid, cid),
                      "connection_id": cid, "description": "Reading collected through this connection."})
    for item in chosen.values():
        nodes.append({**item, "id": "reading:" + item["id"], "item_id": item["id"],
                      "label": item["title"], "type": "reading", "mapped": item["id"] in mapped})
        link("connection:" + item["connection_id"], "reading:" + item["id"], "collected")
    for topic in topics:
        nodes.append({"id": "topic:" + topic["id"], "type": "topic", "label": topic["title"], "description": topic["question"]})
        for cid in topic["concept_ids"]:
            link("topic:" + topic["id"], "concept:" + cid, "includes")
    for concept in concepts:
        nodes.append({"id": "concept:" + concept["id"], "type": "concept", "label": concept["title"], "description": concept["description"]})
        for iid in concept["insight_ids"]:
            link("concept:" + concept["id"], "insight:" + iid, "perspective")
    for insight in insights:
        nodes.append({"id": "insight:" + insight["id"], "type": "insight", "label": insight["text"],
                      "description": insight["text"], "origin": insight["origin"], "citation_ids": insight["citation_ids"]})
        for cid in insight["citation_ids"]:
            link("insight:" + insight["id"], "reading:" + citations[cid]["item_id"], "cites")
    return {"nodes": nodes, "edges": edges, "citations": list(citations.values()),
            "insights": insights, "concepts": concepts, "topics": topics, "warning": warning,
            "stats": {"readings": len(chosen), "library": len(readings), "mapped": len(mapped),
                      "unmapped": len(chosen) - len(mapped), "topics": len(topics), "concepts": len(concepts),
                      "insights": len(insights), "nodes": len(nodes), "edges": len(edges)},
            "filters": {"connection": connection, "from": start, "to": end, "timezone": zone_name,
                        "earliest": min(all_days) if all_days else "", "latest": max(all_days) if all_days else ""}}
