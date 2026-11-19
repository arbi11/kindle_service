"""Small EPUB 3 writer. Uses ZIP/XML directly; no EbookLib dependency."""
from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from pathlib import Path
import re
import uuid
import zipfile
from xml.etree import ElementTree as ET

CSS = """body{font-family:serif;line-height:1.6;margin:5%;color:#202420}
h1{font-size:1.7em;line-height:1.2} h2{font-size:1.3em}
.meta,.source{font-family:sans-serif;font-size:.8em;color:#666}
blockquote{border-left:2px solid #8b997d;padding-left:1em;margin-left:0}
img{max-width:100%;height:auto}pre{white-space:pre-wrap}a{color:#456047}
table{border-collapse:collapse}td,th{border:1px solid #aaa;padding:.3em}
"""


def xhtml(title: str, body: str, language: str = "en") -> bytes:
    from lxml import html, etree

    root = html.fragment_fromstring(body or "<p></p>", create_parent="div")
    for node in root.iter():
        if isinstance(node.tag, str):
            node.tag = "{http://www.w3.org/1999/xhtml}" + node.tag.split("}")[-1].lower()
    xml_root = ET.fromstring(etree.tostring(root, encoding="utf-8", method="xml"))
    ET.register_namespace("", "http://www.w3.org/1999/xhtml")
    fragment = ET.tostring(xml_root, encoding="unicode")
    return (f'<?xml version="1.0" encoding="utf-8"?>\n'
            f'<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="{escape(language)}" lang="{escape(language)}">'
            f'<head><title>{escape(title)}</title><link href="style.css" rel="stylesheet" type="text/css"/>'
            f'</head><body>{fragment}</body></html>').encode()


def write_epub(path: Path, title: str, chapters: list[tuple[str, str]], *,
               author: str = "Daily Edition", language: str = "en", identifier: str | None = None,
               css: str = CSS, images: list[tuple[str, bytes]] | None = None) -> Path:
    if not chapters:
        raise ValueError("An edition needs at least one chapter")
    identifier = identifier or f"urn:uuid:{uuid.uuid4()}"
    modified = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entries = [(f"chapter-{i}.xhtml", name, body) for i, (name, body) in enumerate(chapters, 1)]
    manifest = ['<item id="style" href="style.css" media-type="text/css"/>',
                '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
                '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>']
    manifest += [f'<item id="c{i}" href="{file}" media-type="application/xhtml+xml"/>'
                 for i, (file, _, _) in enumerate(entries, 1)]
    seen = set()
    resources = []
    for name, data in images or []:
        if name in seen or not re.fullmatch(r"images/[A-Za-z0-9_.-]+\.(jpg|jpeg|png|gif|webp)", name):
            continue
        seen.add(name)
        media = "image/jpeg" if name.rsplit(".", 1)[1] in ("jpg", "jpeg") else "image/" + name.rsplit(".", 1)[1]
        manifest.append(f'<item id="img{len(resources)}" href="{name}" media-type="{media}"/>')
        resources.append((name, data))
    spine = "".join(f'<itemref idref="c{i}"/>' for i in range(1, len(entries) + 1))
    opf = (f'<?xml version="1.0" encoding="utf-8"?>'
           f'<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="book-id" version="3.0" xml:lang="{escape(language)}">'
           f'<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
           f'<dc:identifier id="book-id">{escape(identifier)}</dc:identifier>'
           f'<dc:title>{escape(title)}</dc:title><dc:language>{escape(language)}</dc:language>'
           f'<dc:creator>{escape(author)}</dc:creator><meta property="dcterms:modified">{modified}</meta>'
           f'</metadata><manifest>{"".join(manifest)}</manifest><spine toc="ncx">{spine}</spine></package>')
    links = "".join(f'<li><a href="{file}">{escape(name)}</a></li>' for file, name, _ in entries)
    nav = (f'<?xml version="1.0" encoding="utf-8"?>'
           f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="{escape(language)}">'
           f'<head><title>Contents</title></head><body><nav epub:type="toc" id="toc">'
           f'<h1>{escape(title)}</h1><ol>{links}</ol></nav></body></html>')
    points = "".join(f'<navPoint id="p{i}" playOrder="{i}"><navLabel><text>{escape(name)}</text></navLabel>'
                     f'<content src="{file}"/></navPoint>' for i, (file, name, _) in enumerate(entries, 1))
    ncx = (f'<?xml version="1.0" encoding="utf-8"?>'
           f'<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
           f'<head><meta name="dtb:uid" content="{escape(identifier)}"/></head>'
           f'<docTitle><text>{escape(title)}</text></docTitle><navMap>{points}</navMap></ncx>')
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".epub.tmp")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        archive.writestr("OEBPS/content.opf", opf)
        archive.writestr("OEBPS/nav.xhtml", nav)
        archive.writestr("OEBPS/toc.ncx", ncx)
        archive.writestr("OEBPS/style.css", css)
        for file, name, body in entries:
            archive.writestr("OEBPS/" + file, xhtml(name, body, language))
        for name, data in resources:
            archive.writestr("OEBPS/" + name, data)
    temporary.replace(path)
    return path
