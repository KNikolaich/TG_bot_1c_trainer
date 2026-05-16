# ============================================================
# TELEGRAM AI EXAMINATOR FOR 1C DEVELOPERS
# SINGLE FILE VERSION
# GOOGLE COLAB READY
# ============================================================

# ============================================================
# INSTALL
# ============================================================

#!pip install -q aiogram openai pandas aiosqlite nest_asyncio

# ============================================================
# IMPORTS
# ============================================================

import os
import re
import sys
import json
import random
import asyncio
import logging

from datetime import datetime

import pandas as pd
import aiosqlite
import nest_asyncio

from openai import AsyncOpenAI
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage

# ============================================================
# LOGGING
# ============================================================

load_dotenv()

for handler in logging.root.handlers[:]:
    logging.root.removeHandler(handler)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(
            "bot.log",
            encoding="utf-8"
        ),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)

# ============================================================
# LOAD SECRETS
# ============================================================

USE_COLAB_SECRETS = True

if USE_COLAB_SECRETS:

    try:

        from google.colab import userdata

        TELEGRAM_TOKEN = userdata.get(
            "TELEGRAM_BOT_TOKEN"
        )

        OPENAI_API_KEY = userdata.get(
            "OPENAI_API_KEY"
        )

        QUESTIONS_URL = userdata.get(
            "QUESTIONS_URL"
        )

        logger.info(
            "Secrets loaded from Colab"
        )

    except Exception:

        TELEGRAM_TOKEN = os.getenv(
            "TELEGRAM_BOT_TOKEN"
        )

        OPENAI_API_KEY = os.getenv(
            "OPENAI_API_KEY"
        )

        QUESTIONS_URL = os.getenv(
            "QUESTIONS_URL"
        )

# ============================================================
# HELPERS
# ============================================================

def get_csv_url(url):

    if (
        "docs.google.com" in url
        and "/edit" in url
    ):

        return re.sub(
            r"/edit.*",
            "/export?format=csv",
            url
        )

    return url

# ============================================================
# FORMAT AI RESPONSE
# ============================================================

def format_ai_response(text):

    if not text:
        return "AI не вернул ответ"

    text = text.replace("```html", "")
    text = text.replace("```", "")

    # H1
    text = re.sub(
        r"^# (.*)$",
        r"?? <b>\1</b>",
        text,
        flags=re.MULTILINE
    )

    # H2
    text = re.sub(
        r"^## (.*)$",
        r"\n<b>\1</b>",
        text,
        flags=re.MULTILINE
    )

    # bullet points
    text = re.sub(
        r"^- ",
        "• ",
        text,
        flags=re.MULTILINE
    )

    # bold markdown
    text = re.sub(
        r"\*\*(.*?)\*\*",
        r"<b>\1</b>",
        text
    )

    # italic markdown
    text = re.sub(
        r"\*(.*?)\*",
        r"<i>\1</i>",
        text
    )

    # remove excessive empty lines
    text = re.sub(
        r"\n{3,}",
        "\n\n",
        text
    )

    return text.strip()

# ============================================================
# OPENAI
# ============================================================

client = AsyncOpenAI(
    api_key=OPENAI_API_KEY,
    timeout=120.0
)

# ============================================================
# BOT
# ============================================================

bot = Bot(
    token=TELEGRAM_TOKEN,

    default=DefaultBotProperties(
        parse_mode=ParseMode.HTML
    )
)

dp = Dispatcher(
    storage=MemoryStorage()
)

# ============================================================
# DATABASE
# ============================================================

DB_NAME = "examiner.db"

# ============================================================
# STATES
# ============================================================

class ExamStates(StatesGroup):

    choosing_count = State()
    choosing_level = State()
    answering = State()

# ============================================================
# GLOBALS
# ============================================================

QUESTIONS = []

# ============================================================
# DATABASE INIT
# ============================================================

async def init_db():

    logger.info(
        "Initializing DB"
    )

    async with aiosqlite.connect(DB_NAME) as db:

        await db.execute("""
        CREATE TABLE IF NOT EXISTS answers (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            user_id INTEGER,

            question_id INTEGER,

            question TEXT,

            user_answer TEXT,

            ai_score REAL,

            created_at TEXT
        )
        """)

        await db.commit()

    logger.info(
        "DB initialized"
    )

# ============================================================
# DETECT LEVEL
# ============================================================

def detect_level(text):

    text = text.lower()

    if any(
        x in text
        for x in ["junior", "простой"]
    ):
        return "junior"

    if any(
        x in text
        for x in ["middle", "средний"]
    ):
        return "middle"

    if any(
        x in text
        for x in ["senior", "сложный"]
    ):
        return "senior"

    if any(
        x in text
        for x in ["expert", "эксперт"]
    ):
        return "expert"

    return "mixed"

# ============================================================
# LOAD QUESTIONS
# ============================================================

async def load_questions():

    global QUESTIONS

    QUESTIONS = []

    csv_link = get_csv_url(
        QUESTIONS_URL
    )

    logger.info(
        f"Loading CSV: {csv_link}"
    )

    try:

        df = pd.read_csv(
            csv_link,
            encoding="utf-8"
        )

        logger.info(
            f"CSV loaded. Rows: {len(df)}"
        )

        logger.info(
            f"Columns: {list(df.columns)}"
        )

        for idx, row in df.iterrows():

            try:

                question = str(
                    row.get("Вопрос", "")
                ).strip()

                answer = str(
                    row.get("ответ", "")
                ).strip()

                level_raw = str(
                    row.get("сложность", "")
                )

                if (
                    not question
                    or question == "nan"
                ):
                    continue

                level = detect_level(
                    level_raw
                )

                QUESTIONS.append({

                    "id": idx + 1,

                    "level": level,

                    "question": question,

                    "answer": answer
                })

            except Exception:

                logger.exception(
                    f"Row parse error idx={idx}"
                )

        logger.info(
            f"Questions loaded: {len(QUESTIONS)}"
        )

    except Exception:

        logger.exception(
            "LOAD QUESTIONS ERROR"
        )

# ============================================================
# OPENAI REQUEST
# ============================================================

async def openai_request(messages):

    retries = 3

    for attempt in range(retries):

        try:

            response = await client.chat.completions.create(

                model="gpt-4.1-mini",

                temperature=0.3,

                messages=messages
            )

            return response

        except Exception:

            logger.exception(
                f"OpenAI retry {attempt+1}"
            )

            if attempt == retries - 1:
                raise

            await asyncio.sleep(2)

# ============================================================
# KEYBOARDS
# ============================================================

def questions_keyboard():

    return InlineKeyboardMarkup(
        inline_keyboard=[

            [
                InlineKeyboardButton(
                    text="3 вопроса",
                    callback_data="count_3"
                )
            ],

            [
                InlineKeyboardButton(
                    text="5 вопросов",
                    callback_data="count_5"
                )
            ],

            [
                InlineKeyboardButton(
                    text="10 вопросов",
                    callback_data="count_10"
                )
            ],

            [
                InlineKeyboardButton(
                    text="20 вопросов",
                    callback_data="count_20"
                )
            ]
        ]
    )

def level_keyboard():

    return InlineKeyboardMarkup(
        inline_keyboard=[

            [
                InlineKeyboardButton(
                    text="Junior",
                    callback_data="lvl_junior"
                )
            ],

            [
                InlineKeyboardButton(
                    text="Middle",
                    callback_data="lvl_middle"
                )
            ],

            [
                InlineKeyboardButton(
                    text="Senior",
                    callback_data="lvl_senior"
                )
            ],

            [
                InlineKeyboardButton(
                    text="Expert",
                    callback_data="lvl_expert"
                )
            ]
        ]
    )

# ============================================================
# START
# ============================================================

@dp.message(Command("start"))
async def start_handler(
    message: Message,
    state: FSMContext
):

    logger.info(
        f"/start user={message.from_user.id}"
    )

    await state.set_state(
        ExamStates.choosing_count
    )

    text = """
?? <b>AI Экзаменатор по 1С</b>

Проверка знаний:
• Платформа 1С
• Запросы
• СКД
• Архитектура
• Производительность
• Оптимизация

Выбери количество вопросов ??
"""

    await message.answer(
        text,
        reply_markup=questions_keyboard()
    )

# ============================================================
# RELOAD
# ============================================================

@dp.message(Command("reload"))
async def reload_handler(message: Message):

    logger.info(
        f"/reload user={message.from_user.id}"
    )

    await message.answer(
        "?? Перечитываю Google Sheet..."
    )

    try:

        await load_questions()

        await message.answer(
            f"""
? <b>База обновлена</b>

?? Вопросов загружено:
<b>{len(QUESTIONS)}</b>
"""
        )

    except Exception as e:

        logger.exception(
            "/reload error"
        )

        await message.answer(
            f"""
? Ошибка обновления

<code>{str(e)}</code>
"""
        )

# ============================================================
# QUESTION COUNT
# ============================================================

@dp.callback_query(
    ExamStates.choosing_count,
    F.data.startswith("count_")
)
async def process_question_count(
    callback: CallbackQuery,
    state: FSMContext
):

    try:

        count = int(
            callback.data.split("_")[1]
        )

        await state.update_data(
            total_count=count
        )

        await state.set_state(
            ExamStates.choosing_level
        )

        await callback.message.delete()

        await callback.message.answer(
            f"""
?? Выбрано вопросов:
<b>{count}</b>

Теперь выбери уровень ??
""",
            reply_markup=level_keyboard()
        )

        await callback.answer()

    except Exception:

        logger.exception(
            "process_question_count error"
        )

# ============================================================
# START EXAM
# ============================================================

@dp.callback_query(
    ExamStates.choosing_level,
    F.data.startswith("lvl_")
)
async def process_difficulty(
    callback: CallbackQuery,
    state: FSMContext
):

    try:

        lvl = callback.data.split("_")[1]

        data = await state.get_data()

        count = data["total_count"]

        logger.info(
            f"Selected level={lvl}"
        )

        filtered = [

            q for q in QUESTIONS

            if q["level"] == lvl
            or q["level"] == "mixed"
        ]

        if not filtered:

            filtered = QUESTIONS

        if not filtered:

            await callback.message.answer(
                "? База вопросов пустая"
            )

            return

        pool = random.sample(
            filtered,
            min(len(filtered), count)
        )

        await state.update_data(
            pool=pool,
            current_idx=0,
            user_answers=[]
        )

        await state.set_state(
            ExamStates.answering
        )

        first = pool[0]

        await callback.message.delete()

        await callback.message.answer(
            f"""
?? <b>Экзамен начался</b>

?? Уровень:
<b>{lvl.upper()}</b>

?? Вопрос 1 из {len(pool)}

???????????????

{first["question"]}
"""
        )

        await callback.answer()

    except Exception:

        logger.exception(
            "process_difficulty error"
        )

        await callback.message.answer(
            "? Ошибка запуска экзамена"
        )

# ============================================================
# ANSWERS
# ============================================================

@dp.message(ExamStates.answering)
async def handle_answer(
    message: Message,
    state: FSMContext
):

    try:

        data = await state.get_data()

        pool = data["pool"]

        idx = data["current_idx"]

        answers = data.get(
            "user_answers",
            []
        )

        current = pool[idx]

        logger.info("question: " + current["question"])
        logger.info("user_answer: " + message.text)

        answers.append({

            "question": current["question"],

            "correct_answer": current["answer"],

            "user_answer": message.text
        })

        idx += 1

        # ====================================================
        # NEXT QUESTION
        # ====================================================

        if idx < len(pool):

            await state.update_data(

                current_idx=idx,

                user_answers=answers
            )

            next_question = pool[idx]

            await message.answer(
                f"""
? Ответ принят

?? <b>Вопрос {idx+1} из {len(pool)}</b>

???????????????

{next_question["question"]}
"""
            )

            return

        # ====================================================
        # FINISH
        # ====================================================

        await message.answer(
            """
? <b>Экзамен завершен</b>

AI анализирует ответы...
"""
        )

        await state.clear()

        prompt = """
Ты мудрый, опытный и доброжелательный senior interviewer по 1С.

Верни ответ в markdown формате.

Структура:

# Общая оценка

# Сильные стороны

# Что подтянуть

# Разбор ответов

## Вопрос 1

## Вопрос 2

# Финальный вывод

Пиши красиво.
Кратко.
Структурировано.
"""

        for i, ans in enumerate(answers):

            prompt += f"""

========================

Вопрос {i+1}:
{ans["question"]}

Эталонный ответ:
{ans["correct_answer"]}

Ответ пользователя:
{ans["user_answer"]}
"""

        response = await openai_request([

            {
                "role": "system",
                "content": "Ты senior interviewer 1С"
            },

            {
                "role": "user",
                "content": prompt
            }
        ])

        result = response.choices[0].message.content

        result = format_ai_response(
            result
        )

        logger.info(result)

        await message.answer(
            result,
            parse_mode=ParseMode.HTML
        )

        await message.answer(
            """
?? Хочешь пройти ещё один экзамен?

Нажми:
/start
"""
        )

    except Exception:

        logger.exception(
            "handle_answer error"
        )

        await message.answer(
            "? Ошибка обработки ответа"
        )

# ============================================================
# MAIN
# ============================================================

async def main():

    logger.info(
        "BOT STARTING"
    )

    await init_db()

    await load_questions()

    logger.info(
        f"Questions loaded: {len(QUESTIONS)}"
    )

    await dp.start_polling(bot)

# ============================================================
# STOP PREVIOUS TASKS
# ============================================================

async def stop_previous_tasks():

    logger.info(
        "Stopping previous tasks..."
    )

    current_task = asyncio.current_task()

    tasks = [

        task for task in asyncio.all_tasks()

        if task is not current_task
    ]

    for task in tasks:

        task.cancel()

    await asyncio.gather(
        *tasks,
        return_exceptions=True
    )

    try:

        await bot.session.close()

    except Exception:

        pass

# ============================================================
# RUN
# ============================================================

async def run_bot():

    await stop_previous_tasks()

    logger.info(
        "Starting bot..."
    )

    await main()

# ============================================================
# ENTRYPOINT
# ============================================================

if __name__ == "__main__":

    nest_asyncio.apply()

    try:

        loop = asyncio.get_event_loop()

        loop.create_task(
            run_bot()
        )

        print("""
============================================================
AI TELEGRAM EXAM BOT STARTED
============================================================

Команды:
/start
/reload

Логи:
bot.log

============================================================
""")

    except Exception:

        logger.exception(
            "MAIN LOOP ERROR"
        )