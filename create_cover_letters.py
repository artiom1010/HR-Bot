from docx import Document
from docx.shared import Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH

TEMPLATES_DIR = "templates"


def add_paragraph(doc, text, bold=False, size=11, align=WD_ALIGN_PARAGRAPH.LEFT, color=None, space_before=0, space_after=6):
    p = doc.add_paragraph()
    p.alignment = align
    p.paragraph_format.space_before = Pt(space_before)
    p.paragraph_format.space_after = Pt(space_after)
    run = p.add_run(text)
    run.bold = bold
    run.font.size = Pt(size)
    if color:
        run.font.color.rgb = RGBColor(*color)
    return p


def create_ru():
    doc = Document()
    style = doc.styles['Normal']
    style.font.name = 'Arial'
    style.font.size = Pt(11)

    add_paragraph(doc, "[Ваше Имя Фамилия]", bold=True, size=13)
    add_paragraph(doc, "[Телефон] | [Email] | [LinkedIn / Город]", size=10, color=(100, 100, 100))
    add_paragraph(doc, "")
    add_paragraph(doc, "[Дата]", size=10)
    add_paragraph(doc, "")
    add_paragraph(doc, "Уважаемый(ая) [Имя HR-менеджера / команда отбора],", space_before=6)
    add_paragraph(doc, "")

    add_paragraph(doc,
        "Я хочу выразить свой интерес к позиции [Название должности] в компании [Название компании]. "
        "Имея опыт работы в [Сфера/Индустрия] и подтверждённые результаты в [ключевое достижение], "
        "я уверен(а), что смогу внести значительный вклад в вашу команду.")
    add_paragraph(doc, "")

    add_paragraph(doc,
        "На протяжении [количество лет] лет я работал(а) в [предыдущая компания / сфера], где успешно "
        "[ключевое достижение 1] и [ключевое достижение 2]. Мой опыт в [навык] позволяет мне эффективно "
        "решать задачи в быстро меняющейся среде.")
    add_paragraph(doc, "")

    add_paragraph(doc,
        "Меня привлекает [Название компании] благодаря [причина интереса к компании — культура, продукт, миссия]. "
        "Убеждён(а), что мои навыки в [ключевой навык] и мотивация к [цель] сделают меня ценным членом вашей команды.")
    add_paragraph(doc, "")

    add_paragraph(doc,
        "Буду рад(а) обсудить, как мой опыт соответствует потребностям вашей компании. "
        "Готов(а) пройти собеседование в удобное для вас время.")
    add_paragraph(doc, "")

    add_paragraph(doc, "С уважением,")
    add_paragraph(doc, "[Ваше Имя Фамилия]", bold=True)

    doc.save(f"{TEMPLATES_DIR}/ru_cover_letter.docx")
    print("✅ ru_cover_letter.docx создан")


def create_en():
    doc = Document()
    style = doc.styles['Normal']
    style.font.name = 'Arial'
    style.font.size = Pt(11)

    add_paragraph(doc, "[Your Full Name]", bold=True, size=13)
    add_paragraph(doc, "[Phone] | [Email] | [LinkedIn / City]", size=10, color=(100, 100, 100))
    add_paragraph(doc, "")
    add_paragraph(doc, "[Date]", size=10)
    add_paragraph(doc, "")
    add_paragraph(doc, "Dear [Hiring Manager's Name / Hiring Team],", space_before=6)
    add_paragraph(doc, "")

    add_paragraph(doc,
        "I am writing to express my interest in the [Job Title] position at [Company Name]. "
        "With a background in [Field/Industry] and a proven track record of [key achievement], "
        "I am confident in my ability to make a meaningful contribution to your team.")
    add_paragraph(doc, "")

    add_paragraph(doc,
        "Over the past [X] years, I have worked at [Previous Company / in the field], where I successfully "
        "[key achievement 1] and [key achievement 2]. My expertise in [skill] allows me to thrive "
        "in fast-paced, results-driven environments.")
    add_paragraph(doc, "")

    add_paragraph(doc,
        "I am particularly drawn to [Company Name] because of [reason — culture, product, mission]. "
        "I believe my skills in [key skill] and my passion for [goal] make me a strong fit for your team.")
    add_paragraph(doc, "")

    add_paragraph(doc,
        "I would welcome the opportunity to discuss how my background aligns with your needs. "
        "I am available for an interview at your earliest convenience.")
    add_paragraph(doc, "")

    add_paragraph(doc, "Sincerely,")
    add_paragraph(doc, "[Your Full Name]", bold=True)

    doc.save(f"{TEMPLATES_DIR}/en_cover_letter.docx")
    print("✅ en_cover_letter.docx created")


def create_ro():
    doc = Document()
    style = doc.styles['Normal']
    style.font.name = 'Arial'
    style.font.size = Pt(11)

    add_paragraph(doc, "[Numele și Prenumele]", bold=True, size=13)
    add_paragraph(doc, "[Telefon] | [Email] | [LinkedIn / Oraș]", size=10, color=(100, 100, 100))
    add_paragraph(doc, "")
    add_paragraph(doc, "[Data]", size=10)
    add_paragraph(doc, "")
    add_paragraph(doc, "Stimate(ă) [Numele managerului HR / Echipei de recrutare],", space_before=6)
    add_paragraph(doc, "")

    add_paragraph(doc,
        "Vă scriu pentru a-mi exprima interesul față de poziția de [Titlul postului] din cadrul companiei [Numele companiei]. "
        "Cu o experiență solidă în [Domeniu/Industrie] și rezultate demonstrate în [realizare cheie], "
        "sunt convins(ă) că pot aduce o contribuție valoroasă echipei dumneavoastră.")
    add_paragraph(doc, "")

    add_paragraph(doc,
        "În ultimii [X] ani, am activat în cadrul [Compania anterioară / domeniu], unde am reușit "
        "[realizare cheie 1] și [realizare cheie 2]. Expertiza mea în [competență] îmi permite să mă adaptez "
        "eficient în medii dinamice și orientate spre rezultate.")
    add_paragraph(doc, "")

    add_paragraph(doc,
        "Sunt atras(ă) în mod deosebit de [Numele companiei] datorită [motiv — cultură, produs, misiune]. "
        "Cred că abilitățile mele în [competență cheie] și motivația pentru [obiectiv] mă recomandă "
        "ca un candidat potrivit pentru echipa dumneavoastră.")
    add_paragraph(doc, "")

    add_paragraph(doc,
        "Aș aprecia oportunitatea de a discuta despre modul în care experiența mea corespunde nevoilor companiei. "
        "Sunt disponibil(ă) pentru un interviu la momentul convenabil pentru dumneavoastră.")
    add_paragraph(doc, "")

    add_paragraph(doc, "Cu stimă,")
    add_paragraph(doc, "[Numele și Prenumele]", bold=True)

    doc.save(f"{TEMPLATES_DIR}/ro_cover_letter.docx")
    print("✅ ro_cover_letter.docx creat")


if __name__ == "__main__":
    create_ru()
    create_en()
    create_ro()
