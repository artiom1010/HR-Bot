import json
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any

from sqlalchemy.orm import Session
from jinja2 import Environment, FileSystemLoader, select_autoescape
from weasyprint import HTML, CSS

from _database import engine, ProcessedData, Answer

TEMPLATES_DIR = Path("templates")
TEMPLATE_HTML = TEMPLATES_DIR / "resume.html"
OUT_DIR       = Path("files")


def _row_to_context(row: ProcessedData) -> Dict[str, Any]:
    """
    Преобразует запись из БД в словарь для шаблона.
    """
    ignore = {"id", "cv_naming", "order_id", "created_at", "experiences_json"}
    context = {
        col.name: getattr(row, col.name)
        for col in row.__table__.columns
        if col.name not in ignore
    }

    if getattr(row, "experiences_json", None):
        try:
            context["experiences"] = json.loads(row.experiences_json)
        except json.JSONDecodeError:
            context["experiences"] = []
    else:
        context["experiences"] = [{
            "job_title_experience": getattr(row, "job_title_experience", None),
            "employment_period":   getattr(row, "employment_period", None),
            "company_name":        getattr(row, "company_name", None),
            "job_responsibilities": (
                [row.job_responsibilities] if getattr(row, "job_responsibilities", None) else []
            )
        }]
    for exp in context["experiences"]:
        jr = exp.get("job_responsibilities")
        if isinstance(jr, str):
            exp["job_responsibilities"] = [jr]
        elif jr is None:
            exp["job_responsibilities"] = []
    return context


from labels import RESUME_LABELS
def _render_html(context: dict) -> str:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "xml"])
    )
    template = env.get_template(TEMPLATE_HTML.name)
    
    # Получаем язык из контекста, по умолчанию русский
    lang = context.get("resume_language", "ru")
    labels = RESUME_LABELS.get(lang, RESUME_LABELS["ru"])
    
    return template.render(labels=labels, **context)


def build_resume(order_id: int, make_pdf: bool = True, make_docx: bool = True) -> Optional[Dict[str, str]]:
    """
    Генерирует HTML + (опционально) PDF и DOCX.
    Возвращает словарь путей вида {'html': ..., 'pdf': ..., 'docx': ...} или None, если записи нет.
    """
    with Session(engine) as s:
        row = s.query(ProcessedData).filter_by(order_id=order_id).first()
        if row is None:
            return None
    
        user_id = row.order.user_id
        lang_answer = s.query(Answer).filter_by(
            user_id=user_id, 
            question_key="resume_language"
        ).first()

        resume_lang = "ru"
        if lang_answer and lang_answer.answer:
            code = lang_answer.answer.split()[0].lower()  # "RU" → "ru"
            if code in ("ru", "en", "ro"):
                resume_lang = code

        # 2) Готовим контекст и HTML
        context  = _row_to_context(row)
        context["resume_language"] = resume_lang
        cv_naming = row.cv_naming

    html_out = _render_html(context)

    # 3) Подготавливаем имена и директории
    OUT_DIR.mkdir(exist_ok=True)

    base_name = (cv_naming or f"resume_{order_id}").rsplit(".", 1)[0]  # ← используем переменную
    
    html_path = OUT_DIR / f"{base_name}.html"
    pdf_path  = OUT_DIR / f"{base_name}.pdf"
    docx_path = OUT_DIR / f"{base_name}.docx"

    # 4) Сохраняем HTML рядом (удобно для отладки)
    html_path.write_text(html_out, encoding="utf-8")

    result = {"html": str(html_path)}

    # 5) Делаем PDF через WeasyPrint
    if make_pdf:
        css_file = TEMPLATES_DIR / "styles.css"
        stylesheets = [CSS(filename=str(css_file.resolve()))] if css_file.exists() else None
        # base_url важен для относительных путей в CSS (шрифты/картинки)
        HTML(string=html_out, base_url=str(TEMPLATES_DIR.resolve())).write_pdf(
            target=str(pdf_path),
            stylesheets=stylesheets
        )
        result["pdf"] = str(pdf_path)

    # 6) Делаем DOCX через Pandoc
    if make_docx:
        if shutil.which("pandoc") is None:
            raise RuntimeError("Pandoc не найден в PATH. Установи pandoc и повтори.")

        subprocess.run(
            [
                "pandoc", str(html_path), "-t", "docx", "-o", str(docx_path),
                "--metadata=title:", "--reference-doc", str((TEMPLATES_DIR / "reference.docx").resolve())
            ], check=True
        )
        result["docx"] = str(docx_path)
    return result



def generate_resume(order_id: int, to_pdf: bool = True) -> Optional[str]:
    paths = build_resume(order_id, make_pdf=True, make_docx=True)
    if not paths:
        return None
    if to_pdf:
        return paths.get("pdf")
        # return paths.get("pdf") or paths.get("html")  # если PDF вдруг не собрался
    else:
        return paths.get("docx")