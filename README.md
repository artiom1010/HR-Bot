# HR-Bot — AI Resume Generator for Telegram

A Telegram bot that interviews the user step by step and turns the answers into a ready-to-send resume in PDF and Word. An LLM suggests tasks, achievements, skills and values for the user's role, so people who struggle to describe their experience get concrete options to pick from. Payment is built in through Paynet (Moldova).

## What it does

1. **Interview.** The bot asks about the target position, work experience, education, languages and skills. Progress is saved to the database as the user goes.
2. **AI suggestions.** After the user names the position or a past role, a DigitalOcean GenAI agent generates options to choose from:
   - typical tasks for the position;
   - tasks and achievements for each past role;
   - hard skills and tools;
   - professional values.
3. **Resume build.** The collected answers are processed by the LLM into structured resume content, rendered with Jinja2 + WeasyPrint to **PDF** and with docxtpl to **DOCX**.
4. **Payment.** The bot creates a Paynet invoice (240 MDL) and delivers the files once the payment webhook confirms it. A cover letter template is included.
5. **Languages.** Interface and resume in Russian, English or Romanian.

## Under the hood

| Part | Tech |
|---|---|
| Bot | Python, aiogram 3 (routers for interview, order, payment) |
| AI | DigitalOcean GenAI Agent (OpenAI-compatible chat completions), JSON-repair of LLM output, retries and timeouts |
| Documents | Jinja2 HTML template → WeasyPrint PDF; docxtpl → Word |
| Payments | Paynet API, FastAPI webhook `/paynet/notify` |
| Data | SQLite + SQLAlchemy: users, answers, drafts, orders, payment logs, step logs |
| Analytics | `/stats` endpoint: new, active and paying users, plus a per-step funnel ending with payment |
| Monitoring | Telegram alerts on silent failures: AI agent, Paynet, document generation |

## Project structure

```
run.py                  # bot entry point (polling)
__notify_stub.py        # FastAPI app: Paynet webhook + stats API
handlers/               # interview flow, AI options, order and payment handlers
_genai.py               # LLM call that turns answers into resume content
resume_generator.py     # PDF / DOCX rendering
pay_test.py             # Paynet client (token, invoice, payment registration)
alerts.py               # Telegram alerting
templates/, files/      # resume HTML/CSS and Word templates, cover letters
translations/           # en / ro strings (ru is the base language)
```

## Running locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install aiogram sqlalchemy httpx aiohttp python-dotenv jinja2 weasyprint docxtpl fastapi uvicorn
cp .env.example .env   # fill in the values below
python run.py                          # the bot
uvicorn __notify_stub:app --port 8000  # payment webhook and stats
```

Environment variables (`.env`):

```
TELEGRAM_BOT_TOKEN=        # bot token from @BotFather
DO_API_TOKEN=              # DigitalOcean GenAI agent key
DO_AGENT_URL=              # agent endpoint
PUBLIC_BASE=               # public URL of the webhook app
API_BASE_URL=              # Paynet API
MERCHANT_CODE=
API_USER=
API_PASS=
FORM_SECRET_KEY=
CURRENCY=
LINKSUCCESS=               # redirect after successful payment
LINKERROR=                 # redirect after failed payment
TELEGRAM_ALERT_BOT_TOKEN=  # optional: alerts
TELEGRAM_ALERT_CHAT_IDS=   # optional: comma-separated chat ids
ALERT_PREFIX=HRBot
```

WeasyPrint needs system libraries (Pango, Cairo); see the [WeasyPrint install guide](https://doc.courtbouillon.org/weasyprint/stable/first_steps.html).
