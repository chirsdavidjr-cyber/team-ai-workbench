import base64
import io
import json
import os
import re
import zipfile
import secrets
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select, or_, and_, func
from sqlalchemy.orm import Session, load_only

from ..deps import get_current_user, get_admin_user, get_db
from ..kb_service import chunk_markdown, embed_texts, ai_preliminary_review
from ..llm import client_for
from ..models import KbChunk, KbDoc, KbFolder, KbLink, KbReview, Provider, User

router = APIRouter(prefix="/api/kb", tags=["knowledge-base"])

UPLOAD_DIR = Path(__file__).resolve().parent.parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

# 支持的知识文件类型
TEXT_EXTS = (".md", ".markdown", ".txt")
DOC_EXTS = (".pdf", ".docx", ".pptx", ".doc", ".xls")
SUPPORTED_EXTS = TEXT_EXTS + DOC_EXTS
# 对话附件额外支持的文本类格式（解析层放开；知识库上传仍按 SUPPORTED_EXTS 把关）
PLAIN_EXTS = (
    ".csv", ".tsv", ".log", ".json", ".xml", ".yaml", ".yml", ".toml", ".ini", ".conf",
    ".py", ".js", ".mjs", ".ts", ".tsx", ".jsx", ".java", ".c", ".h", ".cpp", ".hpp",
    ".cs", ".go", ".rs", ".rb", ".php", ".sh", ".bat", ".sql", ".css", ".scss", ".vue",
    ".swift", ".kt", ".r", ".m",
)
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")


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


def _folder_scope_filter(scope: str, user: User):
    """文件夹可见范围：团队文件夹全员可见；个人文件夹仅本人。"""
    if scope == "team":
        return KbFolder.scope == "team"
    return and_(KbFolder.scope == "personal", KbFolder.owner_id == user.id)

IGNORE_DIRS = {".obsidian", ".trash", ".git", "__pycache__", "node_modules"}

WIKI_LINK_RE = re.compile(r"\[\[([^\]\|#]+)(?:[|#][^\]]*)?\]\]")
TAG_RE = re.compile(
    r"(?:^|[\s。，,.;；:：!！?？()（）\[\]【】\"'“”])#([^\s#，。,.；;：:！！？？]{1,30})"
)


def _fix_zip_name(name: str) -> str:
    """修复 zip 中文文件名：macOS/Windows 打包常不设 UTF-8 flag，zipfile 按 cp437 解成乱码。"""
    if all(ord(c) < 128 for c in name):
        return name
    for enc in ("utf-8", "gbk"):
        try:
            return name.encode("cp437").decode(enc)
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
    return name


def _parse_meta(content: str) -> tuple[list[str], list[str]]:
    """解析 [[双链目标标题]] 与 #标签。"""
    links = []
    for m in WIKI_LINK_RE.finditer(content):
        t = m.group(1).strip()
        if t and t not in links:
            links.append(t)
    tags = []
    for m in TAG_RE.finditer(content):
        t = m.group(1).strip()
        if t and t not in tags:
            tags.append(t)
    return links, tags


def _visible_filter(user: User):
    """本人全部 + 团队池（scope=team 或个人文档审核通过入团队库）。"""
    return or_(
        KbDoc.owner_id == user.id,
        KbDoc.scope == "team",
        KbDoc.team_status == "approved",
    )


async def _reindex_doc(db: Session, doc: KbDoc) -> None:
    """重建切块、向量与双链关系。draft（未入个人库）文档只更新元数据不索引。"""
    db.query(KbChunk).where(KbChunk.doc_id == doc.id).delete()
    if doc.status != "draft":
        chunks = chunk_markdown(doc.content)
        vectors = await embed_texts(chunks)
        for i, text in enumerate(chunks):
            db.add(
                KbChunk(
                    doc_id=doc.id,
                    idx=i,
                    text=text,
                    embedding_json=(json.dumps(vectors[i]) if vectors else None),
                )
            )
        doc.chunk_count = len(chunks)
        doc.status = "ready"
    else:
        doc.chunk_count = 0

    # 双链：目标存在则回填 target_doc_id（目标须在本文档作者可见范围内）
    db.query(KbLink).where(KbLink.source_id == doc.id).delete()
    link_titles, tags = _parse_meta(doc.content)
    doc.tags_json = json.dumps(tags, ensure_ascii=False)
    for t in link_titles:
        target = db.scalar(
            select(KbDoc).where(
                KbDoc.title == t,
                or_(
                    KbDoc.scope == "team",
                    KbDoc.team_status == "approved",
                    KbDoc.owner_id == doc.owner_id,
                ),
            )
        )
        db.add(
            KbLink(
                source_id=doc.id,
                target_title=t,
                target_doc_id=target.id if target else None,
            )
        )
    # 状态只在上方非 draft 分支设置；draft（未入个人库）保持 draft，避免"移出个人库"被覆盖失效
    doc.size = len(doc.content.encode("utf-8"))


def _doc_out(d: KbDoc, link_count: int | None = None) -> dict:
    return {
        "id": d.id,
        "title": d.title,
        "path": d.path,
        "scope": d.scope,
        "team_status": d.team_status,
        "owner_id": d.owner_id,
        "size": d.size,
        "tags": d.tags,
        "summary": d.summary,
        "has_source_file": bool(d.source_file),
        "chunk_count": d.chunk_count,
        "status": d.status,
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "updated_at": d.updated_at.isoformat() if d.updated_at else None,
        "link_count": link_count,
    }


async def _ingest(
    db: Session, user: User, scope: str, title: str, path: str, text: str,
    source_name: str | None = None, source_data: bytes | None = None,
) -> KbDoc:
    doc = KbDoc(owner_id=user.id, scope=scope, title=title, path=path, content=text)
    db.add(doc)
    db.flush()
    if source_data is not None and source_name:
        try:
            doc.source_file = _save_source(doc.id, source_name, source_data)
        except OSError:
            doc.source_file = ""  # 存盘失败不阻断入库
    await _reindex_doc(db, doc)
    return doc


async def _gen_summary_bg(doc_id: int) -> None:
    """后台生成 AI 摘要与智能标签（失败静默，不影响上传速度）。"""
    from ..db import SessionLocal
    from ..kb_service import _kb_llm

    with SessionLocal() as db:
        doc = db.get(KbDoc, doc_id)
        if doc is None or not doc.content.strip():
            return
        try:
            raw = await _kb_llm(
                db,
                "你是知识管理员。阅读文档，仅返回 JSON："
                '{"summary":"100字以内中文摘要","tags":["3-5个智能分类标签（词组，不带#）"]}。'
                "摘要提炼核心内容与适用场景；标签用于归类检索。",
                f"标题：{doc.title}\n\n{doc.content[:4000]}",
            )
            m = re.search(r"\{.*\}", raw, re.S)
            if not m:
                return
            parsed = json.loads(m.group(0))
            summary = str(parsed.get("summary", "")).strip()
            ai_tags = [str(t).strip().lstrip("#") for t in parsed.get("tags", []) if str(t).strip()][:5]
            if summary:
                doc.summary = summary
            if ai_tags:
                merged = list(dict.fromkeys(doc.tags + ai_tags))
                doc.tags_json = json.dumps(merged, ensure_ascii=False)
            db.commit()
        except Exception:
            return  # 摘要失败不影响文档本身


def _clean_title(filename: str) -> str:
    return re.sub(r"\.(md|markdown|txt|pdf|docx)$", "", filename, flags=re.I)[:120] or "未命名"


@router.post("/upload")
async def upload(
    background: BackgroundTasks,
    files: list[UploadFile] = File(...),
    scope: str = Form("personal"),
    target: str = Form("收件箱"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """上传知识文件。target：目标文件夹（默认收件箱，符合"先进收件箱再整理"的工作流）。"""
    if scope not in ("personal", "team"):
        raise HTTPException(status_code=400, detail="scope 参数不合法")
    if scope == "team" and user.role != "admin":
        raise HTTPException(status_code=403, detail="团队库上传需要管理员权限")

    target = target.strip().strip("/")
    if target in ("原始资料", "知识库", "收件箱", "来源台账"):
        pass  # 一级分区允许
    elif target and target.split("/")[0] not in ("原始资料", "知识库", "收件箱", "来源台账", ""):
        pass  # 自由文件夹也允许（未初始化工作流的库）
    prefix = f"{target}/" if target else ""

    created, skipped = [], []
    for f in files:
        name = f.filename or ""
        data = await f.read()

        if name.lower().endswith(".zip"):
            try:
                zf = zipfile.ZipFile(io.BytesIO(data))
            except zipfile.BadZipFile:
                skipped.append({"name": name, "reason": "zip 文件损坏"})
                continue
            for info in zf.infolist():
                if info.is_dir():
                    continue
                if not (info.flag_bits & 0x800):
                    info.filename = _fix_zip_name(info.filename)
                rel = info.filename
                parts = rel.split("/")
                if any(p in IGNORE_DIRS for p in parts[:-1]):
                    continue
                if not rel.lower().endswith(SUPPORTED_EXTS):
                    continue
                raw = zf.read(info)
                try:
                    text = await extract_file(db, user, rel, raw)
                except ValueError as e:
                    skipped.append({"name": rel, "reason": str(e)})
                    continue
                except Exception:
                    skipped.append({"name": rel, "reason": "解析失败"})
                    continue
                base = parts[-1]
                doc = await _ingest(
                    db, user, scope, _clean_title(base), prefix + rel, text,
                    source_name=base, source_data=raw,
                )
                created.append(_doc_out(doc))
        elif name.lower().endswith(SUPPORTED_EXTS):
            try:
                text = await extract_file(db, user, name, data)
            except ValueError as e:
                skipped.append({"name": name, "reason": str(e)})
                continue
            except Exception:
                skipped.append({"name": name, "reason": "解析失败（文件可能损坏或加密）"})
                continue
            doc = await _ingest(
                db, user, scope, _clean_title(name), prefix + name, text,
                source_name=name, source_data=data,
            )
            created.append(_doc_out(doc))
        else:
            skipped.append({
                "name": name,
                "reason": "支持 .md / .txt / .pdf / .docx / .pptx / .doc / .xls / .zip",
            })

    db.commit()
    for d in created:
        background.add_task(_gen_summary_bg, d["id"])
    return {"docs": created, "skipped": skipped}


@router.get("/docs")
def list_docs(
    scope: str = "personal",
    q: str = "",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if scope not in ("personal", "team", "all"):
        scope = "personal"
    stmt = select(KbDoc).where(_visible_filter(user))
    if scope == "personal":
        stmt = stmt.where(KbDoc.scope == "personal", KbDoc.owner_id == user.id)
    elif scope == "team":
        stmt = stmt.where(or_(KbDoc.scope == "team", KbDoc.team_status == "approved"))
    q = q.strip()
    if q:
        like = f"%{q}%"
        stmt = stmt.where(KbDoc.title.like(like) | KbDoc.content.like(like))
    # 列表只需元数据：排除 content 全文（_doc_out 未使用 content），大库首屏瘦身
    stmt = stmt.options(
        load_only(
            KbDoc.id, KbDoc.owner_id, KbDoc.scope, KbDoc.team_status, KbDoc.title,
            KbDoc.path, KbDoc.size, KbDoc.tags_json, KbDoc.summary, KbDoc.source_file,
            KbDoc.chunk_count, KbDoc.status, KbDoc.created_at, KbDoc.updated_at,
        )
    )
    rows = db.scalars(stmt.order_by(KbDoc.updated_at.desc())).all()
    link_counts = dict(
        db.execute(
            select(KbLink.source_id, func.count()).group_by(KbLink.source_id)
        ).all()
    )
    return [_doc_out(d, link_counts.get(d.id, 0)) for d in rows]


class DocCreateIn(BaseModel):
    title: str
    folder: str = ""
    content: str = ""
    scope: str = "personal"


@router.post("/docs")
async def create_doc(
    body: DocCreateIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if body.scope == "team" and user.role != "admin":
        raise HTTPException(status_code=403, detail="写入团队库需要管理员权限")
    title = body.title.strip()[:120] or "未命名"
    folder = body.folder.strip().strip("/")
    path = f"{folder}/{title}.md" if folder else f"{title}.md"
    doc = KbDoc(
        owner_id=user.id, scope=body.scope, title=title, path=path, content=body.content
    )
    db.add(doc)
    db.flush()
    await _reindex_doc(db, doc)
    db.commit()
    return _doc_out(doc)


class DocUpdateIn(BaseModel):
    title: str | None = None
    content: str | None = None


@router.put("/docs/{doc_id}")
async def update_doc(
    doc_id: int,
    body: DocUpdateIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    d = db.get(KbDoc, doc_id)
    if d is None or (d.scope == "personal" and d.owner_id != user.id):
        raise HTTPException(status_code=404, detail="文档不存在")
    if d.scope == "team" and user.role != "admin" and d.owner_id != user.id:
        raise HTTPException(status_code=403, detail="仅创建者或管理员可编辑团队库文档")
    if body.title is not None and body.title.strip():
        d.title = body.title.strip()[:120]
        folder = d.path.rsplit("/", 1)[0] if "/" in d.path else ""
        d.path = f"{folder}/{d.title}.md" if folder else f"{d.title}.md"
    if body.content is not None:
        d.content = body.content
        await _reindex_doc(db, d)
    db.commit()
    return _doc_out(d)


@router.get("/docs/{doc_id}")
def get_doc(
    doc_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    d = db.get(KbDoc, doc_id)
    if d is None or (d.scope == "personal" and d.owner_id != user.id):
        raise HTTPException(status_code=404, detail="文档不存在")
    out = _doc_out(d)
    out["content"] = d.content
    # 双链关系：outgoing（引用了谁）/ incoming（被谁引用）
    out_links = db.scalars(select(KbLink).where(KbLink.source_id == d.id)).all()
    in_links = db.scalars(select(KbLink).where(KbLink.target_doc_id == d.id)).all()
    # 反链标题一次取齐，避免逐条 db.get 的 N+1
    src_titles = {
        d.id: d.title
        for d in db.scalars(
            select(KbDoc).where(KbDoc.id.in_([l.source_id for l in in_links]))
        ).all()
    } if in_links else {}
    out["links"] = {
        "outgoing": [
            {"title": l.target_title, "doc_id": l.target_doc_id} for l in out_links
        ],
        "incoming": [
            {"title": src_titles.get(l.source_id, "?"), "doc_id": l.source_id}
            for l in in_links
        ],
    }
    return out


@router.get("/docs/{doc_id}/file")
def download_source(
    doc_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """下载上传时的原文件。"""
    d = db.get(KbDoc, doc_id)
    # 个人库仅本人；team_status=approved 的文档已入团队库，全员可看原文件
    if d is None or (d.scope == "personal" and d.owner_id != user.id and d.team_status != "approved"):
        raise HTTPException(status_code=404, detail="文档不存在")
    if not d.source_file:
        raise HTTPException(status_code=404, detail="该文档没有保存原文件（早期上传或纯手写笔记）")
    path = UPLOAD_DIR / d.source_file
    if not path.exists():
        raise HTTPException(status_code=404, detail="原文件已丢失")
    orig_name = d.source_file.split("_", 2)[2] if d.source_file.count("_") >= 2 else d.title
    return FileResponse(path, filename=orig_name)


@router.delete("/docs/{doc_id}")
def delete_doc(
    doc_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    d = db.get(KbDoc, doc_id)
    if d is None or (d.scope == "personal" and d.owner_id != user.id):
        raise HTTPException(status_code=404, detail="文档不存在")
    if d.scope == "team" and user.role != "admin":
        raise HTTPException(status_code=403, detail="删除团队库文档需要管理员权限")
    db.query(KbChunk).where(KbChunk.doc_id == d.id).delete()
    db.query(KbLink).where((KbLink.source_id == d.id) | (KbLink.target_doc_id == d.id)).delete()
    if d.source_file:
        try:
            (UPLOAD_DIR / d.source_file).unlink(missing_ok=True)
        except OSError:
            pass
    db.delete(d)
    db.commit()
    return {"ok": True}


# ---------- 知识管理工作流（收件箱 → 原始资料 → 来源台账 → 知识库） ----------

WORKFLOW_FOLDERS = [
    "收件箱",
    "原始资料/文章", "原始资料/论文报告", "原始资料/书籍摘录", "原始资料/视频转录",
    "原始资料/会议记录", "原始资料/灵感摘录", "原始资料/数据表格",
    "来源台账",
    "知识库/概念", "知识库/常见问题", "知识库/综合分析", "知识库/项目", "知识库/主题",
]

INGEST_CATEGORIES = ["文章", "论文报告", "书籍摘录", "视频转录", "会议记录", "灵感摘录", "数据表格"]


@router.post("/init-workflow")
def init_workflow(
    body: dict,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """初始化标准知识管理工作流目录（中文命名，幂等）。"""
    scope = body.get("scope") if body.get("scope") in ("personal", "team") else "personal"
    if scope == "team" and user.role != "admin":
        raise HTTPException(status_code=403, detail="团队库需要管理员初始化")

    created_folders = 0
    for path in WORKFLOW_FOLDERS:
        exists = db.scalar(
            select(KbFolder).where(KbFolder.path == path, _folder_scope_filter(scope, user))
        )
        if not exists:
            db.add(KbFolder(owner_id=user.id, scope=scope, path=path, sort=0))
            created_folders += 1

    # 索引 / 维护日志 / 来源登记（不存在才创建）
    seeds = [
        ("索引", "索引", "# 知识库索引\n\n> 知识库首页。重要入口在这里维护，每次整理后更新。\n\n## 结构导览\n- [[来源登记]] —— 所有资料的来源台账\n- [[维护日志]] —— 每次整理与治理的记录\n- 知识库/ —— 整理后的可复用知识（概念 · 常见问题 · 综合分析 · 项目 · 主题）\n- 原始资料/ —— 按来源形态存放的原始证据\n- 收件箱/ —— 新资料先进这里，等待整理\n\n## 重要页面\n\n（待整理后补充，用 [[页面名]] 链接）\n"),
        ("维护日志", "维护日志", "# 维护日志\n\n| 日期 | 动作 | 对象 | 结果 |\n| --- | --- | --- | --- |\n"),
        ("来源台账/来源登记", "来源登记", "# 来源登记\n\n> 每条资料的来源台账：来源、状态、可信度、是否已编译。\n\n| 日期 | 标题 | 来源形态 | 来源/链接 | 状态 | 已编译 |\n| --- | --- | --- | --- | --- | --- |\n"),
    ]
    created_docs = 0
    for path, title, content in seeds:
        doc_scope = (
            or_(KbDoc.scope == "team", KbDoc.team_status == "approved")
            if scope == "team"
            else and_(KbDoc.scope == "personal", KbDoc.owner_id == user.id)
        )
        exists = db.scalar(select(KbDoc).where(KbDoc.path == f"{path}.md", doc_scope))
        if not exists:
            db.add(
                KbDoc(owner_id=user.id, scope=scope, title=title,
                      path=f"{path}.md", content=content,
                      size=len(content.encode("utf-8")))
            )
            created_docs += 1

    db.commit()
    return {"ok": True, "created_folders": created_folders, "created_docs": created_docs}


@router.post("/ingest")
async def ingest_inbox(
    body: dict,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """一键整理收件箱：AI 基于当前文件夹树动态归类 → 移动 → 登记来源台账 → 高价值内容编译为知识页。
    归类不写死：AI 从现有文件夹中选最合适的目标，也可在现有文件夹下建议新子层级（如 原始资料/湖北/公众号）。"""
    from datetime import date

    from ..kb_service import _kb_llm

    ZONES = ["原始资料", "知识库", "收件箱", "来源台账"]

    scope = body.get("scope") if body.get("scope") in ("personal", "team") else "personal"
    doc_scope = (
        or_(KbDoc.scope == "team", KbDoc.team_status == "approved")
        if scope == "team"
        else and_(KbDoc.scope == "personal", KbDoc.owner_id == user.id)
    )
    inbox_docs = db.scalars(
        select(KbDoc).where(
            or_(KbDoc.path == "收件箱", KbDoc.path.like("收件箱/%")), doc_scope
        ).order_by(KbDoc.id)
    ).all()
    inbox_docs = [d for d in inbox_docs if d.path != "收件箱.md"]  # 排除同名笔记
    if not inbox_docs:
        return {"processed": 0, "report": []}

    # 当前库完整文件夹树（供 AI 动态归类，目录结构变了归类随之变化）
    all_paths = {p for p in db.scalars(select(KbDoc.path).where(doc_scope)) if p}
    folder_set: set[str] = set()
    for p in all_paths:
        parts = p.split("/")
        for i in range(1, len(parts)):
            folder_set.add("/".join(parts[:i]))
    for f in db.scalars(select(KbFolder.path).where(_folder_scope_filter(scope, user))):
        folder_set.add(f)
    folder_tree = "\n".join(sorted(folder_set)) or "（空）"

    # 台账文档（不存在则创建）
    register = db.scalar(select(KbDoc).where(KbDoc.path == "来源台账/来源登记.md", doc_scope))
    if register is None:
        register = KbDoc(
            owner_id=user.id, scope=scope, title="来源登记",
            path="来源台账/来源登记.md",
            content="# 来源登记\n\n| 日期 | 标题 | 归档位置 | 来源/链接 | 状态 | 已编译 |\n| --- | --- | --- | --- | --- | --- |\n",
        )
        db.add(register)
        db.flush()

    today = date.today().isoformat()
    report = []
    for d in inbox_docs:
        # AI 动态归类：从现有文件夹中选目标，或建议现有文件夹下的新子层级
        try:
            raw = await _kb_llm(
                db,
                "你是知识库管理员。当前知识库文件夹结构如下：\n" + folder_tree +
                "\n\n任务：为资料选择归档文件夹并评估价值，仅返回 JSON："
                '{"target":"归档文件夹路径","worth_compiling":true|false,'
                '"source_desc":"来源一句话（从内容推断）",'
                '"compile_note":"若值得编译，给出知识页主题（10字内）；否则空字符串"}。\n'
                "规则：target 必须以「原始资料」开头（新资料一律归原始资料）；"
                "优先复用上面列出的现有文件夹；确无合适的，可在现有文件夹下扩展新子层级"
                "（如按 地区/来源 细分：原始资料/湖北/公众号）；允许新建子层级但不得超过「原始资料」下两层。"
                "worth_compiling 判断标准：内容包含可反复复用的结论/方法/口径，而非一次性事务记录。",
                f"标题：{d.title}\n摘要线索：{(d.summary or '')[:100]}\n\n{d.content[:3000]}",
            )
            m = re.search(r"\{.*\}", raw, re.S)
            parsed = json.loads(m.group(0)) if m else {}
        except Exception:
            parsed = {}

        target = str(parsed.get("target", "")).strip().strip("/")
        top = target.split("/")[0]
        depth_ok = 1 < len(target.split("/")) <= 3
        if not target or top != "原始资料" or not depth_ok:
            target = "原始资料/文章"  # 兜底：结构异常时归默认文件夹
        worth = bool(parsed.get("worth_compiling"))
        source_desc = str(parsed.get("source_desc", "未注明来源"))[:60]
        compile_note = str(parsed.get("compile_note", ""))[:20]

        # 1) 移动并规范命名：<目标>/YYYY-MM-DD-标题.md（目标文件夹随聚合自动出现，无需预建）
        new_path = f"{target}/{today}-{d.title}.md"
        d.path = new_path

        # 2) 登记
        compiled_flag = "是" if worth else "否"
        register.content += f"| {today} | [[{d.title}]] | {target} | {source_desc} | new | {compiled_flag} |\n"
        register.size = len(register.content.encode("utf-8"))

        # 3) 高价值 → 编译知识页（知识库分区下，双链回指原文）
        compiled_doc = None
        if worth and compile_note:
            page_title = f"{compile_note}（{d.title}）"[:60]
            try:
                raw2 = await _kb_llm(
                    db,
                    "你是知识编译官。基于资料提炼一页可复用知识（Markdown）："
                    "# <标题>\n## 核心结论（3条以内，可直接执行/引用）\n## 关键细节\n## 适用场景\n## 来源\n"
                    f"来源区写：- 原始资料：[[{d.title}]]。正文开头不要寒暄。",
                    f"标题：{d.title}\n\n{d.content[:4000]}",
                )
                page_content = raw2.strip()[:6000]
                page_content = re.sub(r"^#.*\n", f"# {page_title}\n", page_content, count=1)
                compiled_doc = KbDoc(
                    owner_id=user.id, scope=scope, title=page_title,
                    path=f"知识库/综合分析/{page_title}.md", content=page_content,
                )
                db.add(compiled_doc)
                db.flush()
                await _reindex_doc(db, compiled_doc)
            except Exception:
                compiled_doc = None

        await _reindex_doc(db, d)
        report.append({
            "title": d.title,
            "category": target,
            "moved_to": new_path,
            "worth_compiling": worth,
            "compiled_page": compiled_doc.title if compiled_doc else None,
        })

    # 维护日志追加
    log = db.scalar(select(KbDoc).where(KbDoc.path == "维护日志.md", doc_scope))
    if log:
        log.content += f"| {today} | 整理收件箱 | {len(inbox_docs)} 篇 | AI 动态归类并登记，编译 {sum(1 for r in report if r['compiled_page'])} 篇 |\n"
        log.size = len(log.content.encode("utf-8"))

    db.commit()
    return {"processed": len(inbox_docs), "report": report}


@router.post("/search")
async def search(
    body: dict,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """知识库检索（前端预览用；对话内检索由 chat 流程内部调用 kb_service）。"""
    from ..kb_service import search_kb

    query = str(body.get("query", "")).strip()
    if not query:
        raise HTTPException(status_code=400, detail="query 不能为空")
    scope = body.get("scope") or "all"
    top_k = min(int(body.get("top_k", 5)), 10)
    return await search_kb(db, user.id, query, scope, top_k)


# ---------- 文件与文件夹管理 ----------

class FolderIn(BaseModel):
    path: str
    scope: str = "personal"


@router.get("/folders")
def list_folders(
    scope: str = "personal",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """当前库的文件夹（含空文件夹占位），按 sort 与路径排序。"""
    rows = db.scalars(
        select(KbFolder)
        .where(_folder_scope_filter(scope if scope in ("personal", "team") else "personal", user))
        .order_by(KbFolder.sort, KbFolder.path)
    ).all()
    return [
        {"id": f.id, "path": f.path, "scope": f.scope, "sort": f.sort}
        for f in rows
    ]


@router.post("/folders")
def create_folder(
    body: FolderIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    scope = body.scope if body.scope in ("personal", "team") else "personal"
    if scope == "team" and user.role != "admin":
        raise HTTPException(status_code=403, detail="团队文件夹需要管理员创建")
    path = body.path.strip().strip("/")
    if not path:
        raise HTTPException(status_code=400, detail="文件夹路径不合法")
    exists = db.scalar(
        select(KbFolder).where(KbFolder.path == path, _folder_scope_filter(scope, user))
    )
    if not exists:
        max_sort = db.scalar(
            select(func.max(KbFolder.sort)).where(_folder_scope_filter(scope, user))
        )
        db.add(KbFolder(owner_id=user.id, scope=scope, path=path, sort=(max_sort or 0) + 1))
        db.commit()
    return {"ok": True, "path": path}


class FolderRenameIn(BaseModel):
    old_path: str
    new_path: str
    scope: str = "personal"


def _guard_skeleton(*paths: str) -> None:
    """只锁定四个一级分区本身（收件箱/原始资料/知识库/来源台账）；其下子层级任由用户增删改移。"""
    locked = {"收件箱", "原始资料", "知识库", "来源台账"}
    for p in paths:
        if p.strip("/") in locked:
            raise HTTPException(
                status_code=403,
                detail=f"「{p}」是知识工作流的固定分区，不可修改；分区内的子文件夹可自由整理",
            )


@router.patch("/folders")
def rename_folder(
    body: FolderRenameIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """重命名/移动文件夹（new_path 可含新的父层级），同步移动同库下前缀匹配的文档与子文件夹。"""
    scope = body.scope if body.scope in ("personal", "team") else "personal"
    old, new = body.old_path.strip("/"), body.new_path.strip("/")
    if not old or not new or old == new:
        raise HTTPException(status_code=400, detail="路径不合法")
    _guard_skeleton(old, new)

    doc_scope = (
        or_(KbDoc.scope == "team", KbDoc.team_status == "approved")
        if scope == "team"
        else and_(KbDoc.scope == "personal", KbDoc.owner_id == user.id)
    )
    docs = db.scalars(
        select(KbDoc).where(
            or_(KbDoc.path == old, KbDoc.path.like(f"{old}/%")), doc_scope
        )
    ).all()
    for d in docs:
        d.path = new + d.path[len(old):]

    sub_folders = db.scalars(
        select(KbFolder).where(
            or_(KbFolder.path == old, KbFolder.path.like(f"{old}/%")),
            _folder_scope_filter(scope, user),
        )
    ).all()
    exact = None
    for f in sub_folders:
        if f.path == old:
            exact = f
        else:
            f.path = new + f.path[len(old):]
    if exact:
        exact.path = new
    else:
        db.add(KbFolder(owner_id=user.id, scope=scope, path=new, sort=0))
    db.commit()
    return {"ok": True, "moved": len(docs)}


class FolderDeleteIn(BaseModel):
    path: str
    scope: str = "personal"


@router.delete("/folders")
def delete_folder(
    body: FolderDeleteIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """删除空文件夹（内有文档/子文件夹时拒绝）。"""
    scope = body.scope if body.scope in ("personal", "team") else "personal"
    path = body.path.strip("/")
    _guard_skeleton(path)
    doc_scope = (
        or_(KbDoc.scope == "team", KbDoc.team_status == "approved")
        if scope == "team"
        else and_(KbDoc.scope == "personal", KbDoc.owner_id == user.id)
    )
    has_docs = db.scalar(
        select(func.count()).select_from(KbDoc).where(
            or_(KbDoc.path == path, KbDoc.path.like(f"{path}/%")), doc_scope
        )
    )
    if has_docs:
        raise HTTPException(status_code=400, detail="文件夹内还有文档，请先移出或删除")
    has_sub = db.scalar(
        select(func.count()).select_from(KbFolder).where(
            KbFolder.path.like(f"{path}/%"), _folder_scope_filter(scope, user)
        )
    )
    if has_sub:
        raise HTTPException(status_code=400, detail="文件夹内还有子文件夹")
    targets = db.scalars(
        select(KbFolder).where(KbFolder.path == path, _folder_scope_filter(scope, user))
    ).all()
    if not targets:
        raise HTTPException(status_code=404, detail="文件夹不存在")
    for f in targets:
        db.delete(f)
    db.commit()
    return {"ok": True}


class FolderSortIn(BaseModel):
    path: str
    direction: str  # up | down
    scope: str = "personal"


@router.post("/folders/sort")
def sort_folder(
    body: FolderSortIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """同级文件夹上移/下移。"""
    scope = body.scope if body.scope in ("personal", "team") else "personal"
    rows = db.scalars(
        select(KbFolder)
        .where(_folder_scope_filter(scope, user))
        .order_by(KbFolder.sort, KbFolder.path)
    ).all()
    target = body.path.strip("/")
    same_level = [f for f in rows if f.path.count("/") == target.count("/")]
    idx = next((i for i, f in enumerate(same_level) if f.path == target), None)
    if idx is None:
        raise HTTPException(status_code=404, detail="文件夹不存在")
    swap_with = idx - 1 if body.direction == "up" else idx + 1
    if 0 <= swap_with < len(same_level):
        same_level[idx], same_level[swap_with] = same_level[swap_with], same_level[idx]
        for i, f in enumerate(same_level):
            f.sort = i + 1
        db.commit()
    return {"ok": True}


class MoveDocIn(BaseModel):
    folder: str  # 目标文件夹（空=根目录）


@router.put("/docs/{doc_id}/move")
def move_doc(
    doc_id: int,
    body: MoveDocIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    d = _get_editable_doc(doc_id, user, db)
    folder = body.folder.strip().strip("/")
    d.path = f"{folder}/{d.title}.md" if folder else f"{d.title}.md"
    db.commit()
    return _doc_out(d)


def _get_editable_doc(doc_id: int, user: User, db: Session) -> KbDoc:
    d = db.get(KbDoc, doc_id)
    if d is None or (d.scope == "personal" and d.owner_id != user.id):
        raise HTTPException(status_code=404, detail="文档不存在")
    if d.scope == "team" and user.role != "admin" and d.owner_id != user.id:
        raise HTTPException(status_code=403, detail="仅创建者或管理员可操作该文档")
    return d


@router.post("/docs/{doc_id}/personal")
async def toggle_personal(
    doc_id: int,
    body: dict,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """enabled=true 入个人库（索引、AI 可检索）；false 移出转草稿。"""
    d = _get_editable_doc(doc_id, user, db)
    enabled = bool(body.get("enabled", True))
    if d.scope == "team" and d.owner_id != user.id:
        raise HTTPException(status_code=403, detail="团队库文档由创建者管理个人副本")
    if enabled and d.scope == "team":
        d.scope = "personal"  # 管理员直传团队库的文档也可拉回个人使用
        d.team_status = "none" if d.team_status == "none" else d.team_status
    d.status = "ready" if enabled else "draft"
    await _reindex_doc(db, d)
    db.commit()
    return _doc_out(d)


@router.post("/docs/{doc_id}/reindex")
async def reindex_doc(
    doc_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    d = _get_editable_doc(doc_id, user, db)
    await _reindex_doc(db, d)
    db.commit()
    return _doc_out(d)


# ---------- 入团队库审核流 ----------

@router.post("/docs/{doc_id}/request-team")
async def request_team(
    doc_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """申请入团队库：AI 预审（查重/质量/敏感）→ 进入管理员审核队列。"""
    d = _get_editable_doc(doc_id, user, db)
    if d.team_status == "pending":
        raise HTTPException(status_code=409, detail="该文档已在审核中")
    if d.team_status == "approved":
        raise HTTPException(status_code=409, detail="该文档已在团队库中")
    if d.status != "ready":
        raise HTTPException(status_code=400, detail="请先将文档入个人库（解析）后再申请")

    ai_review = await ai_preliminary_review(db, d)
    review = KbReview(
        doc_id=d.id, requester_id=user.id,
        ai_review_json=json.dumps(ai_review, ensure_ascii=False),
    )
    d.team_status = "pending"
    db.add(review)
    db.commit()
    return {"ok": True, "review_id": review.id, "ai_review": ai_review}


@router.get("/reviews")
def list_reviews(
    status: str = "pending",
    _: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    stmt = select(KbReview).order_by(KbReview.id.desc()).limit(50)
    if status != "all":
        stmt = stmt.where(KbReview.status == status)
    rows = db.scalars(stmt).all()
    out = []
    for r in rows:
        doc = db.get(KbDoc, r.doc_id)
        if doc is None:
            continue
        requester = db.get(User, r.requester_id)
        out.append(
            {
                "id": r.id,
                "doc": _doc_out(doc),
                "requester": requester.name if requester else "?",
                "ai_review": r.ai_review,
                "status": r.status,
                "reason": r.reason,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
        )
    return out


class ReviewDecisionIn(BaseModel):
    action: str  # approve | reject
    reason: str = ""


@router.post("/reviews/{review_id}/decide")
def decide_review(
    review_id: int,
    body: ReviewDecisionIn,
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    r = db.get(KbReview, review_id)
    if r is None or r.status != "pending":
        raise HTTPException(status_code=404, detail="审核单不存在或已处理")
    doc = db.get(KbDoc, r.doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="文档已被删除")
    if body.action == "approve":
        r.status = "approved"
        doc.team_status = "approved"
    elif body.action == "reject":
        if not body.reason.strip():
            raise HTTPException(status_code=400, detail="驳回需填写理由")
        r.status = "rejected"
        r.reason = body.reason.strip()
        doc.team_status = "rejected"
    else:
        raise HTTPException(status_code=400, detail="action 不合法")
    r.reviewer_id = admin.id
    db.commit()
    return {"ok": True}


@router.get("/graph")
def graph(
    scope: str = "all",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """图谱数据：节点=文档，边=双链。前端力导向渲染（限 500 文档防爆）。"""
    stmt = (
        select(KbDoc)
        .where(KbDoc.status == "ready")
        .where(_visible_filter(user))
        .options(
            load_only(
                KbDoc.id, KbDoc.title, KbDoc.scope, KbDoc.chunk_count,
                KbDoc.owner_id, KbDoc.team_status,
            )
        )
    )
    if scope == "personal":
        stmt = stmt.where(KbDoc.scope == "personal", KbDoc.owner_id == user.id)
    elif scope == "team":
        stmt = stmt.where(or_(KbDoc.scope == "team", KbDoc.team_status == "approved"))
    docs = db.scalars(stmt.limit(500)).all()
    id_set = {d.id for d in docs}
    links = db.scalars(
        select(KbLink).where(KbLink.source_id.in_(id_set))
    ).all()
    return {
        "nodes": [
            {"id": d.id, "title": d.title, "scope": d.scope, "chunk_count": d.chunk_count}
            for d in docs
        ],
        "edges": [
            {"source": l.source_id, "target": l.target_doc_id, "title": l.target_title}
            for l in links
        ],
    }


# ---------------------------------------------------------------- 知识库管家
# 供对话中的 AI 指令调用：把「当前库结构」喂给模型、执行模型给出的文件操作。


def kb_context_text(db: Session, user: User, scope: str = "personal") -> str:
    """当前库的文件夹树 + 文档路径清单（作为管家的操作上下文）。"""
    doc_scope = (
        or_(KbDoc.scope == "team", KbDoc.team_status == "approved")
        if scope == "team"
        else and_(KbDoc.scope == "personal", KbDoc.owner_id == user.id)
    )
    folders = sorted(
        f for f in db.scalars(select(KbFolder.path).where(_folder_scope_filter(scope, user)))
    )
    docs = db.scalars(select(KbDoc).where(doc_scope).order_by(KbDoc.updated_at.desc()).limit(300)).all()
    doc_lines = [f"- {d.path}（标题：{d.title}）" for d in docs]
    return (
        "【文件夹树】\n" + ("\n".join(folders) or "（空）")
        + "\n\n【文档清单】\n" + ("\n".join(doc_lines) or "（空）")
    )


def _find_doc(db: Session, user: User, scope: str, key: str) -> KbDoc | None:
    """按标题或路径片段定位文档（管家指令中的模糊指代）。"""
    doc_scope = (
        or_(KbDoc.scope == "team", KbDoc.team_status == "approved")
        if scope == "team"
        else and_(KbDoc.scope == "personal", KbDoc.owner_id == user.id)
    )
    like = f"%{key.strip()}%"
    return db.scalar(
        select(KbDoc)
        .where(doc_scope, or_(KbDoc.title.like(like), KbDoc.path.like(like)))
        .order_by(KbDoc.updated_at.desc())
        .limit(1)
    )


async def apply_kb_op(db: Session, user: User, op: dict, scope: str = "personal") -> str:
    """执行管家给出的单个操作，返回结果描述（出错也返回文案，不中断会话）。"""
    action = str(op.get("action", "")).strip()
    try:
        if action == "create_folder":
            path = str(op.get("path", "")).strip().strip("/")
            if not path:
                return "❌ create_folder 缺少 path"
            _guard_skeleton(path)
            exists = db.scalar(
                select(KbFolder).where(KbFolder.path == path, _folder_scope_filter(scope, user))
            )
            if not exists:
                max_sort = db.scalar(
                    select(func.max(KbFolder.sort)).where(_folder_scope_filter(scope, user))
                )
                db.add(KbFolder(owner_id=user.id, scope=scope, path=path, sort=(max_sort or 0) + 1))
                db.commit()
                return f"✅ 新建文件夹 {path}"
            return f"⏭️ 文件夹已存在：{path}"

        if action == "rename_folder":
            old, new = str(op.get("old", "")).strip("/"), str(op.get("new", "")).strip("/")
            if not old or not new or old == new:
                return "❌ rename_folder 缺少 old/new"
            _guard_skeleton(old, new)
            doc_scope = (
                or_(KbDoc.scope == "team", KbDoc.team_status == "approved")
                if scope == "team"
                else and_(KbDoc.scope == "personal", KbDoc.owner_id == user.id)
            )
            docs = db.scalars(
                select(KbDoc).where(or_(KbDoc.path == old, KbDoc.path.like(f"{old}/%")), doc_scope)
            ).all()
            for d in docs:
                d.path = new + d.path[len(old):]
            subs = db.scalars(
                select(KbFolder).where(
                    or_(KbFolder.path == old, KbFolder.path.like(f"{old}/%")),
                    _folder_scope_filter(scope, user),
                )
            ).all()
            exact = next((f for f in subs if f.path == old), None)
            for f in subs:
                if f.path != old:
                    f.path = new + f.path[len(old):]
            if exact:
                exact.path = new
            else:
                db.add(KbFolder(owner_id=user.id, scope=scope, path=new, sort=0))
            db.commit()
            return f"✅ 文件夹 {old} → {new}（同步移动 {len(docs)} 篇文档）"

        if action == "delete_folder":
            path = str(op.get("path", "")).strip("/")
            if not path:
                return "❌ delete_folder 缺少 path"
            _guard_skeleton(path)
            doc_scope = (
                or_(KbDoc.scope == "team", KbDoc.team_status == "approved")
                if scope == "team"
                else and_(KbDoc.scope == "personal", KbDoc.owner_id == user.id)
            )
            has_docs = db.scalar(
                select(func.count()).select_from(KbDoc).where(
                    or_(KbDoc.path == path, KbDoc.path.like(f"{path}/%")), doc_scope
                )
            )
            if has_docs:
                return f"❌ {path} 内还有 {has_docs} 篇文档，请先移出或删除"
            subs = db.scalars(
                select(KbFolder).where(KbFolder.path.like(f"{path}/%"), _folder_scope_filter(scope, user))
            ).all()
            if subs:
                return f"❌ {path} 内还有子文件夹（{subs[0].path} 等），请先删除子级"
            targets = db.scalars(
                select(KbFolder).where(KbFolder.path == path, _folder_scope_filter(scope, user))
            ).all()
            if not targets:
                return f"❌ 文件夹不存在：{path}"
            for f in targets:
                db.delete(f)
            db.commit()
            return f"✅ 删除文件夹 {path}"

        if action == "move_doc":
            key, to = str(op.get("title", "")).strip(), str(op.get("to", "")).strip("/")
            if not key:
                return "❌ move_doc 缺少 title"
            d = _find_doc(db, user, scope, key)
            if d is None:
                return f"❌ 未找到文档：{key}"
            d.path = f"{to}/{d.title}.md" if to else f"{d.title}.md"
            await _reindex_doc(db, d)
            db.commit()
            return f"✅ 《{d.title}》移动到 {to or '根目录'}"

        if action == "rename_doc":
            key = str(op.get("title", "")).strip()
            new_title = str(op.get("new_title", "")).strip()
            if not key or not new_title:
                return "❌ rename_doc 缺少 title/new_title"
            d = _find_doc(db, user, scope, key)
            if d is None:
                return f"❌ 未找到文档：{key}"
            old_path = d.path
            d.title = new_title
            d.path = "/".join(old_path.split("/")[:-1] + [f"{new_title}.md"])
            await _reindex_doc(db, d)
            db.commit()
            return f"✅ 重命名《{key}》→《{new_title}》"

        if action == "delete_doc":
            key = str(op.get("title", "")).strip()
            if not key:
                return "❌ delete_doc 缺少 title"
            d = _find_doc(db, user, scope, key)
            if d is None:
                return f"❌ 未找到文档：{key}"
            title = d.title
            db.query(KbChunk).where(KbChunk.doc_id == d.id).delete()
            db.query(KbLink).where(or_(KbLink.source_id == d.id, KbLink.target_doc_id == d.id)).delete()
            db.delete(d)
            db.commit()
            return f"✅ 删除文档《{title}》"

        if action == "create_doc":
            title = str(op.get("title", "")).strip() or "未命名"
            folder = str(op.get("folder", "")).strip("/")
            content = str(op.get("content", "")).strip() or f"# {title}\n"
            path = f"{folder}/{title}.md" if folder else f"{title}.md"
            doc = await _ingest(db, user, scope, title, path, content)
            db.commit()
            return f"✅ 新建文档《{title}》于 {folder or '根目录'}"

        if action == "organize_inbox":
            # 复用「整理收件箱」端点逻辑：AI 动态归类 → 登记台账 → 编译知识页
            res = await ingest_inbox(body={"scope": scope}, user=user, db=db)
            lines = [
                f"《{r['title']}》→ {r['category']}"
                + (f"，编译知识页《{r['compiled_page']}》" if r.get("compiled_page") else "")
                for r in res.get("report", [])
            ]
            summary = "\n".join(lines) if lines else "收件箱为空，无需整理"
            return f"✅ 整理收件箱完成：处理 {res.get('processed', 0)} 篇\n{summary}"

        return f"❌ 未知操作：{action}"
    except HTTPException as e:
        db.rollback()
        return f"❌ {e.detail}"
    except Exception as e:
        db.rollback()
        return f"❌ 执行失败：{e}"
