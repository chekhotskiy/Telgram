
import os
import openai
import sqlite3
import pytesseract
import csv
import json
from telegram.ext import CallbackQueryHandler
from PIL import Image
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from telegram import Update, WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import CommandHandler, CallbackContext

# API-ключи (ОСТАВЛЯЕМ В КОДЕ ДЛЯ ОТЛАДКИ)
OPENAI_API_KEY = "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"
TELEGRAM_BOT_TOKEN = "7624151350:AAGC3EYYMV3KMkQtbVlRApMTOks5cG77kxE"

client = openai.OpenAI(api_key=OPENAI_API_KEY)

# Подключаем базу данных SQLite
conn = sqlite3.connect("receipts.db", check_same_thread=False)
cursor = conn.cursor()

# Создаем таблицу для чеков
cursor.execute('''
CREATE TABLE IF NOT EXISTS receipts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    item TEXT,
    quantity INTEGER,
    price REAL
)
''')
conn.commit()

# Главное меню
def get_main_menu():
    keyboard = [
        [InlineKeyboardButton("📸 Сканировать чек", callback_data="scan_receipt")],
    ]
    return InlineKeyboardMarkup(keyboard)

# Обработчик /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Отправь фото чека, и я обработаю его.", reply_markup=get_main_menu())

# Обработчик кнопки "Сканировать чек"
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("Отправьте фото чека.")

# Обработчик изображений (анализ чека)
async def process_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    photo = await update.message.photo[-1].get_file()
    
    file_path = f"receipt_{user_id}.jpg"
    await photo.download_to_drive(file_path)

    # Распознаем текст с изображения (добавили поддержку русского)
    image = Image.open(file_path)
    text = pytesseract.image_to_string(image, lang="rus+eng")

    # GPT-4o анализирует чек
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "Extract items, quantity, and price from the receipt. Return data ONLY as CSV in format: 'Item, Quantity, Price' without any additional text or explanations."},
            {"role": "user", "content": text}
        ]
    )

    structured_data = response.choices[0].message.content

async def send_form(update: Update, context: CallbackContext):
    keyboard = [
        [InlineKeyboardButton("📝 Заполнить чек", web_app=WebAppInfo("https://chekhotskiy.github.io/"))]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text("Заполните чек:", reply_markup=reply_markup)

async def handle_webapp_data(update: Update, context: CallbackContext):
    query = update.callback_query
    data = query.web_app_data.data  # JSON-данные

    # Декодируем JSON
    receipt = json.loads(data)

    item = receipt["Item"]
    quantity = int(receipt["Quantity"])
    price = float(receipt["Price"])

    # Сохраняем в базу данных
    cursor.execute("INSERT INTO receipts (user_id, item, quantity, price) VALUES (?, ?, ?, ?)",
                   (update.effective_user.id, item, quantity, price))
    conn.commit()

    await query.message.reply_text(f"✅ Чек сохранен: {item}, {quantity} шт, {price} ₽")

    # Сохраняем данные в базу
    for line in structured_data.split("\n"):
        parts = line.split(",")  # Формат: "Item, Quantity, Price"
    
        if len(parts) != 3:
            print(f"⚠ Ошибка формата: {line}")  # Логируем ошибочную строку
            continue  # Пропускаем строку, если формат неверный

        item, quantity, price = parts

        try:
            quantity = int(quantity.strip())  # Убираем пробелы и конвертируем
            price = float(price.strip())  # Конвертируем цену
            cursor.execute("INSERT INTO receipts (user_id, item, quantity, price) VALUES (?, ?, ?, ?)",
                           (user_id, item.strip(), quantity, price))
        except ValueError as e:
            print(f"⚠ Ошибка конвертации: {line} ({e})")  # Показываем, какая строка вызвала ошибку
            continue  # Пропускаем, если данные некорректны

    conn.commit()
    await update.message.reply_text(f"✅ Чек обработан и сохранен!\n\n{structured_data}")

# Экспорт чеков в CSV
async def export_receipts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    file_path = f"receipts_{user_id}.csv"

    cursor.execute("SELECT item, quantity, price FROM receipts WHERE user_id = ?", (user_id,))
    receipts = cursor.fetchall()

    if not receipts:
        await update.message.reply_text("У вас пока нет сохраненных чеков.")
        return

    with open(file_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Товар", "Количество", "Цена"])
        writer.writerows(receipts)

    await update.message.reply_document(document=open(file_path, "rb"), filename="receipts.csv")

# Запускаем бота
def main():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("export", export_receipts))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.PHOTO, process_receipt))  # Обрабатываем фото чеков
    app.add_handler(CommandHandler("form", send_form))
    app.add_handler(CallbackQueryHandler(handle_webapp_data))

    print("Бот запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()
