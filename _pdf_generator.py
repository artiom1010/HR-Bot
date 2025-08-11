# import json
# from pathlib import Path
# from typing import Optional

# from docxtpl import DocxTemplate
# from docx2pdf import convert
# from sqlalchemy.orm import Session

# from _database import engine, ProcessedData


# TEMPLATE_PATH = Path("files/resume_tmpl.docx")
# OUT_DIR       = Path("files")


# def _row_to_context(row: ProcessedData) -> dict:
#     ignore = {"id", "cv_naming", "order_id", "created_at", "experiences_json"}
#     context = {
#         col.name: getattr(row, col.name)
#         for col in row.__table__.columns
#         if col.name not in ignore
#     }
#     # ---------- добавляем список experiences ----------
#     if row.experiences_json:
#         try:
#             context["experiences"] = json.loads(row.experiences_json)
#         except json.JSONDecodeError:
#             context["experiences"] = []
#     else:
#         # fallback: соберём «одиночные» поля, чтобы ничего не ломалось
#         context["experiences"] = [{
#             "job_title_experience": row.job_title_experience,
#             "employment_period":   row.employment_period,
#             "company_name":        row.company_name,
#             "job_responsibilities": (
#                 [row.job_responsibilities] if row.job_responsibilities else []
#             )
#         }]
#     return context


# def generate_resume(order_id: int, to_pdf: bool = True) -> Optional[str]:
#     """
#     Вернёт путь к файлу или None, если данных нет.
#     """
#     with Session(engine) as s:
#         row = s.query(ProcessedData).filter_by(order_id=order_id).first()

#     if row is None:
#         return None

#     context = _row_to_context(row)
#     doc = DocxTemplate(TEMPLATE_PATH)
#     doc.render(context)

#     OUT_DIR.mkdir(exist_ok=True)

#     raw_name = row.cv_naming or f"resume_{order_id}"
#     filename = raw_name if raw_name.lower().endswith(".docx") else f"{raw_name}.docx"

#     out_path = OUT_DIR / filename
#     doc.save(out_path)

#     if to_pdf:
#         pdf_path = out_path.with_suffix(".pdf")   # то же имя, но .pdf
#         convert(str(out_path), str(pdf_path))     # <-- сама конверсия
#         return str(pdf_path)

#     return str(out_path)
