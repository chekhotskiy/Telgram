import os
import openai
import requests
import pytesseract
import json
import psycopg2
import csv
import logging
from PIL import Image
from aiogram import Bot, Dispatcher, types
import asyncio
import boto3

# Настройки логирования
logging.basicConfig(level=logging.INFO)

# API-ключи (Теперь берутся из переменных окружения)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
R2_ACCESS_KEY = os.getenv("R2_ACCESS_KEY")
R2_SECRET_KEY = os.getenv("R2_SECRET_KEY")
R2_BUCKET_NAME = os.getenv("R2_BUCKET_NAME")
R2_ENDPOINT = os.getenv("R2_ENDPOINT")
DATABASE_URL = os.getenv("DATABASE_URL")

# Инициализация бота
bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher(bot)

# Подключение к Supabase (PostgreSQL)
conn = psycopg2.connect(DATABASE_URL)
cursor = conn.cursor()

# Создаем таблицу для чеков (если ее нет)
cursor.execute('''
CREATE TABLE IF NOT EXISTS receipts (
    id SERIAL PRIMARY KEY,
    user_id BIGINT,
    item TEXT,
    quantity INTEGER,
    price REAL
)
''')
conn.commit()

# Инициализация Cloudflare R2
s3_client = boto3.client(
    "s3",
    endpoint_url=R2_ENDPOINT,
    aws_access_key_id=R2_ACCESS_KEY,
    aws_secret_access_key=R2_SECRET_KEY
)

# Функция обработки запросов к ChatGPT
async def chatgpt_request(prompt):
    openai.api_key = OPENAI_API_KEY
    response = openai.ChatCompletion.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}]
    )
    return response["choices"][0]["message"]["content"]

# Главное меню
def get_main_menu():
    keyboard = [[types.InlineKeyboardButton("📸 Сканировать чек", callback_data="scan_receipt")]]
    return types.InlineKeyboardMarkup(inline_keyboard=keyboard)

# Обработчик команды /start
@dp.message_handler(commands=["start"])
async def start_cmd(message: types.Message):
    await message.reply("Привет! Отправь фото чека, и я обработаю его.", reply_markup=get_main_menu())

# Обработчик кнопки "Сканировать чек"
@dp.callback_query_handler(lambda query: query.data == "scan_receipt")
async def button_handler(callback_query: types.CallbackQuery):
    await callback_query.answer()
    await callback_query.message.answer("Отправьте фото чека.")

# Обработчик изображений (анализ чека)
@dp.message_handler(content_types=types.ContentType.PHOTO)
async def process_receipt(message: types.Message):
    user_id = message.from_user.id
    photo = await bot.get_file(message.photo[-1].file_id)

    file_path = f"receipt_{user_id}.jpg"
    file_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{photo.file_path}"

    # Скачиваем изображение
    response = requests.get(file_url)
    with open(file_path, "wb") as f:
        f.write(response.content)

    # Распознаем текст с изображения (русский и английский)
    image = Image.open(file_path)
    text = pytesseract.image_to_string(image, lang="rus+eng")

    # Отправляем в ChatGPT для структурирования
    response = await chatgpt_request(f"Extract items, quantity, and price from this receipt:\n{text}")
    structured_data = response.strip()

    # Загружаем изображение в Cloudflare R2
    r2_file_name = f"{user_id}/{photo.file_id}.jpg"
    s3_client.put_object(Bucket=R2_BUCKET_NAME, Key=r2_file_name, Body=open(file_path, "rb"))

    file_link = f"https://{R2_BUCKET_NAME}.r2.dev/{r2_file_name}"
    await message.reply(f"✅ Чек сохранен: {file_link}")

    # Разбираем данные и сохраняем в базу
    for line in structured_data.split("\n"):
        parts = line.split(",")

        if len(parts) != 3:
            logging.warning(f"⚠ Ошибка формата: {line}")
            continue

        item, quantity, price = parts

        try:
            quantity = int(quantity.strip())
            price = float(price.strip())

            cursor.execute("INSERT INTO receipts (user_id, item, quantity, price) VALUES (%s, %s, %s, %s)",
                           (user_id, item.strip(), quantity, price))
            conn.commit()
        except ValueError as e:
            logging.error(f"⚠ Ошибка конвертации: {line} ({e})")
            continue

    await message.reply("✅ Чек обработан и сохранен!")

# Экспорт чеков в CSV
@dp.message_handler(commands=["export"])
async def export_receipts(message: types.Message):
    user_id = message.from_user.id
    file_path = f"receipts_{user_id}.csv"

    cursor.execute("SELECT item, quantity, price FROM receipts WHERE user_id = %s", (user_id,))
    receipts = cursor.fetchall()

    if not receipts:
        await message.reply("У вас пока нет сохраненных чеков.")
        return

    with open(file_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Товар", "Количество", "Цена"])
        writer.writerows(receipts)

    await message.reply_document(document=open(file_path, "rb"), filename="receipts.csv")

# Запуск бота
async def main():
    await dp.start_polling()

if __name__ == "__main__":
    asyncio.run(main())
