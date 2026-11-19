from __future__ import annotations

from html import escape
import re
from urllib.parse import urlparse

ALLOWED = {"div", "p", "h1", "h2", "h3", "h4", "ul", "ol", "li", "blockquote", "strong", "b", "em", "i", "a", "br", "hr", "pre", "code", "table", "thead", "tbody", "tr", "th", "td", "sup", "sub", "span", "figure", "figcaption"}
DROP = {"script", "style", "iframe", "object", "embed", "form", "input", "button", "svg", "math", "link", "meta", "base", "video", "audio", "img"}


def sanitize(body: str) -> str:
    from lxml import html
    if not body or not body.strip():
        return ""
    root = html.fragment_fromstring(body or "<p></p>", create_parent="div")
    for node in list(root.iterdescendants()):
        if not isinstance(node.tag, str):
            node.drop_tree()
            continue
        node.tag = node.tag.split("}")[-1].lower()
        if node.tag in DROP:
            node.drop_tree()
            continue
        if node.tag not in ALLOWED:
            node.drop_tag()
            continue
        href = node.get("href", "")
        node.attrib.clear()
        if node.tag == "a" and urlparse(href).scheme in ("http", "https"):
            node.set("href", href)
            node.set("target", "_blank")
            node.set("rel", "noopener noreferrer")
    return "".join(([escape(root.text)] if root.text else []) + [html.tostring(c, encoding="unicode", method="html") for c in root])


def plain_text(body):
    from lxml import html
    try:
        return html.fragment_fromstring(body or "", create_parent="div").text_content()
    except Exception:
        return re.sub("<[^>]+>", " ", body or "")


def paragraphs_html(paragraphs):
    return "".join("<p>" + escape(str(p)) + "</p>" for p in paragraphs)


def render_newsletter(edition):
    sections = []
    for item in edition["items"]:
        link = f'<a href="{escape(item["url"], quote=True)}">Read the source ↗</a>' if item["url"] else ""
        takeaway = f'<blockquote style="border-left:2px solid #6e8063;margin-left:0;padding-left:16px">{escape(item["takeaway"])}</blockquote>' if item["takeaway"] else ""
        sections.append(f'<section style="padding:24px 0;border-top:1px solid #ddd">'
                        f'<p style="font:12px Arial;color:#65745c">{escape(item["author"])}</p>'
                        f'<h2 style="font-size:25px;line-height:1.3">{escape(item["title"])}</h2>{takeaway}'
                        f'{sanitize(item["summary_html"] or item["body_html"])}<p style="font:12px Arial">{link}</p></section>')
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{escape(edition["title"])}</title></head><body style="margin:0;background:#f7f6f1;color:#272e27">'
            f'<main style="max-width:640px;margin:auto;padding:40px 24px;font:18px/1.75 Georgia,serif">'
            f'<p style="font:12px Arial;letter-spacing:2px;color:#65745c">QUIB27</p>'
            f'<h1 style="font-size:36px;line-height:1.15">{escape(edition["title"])}</h1>'
            f'<p style="font:14px Arial;color:#777">{len(edition["items"])} good things to read. Your time, well spent.</p>'
            f'{"".join(sections)}<p style="font:12px Arial;color:#777;border-top:1px solid #ddd;padding-top:24px">'
            f'Prepared on your device with Quib27.</p></main></body></html>')
