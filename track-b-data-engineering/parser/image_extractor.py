"""Extract embedded images from the source xlsx + dedupe by SHA-256.

An xlsx is a ZIP archive containing image binaries at `xl/media/*.{png,jpg}`.
This module reads them directly via stdlib `zipfile` — more reliable than
openpyxl's `ws._images` which only populates lazily under specific
read-modes and skips images attached via drawings XML.

The Kayo source xlsx has 1,586 image files but ~382 unique image bytes
(same image appears across multiple sheets). We dedupe by SHA-256 so the
Vision LLM gets called once per unique image, not once per reference.
"""

from __future__ import annotations

import hashlib
import logging
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class EmbeddedImage:
    sha256: str
    extension: str  # "png" | "jpg"
    size_bytes: int
    raw_bytes: bytes
    source_sheets: tuple[str, ...]


def extract_unique_images(path: Path | str) -> list[EmbeddedImage]:
    """Read every xl/media/* in the xlsx zip, dedupe by SHA-256.

    Cross-references each image to the sheets it appears in via the
    drawings XML (xl/worksheets/_rels/sheetN.xml.rels + xl/drawings/*.xml).
    """
    p = Path(path)
    with zipfile.ZipFile(p) as zf:
        names = zf.namelist()
        media_paths = sorted(n for n in names if n.startswith("xl/media/"))

        # image-path → set of sheet names that reference it
        image_to_sheets = _build_image_sheet_index(zf, names)

        seen: dict[str, dict] = {}
        for media in media_paths:
            raw = zf.read(media)
            sha = hashlib.sha256(raw).hexdigest()
            ext = media.rsplit(".", 1)[-1].lower()
            if ext not in ("png", "jpg", "jpeg", "gif", "webp"):
                continue
            sheets = image_to_sheets.get(media, set())
            if sha in seen:
                seen[sha]["sheets"].update(sheets)
            else:
                seen[sha] = {
                    "bytes": raw,
                    "ext": "jpg" if ext == "jpeg" else ext,
                    "sheets": set(sheets),
                }

    log.info("Extracted %d unique images from %s", len(seen), p)
    return [
        EmbeddedImage(
            sha256=sha,
            extension=meta["ext"],
            size_bytes=len(meta["bytes"]),
            raw_bytes=meta["bytes"],
            source_sheets=tuple(sorted(meta["sheets"])),
        )
        for sha, meta in sorted(seen.items())
    ]


_NS = {
    "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}
_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def _build_image_sheet_index(zf: zipfile.ZipFile, names: list[str]) -> dict[str, set[str]]:
    """Trace each image file back to the sheets that reference it.

    The xlsx relationship graph is:
        xl/workbook.xml          sheet name → sheet xml path
        xl/_rels/workbook.xml.rels    resolves sheet path → rId
        xl/worksheets/sheetN.xml.rels  resolves drawingN.xml → rId
        xl/drawings/_rels/drawingN.xml.rels  resolves image path → rId
    """
    # 1) sheet path → sheet name
    workbook_path = "xl/workbook.xml"
    if workbook_path not in names:
        return {}
    wb_tree = ET.fromstring(zf.read(workbook_path))
    rels_tree = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    rid_to_target: dict[str, str] = {}
    for rel in rels_tree.findall(f"{{{_REL_NS}}}Relationship"):
        rid_to_target[rel.attrib["Id"]] = rel.attrib["Target"]

    sheet_path_to_name: dict[str, str] = {}
    for sheet in wb_tree.findall("main:sheets/main:sheet", _NS):
        name = sheet.attrib.get("name", "")
        rid = sheet.attrib.get(f"{{{_NS['r']}}}id", "")
        target = rid_to_target.get(rid, "")
        if target:
            # Targets are relative to xl/ (Excel convention).
            full = "xl/" + target.lstrip("/").removeprefix("xl/")
            sheet_path_to_name[full] = name

    # 2) sheet xml → list of drawing.xml referenced
    image_to_sheets: dict[str, set[str]] = {}
    sheet_re = re.compile(r"xl/worksheets/(sheet\d+)\.xml$")
    for sheet_path, sheet_name in sheet_path_to_name.items():
        m = sheet_re.match(sheet_path)
        if not m:
            continue
        rels_path = f"xl/worksheets/_rels/{m.group(1)}.xml.rels"
        if rels_path not in names:
            continue
        sheet_rels = ET.fromstring(zf.read(rels_path))
        for rel in sheet_rels.findall(f"{{{_REL_NS}}}Relationship"):
            target = rel.attrib.get("Target", "")
            if "drawings/" not in target:
                continue
            # target like ../drawings/drawing7.xml
            drawing_path = _resolve_target(sheet_path, target)
            for image_path in _drawings_to_images(zf, drawing_path, names):
                image_to_sheets.setdefault(image_path, set()).add(sheet_name)

    return image_to_sheets


def _drawings_to_images(zf: zipfile.ZipFile, drawing_path: str, names: list[str]) -> list[str]:
    """Given an xl/drawings/drawingN.xml, return the image file paths it references."""
    if drawing_path not in names:
        return []
    drawing_rels = drawing_path.replace("xl/drawings/", "xl/drawings/_rels/") + ".rels"
    if drawing_rels not in names:
        return []
    rels_tree = ET.fromstring(zf.read(drawing_rels))
    out: list[str] = []
    for rel in rels_tree.findall(f"{{{_REL_NS}}}Relationship"):
        target = rel.attrib.get("Target", "")
        if "media/" in target:
            full = _resolve_target(drawing_path, target)
            out.append(full)
    return out


def _resolve_target(base: str, target: str) -> str:
    """Resolve a relative xlsx target path against a base XML file path."""
    base_dir = "/".join(base.split("/")[:-1])
    if target.startswith("/"):
        return target.lstrip("/")
    parts = (base_dir + "/" + target).split("/")
    resolved: list[str] = []
    for p in parts:
        if p == "" or p == ".":
            continue
        if p == "..":
            if resolved:
                resolved.pop()
            continue
        resolved.append(p)
    return "/".join(resolved)
