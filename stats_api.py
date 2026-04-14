from fastapi import APIRouter, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timedelta

from _database import engine, User, Order, PaynetStatus, StepLog

router = APIRouter()

STEP_LABELS = {
    "main_language": "🌐 Язык общения",
    "privacy_agreement": "📋 Согласие с политиками",
    "resume_language": "📄 Язык резюме",
    "target_position": "🧭 Желаемая должность",
    "job_description": "🔍 Описание вакансии",
    "summary_industry": "Сфера работы",
    "summary_experience": "Лет опыта",
    "summary_tasks": "Лучшие задачи",
    "summary_values": "Хард-скиллы",
    "experience_title": "Должность",
    "experience_company": "Компания",
    "experience_period": "Период работы",
    "experience_tasks": "Задачи",
    "experience_positioning": "Позиционирование",
    "experience_best_qualities": "Качества",
    "experience_achievements": "Достижения",
    "edu_institution": "Учебное заведение",
    "edu_specialty": "Специальность",
    "edu_period": "Годы обучения",
    "course_item": "Курс/сертификат",
    "skills_tools": "Программы и инструменты",
    "skills_langs": "Языки",
    "full_name": "Имя и фамилия",
    "contact_phone": "Телефон",
    "contact_email": "Email",
    "contact_links": "Ссылки",
}

@router.get("/stats")
def get_statistics(
    date_from: str = Query(None),
    date_to: str = Query(None)
):
    # Парсим даты
    if date_from:
        dt_from = datetime.fromisoformat(date_from)
    else:
        dt_from = datetime(2020, 1, 1)
    
    if date_to:
        dt_to = datetime.fromisoformat(date_to) + timedelta(days=1)
    else:
        dt_to = datetime.utcnow()
    
    with Session(engine) as s:
        # Общее количество пользователей
        total_users = s.query(func.count(User.id)).scalar()
        
        # Общее количество оплативших пользователей
        total_paid_users = s.query(func.count(func.distinct(Order.user_id))).filter(
            Order.paynet_status == PaynetStatus.paid
        ).scalar()

        # 1. Новые пользователи
        new_users = s.query(func.count(User.id)).filter(
            User.created_at >= dt_from,
            User.created_at < dt_to
        ).scalar()
        
        # 2. Активные пользователи
        active_users = s.query(func.count(func.distinct(StepLog.user_id))).filter(
            StepLog.created_at >= dt_from,
            StepLog.created_at < dt_to
        ).scalar()
        
        # 3. Оплатившие
        paid_users = s.query(func.count(func.distinct(Order.user_id))).filter(
            Order.paynet_status == PaynetStatus.paid,
            Order.paid_at >= dt_from,
            Order.paid_at < dt_to
        ).scalar()
        
        # 4. Воронка по шагам
        funnel_raw = s.query(
            StepLog.step_key,
            func.count(func.distinct(StepLog.user_id))
        ).filter(
            StepLog.created_at >= dt_from,
            StepLog.created_at < dt_to
        ).group_by(StepLog.step_key).all()
        
        funnel = [
            {
                "step_key": step,
                "label": STEP_LABELS.get(step, step),
                "users": count
            }
            for step, count in funnel_raw
        ]
        
        # Сортируем по порядку
        step_order = list(STEP_LABELS.keys())
        funnel.sort(key=lambda x: step_order.index(x["step_key"]) if x["step_key"] in step_order else 999)

        # Добавляем оплату в конец воронки
        funnel.append({
            "step_key": "payment_done",
            "label": "💳 Оплата",
            "users": paid_users,
        })
    
    return {
        "period": {"from": date_from, "to": date_to},
        "total_users": total_users,
        "total_paid_users": total_paid_users,
        "new_users": new_users,
        "active_users": active_users,
        "paid_users": paid_users,
        "funnel": funnel
    }