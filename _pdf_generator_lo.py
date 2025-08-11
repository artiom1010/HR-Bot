# import json, re, shutil, subprocess
# from pathlib import Path
# from typing import Optional
# from sqlalchemy.orm import Session
# from docxtpl import DocxTemplate

# from _database import engine, ProcessedData

# TEMPLATE_PATH = Path("files/resume_tmpl.docx")
# OUT_DIR       = Path("files")

# def _sanitize_filename(name: str) -> str:
#     name = re.sub(r'[\\/:*?"<>|\r\n\t]+', " ", name).strip()
#     return name[:120] or "resume"

# def _row_to_context(row: ProcessedData) -> dict:
#     ignore = {"id", "cv_naming", "order_id", "created_at", "experiences_json"}
#     ctx = {c.name: getattr(row, c.name)
#            for c in row.__table__.columns if c.name not in ignore}

#     if row.experiences_json:
#         try:
#             ctx["experiences"] = json.loads(row.experiences_json)
#         except json.JSONDecodeError:
#             ctx["experiences"] = []
#     else:
#         ctx["experiences"] = [{
#             "job_title_experience": getattr(row, "job_title_experience", ""),
#             "employment_period":   getattr(row, "employment_period", ""),
#             "company_name":        getattr(row, "company_name", ""),
#             "job_responsibilities": (
#                 [row.job_responsibilities] if getattr(row, "job_responsibilities", "") else []
#             ),
#         }]
#     return ctx

# def _libreoffice_available() -> bool:
#     return shutil.which("soffice") is not None

# def _to_pdf_via_libreoffice(docx_path: Path, pdf_path: Path) -> bool:
#     try:
#         subprocess.run(
#             ["soffice", "--headless", "--convert-to", "pdf",
#              "--outdir", str(pdf_path.parent), str(docx_path)],
#             check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
#         )
#         return pdf_path.exists() and pdf_path.stat().st_size > 0
#     except Exception:
#         return False

# def generate_resume(order_id: int, to_pdf: bool = True) -> Optional[str]:
#     # 1) данные
#     with Session(engine) as s:
#         row = s.query(ProcessedData).filter_by(order_id=order_id).first()
#     if not row:
#         return None

#     # 2) рендер DOCX
#     context = _row_to_context(row)
#     doc = DocxTemplate(TEMPLATE_PATH)
#     doc.render(context)

#     OUT_DIR.mkdir(parents=True, exist_ok=True)
#     base = _sanitize_filename(row.cv_naming or f"resume_{order_id}")
#     docx_path = (OUT_DIR / f"{base}.docx").resolve()
#     pdf_path  = (OUT_DIR / f"{base}.pdf").resolve()

#     doc.save(docx_path)

#     # 3) PDF нужен?
#     if not to_pdf:
#         return str(docx_path)
#     if not _libreoffice_available():
#         return str(docx_path)

#     # 4) конверт через LibreOffice
#     if pdf_path.exists():
#         try: pdf_path.unlink()
#         except OSError: pass
#     ok = _to_pdf_via_libreoffice(docx_path, pdf_path)
#     return str(pdf_path) if ok else str(docx_path)




# _pdf_generator_lo.py
import json, re, shutil, subprocess, logging, os
from pathlib import Path
from typing import Optional
from sqlalchemy.orm import Session
from docxtpl import DocxTemplate

from _database import engine, ProcessedData
import tempfile, shutil, subprocess, os, logging, glob

log = logging.getLogger("pdfgen.lo")

TEMPLATE_PATH = Path("files/resume_tmpl.docx")
OUT_DIR       = Path("files")

def _sanitize_filename(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', " ", name).strip()
    return name[:120] or "resume"

def _row_to_context(row: ProcessedData) -> dict:
    ignore = {"id", "cv_naming", "order_id", "created_at", "experiences_json"}
    ctx = {c.name: getattr(row, c.name)
           for c in row.__table__.columns if c.name not in ignore}
    if row.experiences_json:
        try:
            ctx["experiences"] = json.loads(row.experiences_json)
        except json.JSONDecodeError:
            ctx["experiences"] = []
    else:
        ctx["experiences"] = [{
            "job_title_experience": getattr(row, "job_title_experience", ""),
            "employment_period":   getattr(row, "employment_period", ""),
            "company_name":        getattr(row, "company_name", ""),
            "job_responsibilities": (
                [row.job_responsibilities] if getattr(row, "job_responsibilities", "") else []
            ),
        }]
    return ctx

def _libreoffice_available() -> bool:
    path = shutil.which("soffice")
    log.debug("LibreOffice soffice path: %r; ENV PATH: %s", path, os.getenv("PATH"))
    return path is not None



def _to_pdf_via_libreoffice(docx_path: Path, out_pdf: Path) -> bool:
    """
    Конвертирует DOCX во временную папку через LibreOffice с чистым профилем,
    затем переносит итоговый PDF в out_pdf.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_p = Path(tmpdir)
        # Чистый профиль (исключает странности от пользовательского профиля)
        env_profile = f"file://{tmpdir_p.as_posix()}/lo_profile"

        cmd = [
            shutil.which("soffice") or "soffice",
            "--headless",
            "--convert-to", "pdf:writer_pdf_Export",
            "--outdir", str(tmpdir_p),
            f"-env:UserInstallation={env_profile}",
            str(docx_path),
        ]
        log.info("[pdfgen.lo] CMD: %s", " ".join(cmd))

        # Нормальный локаль/кодировки
        env = os.environ.copy()
        env.setdefault("LANG", "en_US.UTF-8")

        try:
            res = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                env=env,
            )
            log.info("[pdfgen.lo] return=%s", res.returncode)
            if res.stdout:
                log.info("[pdfgen.lo] stdout: %s", res.stdout.decode(errors="ignore").strip())
            if res.stderr:
                log.info("[pdfgen.lo] stderr: %s", res.stderr.decode(errors="ignore").strip())
        except Exception as e:
            log.exception("[pdfgen.lo] run failed: %s", e)
            return False

        # Ищем созданный PDF во временной папке (имя может отличаться)
        candidates = sorted(glob.glob(str(tmpdir_p / "*.pdf")))
        log.info("[pdfgen.lo] tmp PDFs found: %s", candidates)
        if not candidates:
            return False

        # Берём первый (обычно он один)
        tmp_pdf = Path(candidates[0])
        try:
            if out_pdf.exists():
                out_pdf.unlink()
            shutil.move(str(tmp_pdf), str(out_pdf))
        except Exception as e:
            log.exception("[pdfgen.lo] move failed: %s", e)
            return False

        ok = out_pdf.exists() and out_pdf.stat().st_size > 0
        log.info("[pdfgen.lo] final PDF ok=%s size=%s path=%s",
                 ok, out_pdf.stat().st_size if out_pdf.exists() else 0, out_pdf)
        return ok


def generate_resume(order_id: int, to_pdf: bool = True) -> Optional[str]:
    with Session(engine) as s:
        row = s.query(ProcessedData).filter_by(order_id=order_id).first()
    if not row:
        log.error("No ProcessedData for order %s", order_id)
        return None

    context = _row_to_context(row)
    doc = DocxTemplate(TEMPLATE_PATH)
    doc.render(context)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    base = _sanitize_filename(row.cv_naming or f"resume_{order_id}")
    docx_path = (OUT_DIR / f"{base}.docx").resolve()
    pdf_path  = (OUT_DIR / f"{base}.pdf").resolve()

    # сохраняем DOCX всегда — это рабочая «подстраховка»
    doc.save(docx_path)
    log.info("DOCX saved: %s", docx_path)

    if not to_pdf:
        log.info("to_pdf=False → return DOCX")
        return str(docx_path)

    if not _libreoffice_available():
        log.warning("LibreOffice not available → return DOCX")
        return str(docx_path)

    # удалим старый файл перед конвертацией
    if pdf_path.exists():
        try: pdf_path.unlink()
        except OSError: pass    


    ok = _to_pdf_via_libreoffice(docx_path, pdf_path)
    if ok:
        log.info("Return PDF: %s", pdf_path)
        return str(pdf_path)
    else:
        log.warning("PDF convert failed → return DOCX")
        return str(docx_path)