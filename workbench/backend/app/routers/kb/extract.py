"""文件解析层：PDF / Office / 图片 / 纯文本。"""

from __future__ import annotations

import base64
import io
import os
import re
import zipfile
from pathlib import Path

from sqlalchemy.orm import Session

from ...llm import client_for
from ...models import Provider, User
from .common import (
    DOC_EXTS,
    IMAGE_EXTS,
    PLAIN_EXTS,
    SUPPORTED_EXTS,
    TEXT_EXTS,
    UPLOAD_DIR,
)

def _extract_doc(data: bytes) -> str:
    """.doc（Word 97）提取：优先 LibreOffice 无头转换获得完整文本；未安装则纯 Python 尽力提取。"""
    import shutil
    import subprocess
    import tempfile

    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if soffice:
        try:
            with tempfile.TemporaryDirectory() as td:
                src = Path(td) / "src.doc"
                src.write_bytes(data)
                subprocess.run(
                    [soffice, "--headless", "--convert-to", "txt:Text", "--outdir", td, str(src)],
                    capture_output=True, timeout=60, check=False,
                )
                out = Path(td) / "src.txt"
                if out.exists():
                    return out.read_text("utf-8", errors="ignore")
        except Exception:
            pass  # 转换失败/超时 → 退回尽力提取
    # 尽力提取：.doc 正文文本常以 utf-16-le 存储，抓取连续可读片段（标注为尽力而为）
    raw = data.decode("utf-16-le", errors="ignore")
    chunks = re.findall(
        r"[\u4e00-\u9fff\w\s，。；：、！？%（）()《》「」“”‘’.,;:!?…\-]{8,}", raw
    )
    text = "\n".join(c.strip() for c in chunks if len(c.strip()) >= 8)
    return f"（.doc 尽力提取，排版可能不完整；如需完整解析请在服务器安装 LibreOffice）\n{text}"


def _extract_text(filename: str, data: bytes) -> str:
    """按扩展名解析文本：文档/表格/代码/网页直读或结构化提取。失败抛 ValueError。"""
    lower = filename.lower()
    if lower.endswith(TEXT_EXTS) or lower.endswith(PLAIN_EXTS):
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return data.decode("gbk", errors="ignore")
    if lower.endswith((".html", ".htm")):
        import html as html_mod

        text = data.decode("utf-8", errors="ignore")
        text = re.sub(r"(?is)<(script|style).*?</\1>", "", text)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        return html_mod.unescape(re.sub(r"\s{2,}", " ", text)).strip()
    if lower.endswith(".xlsx"):
        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        parts = []
        for ws in wb.worksheets:
            parts.append(f"## 工作表：{ws.title}")
            for i, row in enumerate(ws.iter_rows(values_only=True)):
                if i >= 200:
                    parts.append("…（仅前 200 行）")
                    break
                cells = ["" if c is None else str(c) for c in row]
                if any(c.strip() for c in cells):
                    parts.append(" | ".join(cells))
        return "\n".join(parts)
    if lower.endswith(".xls"):
        import pandas as pd

        try:
            sheets = pd.read_excel(io.BytesIO(data), sheet_name=None, engine="xlrd")
        except Exception as e:
            raise ValueError(f".xls 解析失败：{e}")
        parts = []
        for sheet_name, df in list(sheets.items())[:5]:
            parts.append(f"## 工作表：{sheet_name}")
            parts.append(" | ".join(str(c) for c in df.columns))
            for i, row in enumerate(df.itertuples(index=False)):
                if i >= 200:
                    parts.append("…（仅前 200 行）")
                    break
                cells = ["" if (v is None or v != v) else str(v) for v in row]
                if any(c.strip() for c in cells):
                    parts.append(" | ".join(cells))
        return "\n".join(parts) or "（工作簿为空）"
    if lower.endswith(".doc"):
        text = _extract_doc(data)
        if len(text.strip()) < 20:
            raise ValueError(
                ".doc 提取到的文本过少（可能含大量图片/复杂排版）。"
                "建议另存为 .docx，或在服务器安装 LibreOffice 后重试以获得完整解析"
            )
        return text
    if lower.endswith(".pdf"):
        # 同步兜底：仅提取文本层（扫描件的视觉识别走 async extract_pdf）
        import pymupdf

        try:
            doc = pymupdf.open(stream=data, filetype="pdf")
        except Exception as e:
            raise ValueError(f"PDF 打开失败（文件可能损坏或加密）：{e}")
        parts = []
        for page in doc:
            parts.append((page.get_text("text") or "").strip())
        text = "\n\n".join(p for p in parts if p)
        if not text.strip():
            raise ValueError("PDF 无文本层（疑似扫描件），请通过对话附件或视觉识别路径上传")
        return text
    if lower.endswith(".pptx"):
        from pptx import Presentation

        try:
            prs = Presentation(io.BytesIO(data))
        except Exception as e:
            raise ValueError(f"PPTX 解析失败：{e}")
        parts = []
        for i, slide in enumerate(prs.slides, 1):
            seg = [f"## 幻灯片 {i}"]
            for shape in slide.shapes:
                if shape.has_text_frame:
                    t = shape.text_frame.text.strip()
                    if t:
                        seg.append(t)
                if getattr(shape, "has_table", False):
                    for row in shape.table.rows:
                        cells = [c.text.strip() for c in row.cells if c.text.strip()]
                        if cells:
                            seg.append("| " + " | ".join(cells) + " |")
                if getattr(shape, "shape_type", None) == 13 and shape.name:
                    seg.append(f"（含图片：{shape.name}，未识别）")
            if len(seg) > 1:
                parts.append("\n".join(seg))
        return "\n\n".join(parts) or "（幻灯片无文本内容）"
    if lower.endswith(".docx"):
        import docx

        doc = docx.Document(io.BytesIO(data))
        parts = [p.text for p in doc.paragraphs if p.text.strip()]
        for table in doc.tables:
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells if c.text.strip()]
                if cells:
                    parts.append(" | ".join(cells))
        return "\n\n".join(parts)
    # 无扩展名或未知扩展：尝试按纯文本读
    if not lower.endswith(IMAGE_EXTS + (".zip", ".exe", ".bin", ".dmg", ".pkg")) and data:
        if b"\x00" not in data[:4096]:
            try:
                return data.decode("utf-8")
            except UnicodeDecodeError:
                return data.decode("gbk", errors="ignore")
    raise ValueError(f"暂不支持该文件类型：{Path(filename).suffix or filename}")


# ---------------- 视觉识别与统一解析入口 ----------------

VISION_MAX_PAGES = 8  # 单个扫描 PDF 最多视觉识别页数（控制成本与耗时）


# 视觉模型发现关键字（按模型 id 匹配，覆盖各家命名：glm-4.5v / qwen-vl / gpt-4o / gemini …）
VISION_KEYWORDS = ("vision", "-vl", "vl-", "4v", "4.5v", "4.6v", "5v", "gpt-4o", "gemini")


def _vision_target(db: Session, user: User) -> tuple[Provider, str]:
    """挑视觉模型（不写死单一供应商）：env 显式指定 → 用户可见供应商按关键字发现 → 默认模型挂首个可用供应商。"""
    env_pid = os.environ.get("VISION_PROVIDER_ID")
    env_model = os.environ.get("VISION_MODEL")
    if env_pid and env_model:
        try:
            p = db.get(Provider, int(env_pid))
        except ValueError:
            p = None
        if p is not None and (p.owner_id is None or p.owner_id == user.id):
            return p, env_model
    providers = db.scalars(
        select(Provider)
        .where(or_(Provider.owner_id.is_(None), Provider.owner_id == user.id))
        .order_by(Provider.id)
    ).all()
    for p in providers:
        if p.owner_id is None and not p.enabled:
            continue
        for m in json.loads(p.models_json or "[]"):
            mid = (m.get("id") or "").lower()
            if any(k in mid for k in VISION_KEYWORDS):
                return p, m["id"]
    if providers:
        return providers[0], env_model or "glm-4.5v"
    raise ValueError("未配置模型，无法识别图片")


async def recognize_image(db: Session, user: User, filename: str, data: bytes) -> str:
    """图片/扫描页 → 视觉模型识别为 Markdown（文字转录 + 表格重建 + 图表描述）。"""
    provider, vision = _vision_target(db, user)
    ext = filename.lower().rsplit(".", 1)[-1]
    mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp", "gif": "gif", "bmp": "bmp"}.get(ext, "png")
    client = client_for(provider)
    res = await client.chat.completions.create(
        model=vision,
        messages=[{"role": "user", "content": [
            {"type": "text", "text": (
                "请完整识别这张图片：1) 逐字转录图中所有文字（保持原有结构）；"
                "2) 若含表格，输出为 Markdown 表格；"
                "3) 若是图表/截图，补充关键信息（标题、坐标轴、数值趋势）。用 Markdown 输出，不要寒暄。"
            )},
            {"type": "image_url", "image_url": {"url": f"data:image/{mime};base64,{base64.b64encode(data).decode()}"}},
        ]}],
        stream=False,
    )
    text = (res.choices[0].message.content or "").strip()
    if not text:
        raise ValueError("图片识别结果为空")
    return text[:12000]


def _pdf_tables_md(page) -> str:
    """pymupdf 表格检测 → Markdown（保住 PDF 表格结构）。"""
    try:
        finder = page.find_tables()
        tables = finder.tables or []
    except Exception:
        return ""
    out = []
    for t in tables:
        md = []
        for r in t.extract():
            cells = ["" if c is None else str(c).replace("\n", " ").strip() for c in r]
            if any(cells):
                md.append("| " + " | ".join(cells) + " |")
        if md:
            out.append("\n".join(md))
    return "\n\n".join(out)


async def extract_pdf(db: Session, user: User, data: bytes) -> str:
    """PDF 解析主路径：文本层 + 表格检测 + 扫描页视觉识别兜底。"""
    import pymupdf

    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as e:
        raise ValueError(f"PDF 打开失败（文件可能损坏或加密）：{e}")
    parts: list[str] = []
    vision_pages = []
    for i, page in enumerate(doc):
        text = (page.get_text("text") or "").strip()
        tables_md = _pdf_tables_md(page)
        if len(text) >= 20 or tables_md:
            seg = f"【第 {i + 1} 页】\n{text}"
            if tables_md:
                seg += "\n\n" + tables_md
            parts.append(seg)
        else:
            vision_pages.append((i + 1, page))
    for page_no, page in vision_pages[:VISION_MAX_PAGES]:
        try:
            png = page.get_pixmap(dpi=110).tobytes("png")
            text = await recognize_image(db, user, f"page_{page_no}.png", png)
            parts.append(f"【第 {page_no} 页 · 扫描识别】\n{text}")
        except Exception as e:
            parts.append(f"【第 {page_no} 页】（视觉识别失败：{e}）")
    if len(vision_pages) > VISION_MAX_PAGES:
        parts.append(
            f"…另有 {len(vision_pages) - VISION_MAX_PAGES} 页扫描内容未识别（超出单次 {VISION_MAX_PAGES} 页上限）"
        )
    if not parts:
        raise ValueError("PDF 为空或无法解析")
    return "\n\n".join(parts)


def _sniff_ext(data: bytes) -> str:
    """魔数嗅探：扩展名缺失/错误时按文件头识别真实格式，返回小写扩展名（含点）或空串。"""
    if data[:5] == b"%PDF-":
        return ".pdf"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":  # OLE2 复合文档（doc/xls/ppt 旧格式）
        # ① 根目录项 CLSID 精确识别（真实 Office 文件均写入）
        try:
            import struct

            sect_size = 1 << struct.unpack_from("<H", data, 30)[0]
            first_dir = struct.unpack_from("<I", data, 48)[0]
            clsid = data[(first_dir + 1) * sect_size + 80:][:16]
            if clsid.startswith(bytes.fromhex("06090200")) or clsid.startswith(bytes.fromhex("00090200")):
                return ".doc"  # Word 97 / Word 6-95
            if clsid.startswith(bytes.fromhex("20080200")) or clsid.startswith(bytes.fromhex("10080200")):
                return ".xls"  # Excel 97 / Excel 5.0
            if clsid.startswith(bytes.fromhex("108d8164")):
                return ".ppt"
        except Exception:
            pass
        # ② 字符串标记（部分生成器写入）
        head = data[:8192]
        if b"Microsoft Excel" in head:
            return ".xls"
        if b"PowerPoint" in head:
            return ".ppt"
        if b"Microsoft Word" in head:
            return ".doc"
        # ③ 流特征：BIFF8 BOF（Excel）/ FIB magic（Word），位于首个数据扇区
        if b"\x09\x08\x10\x00" in data[512:2048]:
            return ".xls"
        if b"\xec\xa5" in data[512:2048]:
            return ".doc"
        return ".doc"  # OLE 但特征不明：按最常见的 .doc 尽力提取
    if data[:2] == b"PK":  # zip 容器：按内部标志性目录分流
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                names = zf.namelist()
                if any(n.startswith("word/") for n in names):
                    return ".docx"
                if any(n.startswith("ppt/") for n in names):
                    return ".pptx"
                if any(n.startswith("xl/") for n in names):
                    return ".xlsx"
        except Exception:
            pass
    return ""


async def extract_file(db: Session, user: User, filename: str, data: bytes) -> str:
    """统一解析入口：PDF 走视觉增强管线，图片走视觉识别；扩展名未知时按魔数嗅探分流。"""
    lower = filename.lower()
    if not lower.endswith(SUPPORTED_EXTS + PLAIN_EXTS + IMAGE_EXTS + (".html", ".htm")):
        sniffed = _sniff_ext(data)
        if sniffed:
            lower = sniffed  # 信任魔数：扩展名缺失/错误也能正确解析
    if lower.endswith(".pdf"):
        return await extract_pdf(db, user, data)
    if lower.endswith(IMAGE_EXTS):
        return await recognize_image(db, user, filename, data)
    return _extract_text(lower, data)


def _save_source(doc_id: int, filename: str, data: bytes) -> str:
    """保存原文件到 uploads/，返回相对路径。"""
    safe = re.sub(r"[^\w.\u4e00-\u9fff-]+", "_", filename)[:120] or "file"
    rel = f"{doc_id}_{secrets.token_hex(4)}_{safe}"
    (UPLOAD_DIR / rel).write_bytes(data)
    return rel

