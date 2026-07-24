"""Web / XML Agent (L2) — HTML, XML, GeoJSON.

HTML is parsed with BeautifulSoup (handles real-world malformed markup
far better than the stdlib parser); XML/GeoJSON with lxml for speed.
"""
import asyncio
import json
import logging

from bs4 import BeautifulSoup
from lxml import etree

from app.models.schemas import FileCategory, ParsedDocument, TableBlock, TextBlock
from app.parsers.base import BaseParser

logger = logging.getLogger(__name__)


class WebParser(BaseParser):
    category = FileCategory.WEB

    async def parse(self, file_path: str) -> ParsedDocument:
        return await asyncio.to_thread(self._parse_sync, file_path)

    def _parse_sync(self, file_path: str) -> ParsedDocument:
        doc = ParsedDocument(source_file=file_path, category=self.category)
        lower = file_path.lower()
        try:
            if lower.endswith((".html", ".htm")):
                self._parse_html(file_path, doc)
            elif lower.endswith((".geojson",)) or self._looks_like_json(file_path):
                self._parse_geojson(file_path, doc)
            else:
                self._parse_xml(file_path, doc)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Web/XML parse failed on %s", file_path)
            doc.warnings.append(f"Web/XML parse error: {exc}")
        return doc

    def _looks_like_json(self, file_path: str) -> bool:
        try:
            with open(file_path, "rb") as f:
                head = f.read(200).lstrip()
            return head.startswith(b"{") or head.startswith(b"[")
        except OSError:
            return False

    def _parse_html(self, file_path: str, doc: ParsedDocument) -> None:
        with open(file_path, encoding="utf-8", errors="replace") as f:
            soup = BeautifulSoup(f.read(), "html.parser")

        title = soup.title.string.strip() if soup.title and soup.title.string else None
        if title:
            doc.text_blocks.append(TextBlock(text=title, kind="heading"))

        for tag in soup.find_all(["script", "style", "noscript"]):
            tag.decompose()

        for heading in soup.find_all(["h1", "h2", "h3"]):
            text = heading.get_text(strip=True)
            if text:
                doc.text_blocks.append(TextBlock(text=text, kind="heading"))

        for p in soup.find_all(["p", "li", "td"]):
            text = p.get_text(strip=True)
            if text:
                doc.text_blocks.append(TextBlock(text=text, kind="paragraph"))

        for table in soup.find_all("table"):
            rows = [[cell.get_text(strip=True) for cell in row.find_all(["td", "th"])]
                    for row in table.find_all("tr")]
            rows = [r for r in rows if r]
            if rows:
                doc.tables.append(TableBlock(headers=rows[0], rows=rows[1:]))

        doc.metadata = {"title": title, "parser": "beautifulsoup4"}
        if not doc.text_blocks:
            doc.warnings.append("No extractable text found in HTML.")

    def _parse_xml(self, file_path: str, doc: ParsedDocument) -> None:
        parser = etree.XMLParser(recover=True, resolve_entities=False, no_network=True)
        tree = etree.parse(file_path, parser)
        root = tree.getroot()

        def walk(el, depth: int = 0) -> None:
            if depth > 50:  # guard against pathological/adversarial nesting
                return
            text = (el.text or "").strip()
            if text:
                tag = etree.QName(el).localname if el.tag is not None else "element"
                doc.text_blocks.append(TextBlock(text=f"{tag}: {text}", kind="paragraph"))
            for child in el:
                walk(child, depth + 1)

        walk(root)
        doc.metadata = {"root_tag": etree.QName(root).localname, "parser": "lxml"}
        if not doc.text_blocks:
            doc.warnings.append("No extractable text found in XML document.")

    def _parse_geojson(self, file_path: str, doc: ParsedDocument) -> None:
        with open(file_path, encoding="utf-8") as f:
            data = json.load(f)
        features = data.get("features", []) if isinstance(data, dict) else []
        doc.text_blocks.append(TextBlock(text=f"GeoJSON with {len(features)} feature(s).", kind="heading"))

        headers = ["feature_index", "geometry_type", "properties"]
        rows = []
        for i, feat in enumerate(features[:500]):
            geom = (feat.get("geometry") or {}).get("type", "")
            props = json.dumps(feat.get("properties", {}), default=str)[:300]
            rows.append([str(i), geom, props])
            doc.text_blocks.append(TextBlock(text=f"Feature {i} ({geom}): {props}", kind="paragraph"))
        if rows:
            doc.tables.append(TableBlock(headers=headers, rows=rows, caption="Features"))

        doc.metadata = {"feature_count": len(features), "parser": "geojson"}
