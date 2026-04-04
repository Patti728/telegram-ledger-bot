import os
import psycopg2
import re
from datetime import datetime

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)

# ================= CONFIG ================= #

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

OWNER_ID = 123456789  # change this
RATE = 100

# ================= DB ================= #

def get_conn():
    return psycopg2.connect(DATABASE_URL, sslmode="require")

def init_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS ledger (
        id SERIAL PRIMARY KEY,
        chat_id TEXT,
        user_name TEXT,
        currency TEXT,
        amount FLOAT,
        rate FLOAT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    conn.commit()
    conn.close()

# ================= ADMIN ================= #

async def is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        member = await context.bot.get_chat_member(
            update.message.chat.id,
            update.message.from_user.id
        )
        return member.status in ["administrator", "creator"]
    except:
        return False

# ================= CALC ================= #

def calculate_pending(chat_id):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT rate,
        SUM(CASE WHEN currency='USDT' THEN amount ELSE 0 END),
        SUM(CASE WHEN currency='INR' THEN amount ELSE 0 END)
        FROM ledger
        WHERE chat_id=%s
        GROUP BY rate
    """, (chat_id,))

    rows = cur.fetchall()
    conn.close()

    total = 0
    for rate, usdt, inr in rows:
        usdt = usdt or 0
        inr = inr or 0
        total += (inr / rate) - usdt

    return total

# ================= COMMANDS ================= #

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.type == "private":
        return
    if not await is_admin(update, context):
        return
    await update.message.reply_text("🚀 PAYUTECH BOT ACTIVE")

async def set_rate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global RATE
    if not await is_admin(update, context):
        return
    try:
        RATE = float(context.args[0])
        await update.message.reply_text(f"💱 Rate set to ₹{RATE}")
    except:
        await update.message.reply_text("❌ Use: /rate 98")

async def summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return

    chat_id = str(update.message.chat.id)
    pending = calculate_pending(chat_id)

    status = "🟢 Balanced" if abs(pending) < 1 else "🔴 Pending"

    await update.message.reply_text(f"""
📊 PAYUTECH SUMMARY | 📅 {datetime.now().strftime("%d %b %Y")}

💼 Pending: {pending:.2f} U
Status: {status}
""")

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await summary(update, context)

async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return

    chat_id = str(update.message.chat.id)

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM ledger WHERE chat_id=%s", (chat_id,))
    conn.commit()
    conn.close()

    await update.message.reply_text("🗑 Ledger Cleared")

# ================= LEDGER ================= #

async def ledger(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return

    chat_id = str(update.message.chat.id)

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT user_name, currency, amount, rate
        FROM ledger
        WHERE chat_id=%s
        ORDER BY id
    """, (chat_id,))

    rows = cur.fetchall()
    conn.close()

    text = f"📒 PAYUTECH LEDGER | 📅 {datetime.now().strftime('%d %b %Y')}\n\n"

    for i, (user, currency, amount, rate) in enumerate(rows, 1):
        text += f"{i}. {user} → {currency} {amount} @ ₹{rate}\n"

    await update.message.reply_text(text)

# ================= TRANSACTION ================= #

async def handle_tx(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.type == "private":
        return
    if not await is_admin(update, context):
        return

    text = update.message.text.lower().strip()
    user = update.message.from_user.username or update.message.from_user.first_name
    chat_id = str(update.message.chat.id)

    # sign
    sign = -1 if text.startswith("-") else 1

    # amount
    amount_match = re.findall(r"[\d,\.]+", text)
    if not amount_match:
        return

    amount = float(amount_match[0].replace(",", "")) * sign

    # currency
    if "u" in text:
        currency = "USDT"
    elif "inr" in text:
        currency = "INR"
    else:
        currency = "INR"

    conn = get_conn()
    cur = conn.cursor()

    # ✅ FIXED SQL
    cur.execute(
        "INSERT INTO ledger (chat_id, user_name, currency, amount, rate) VALUES (%s, %s, %s, %s, %s)",
        (chat_id, user, currency, amount, RATE)
    )

    conn.commit()
    conn.close()

    await update.message.reply_text(
        f"{'➖' if amount < 0 else '✅'} {currency} {abs(amount)} @ ₹{RATE}"
    )

# ================= MAIN ================= #

def main():
    init_db()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("rate", set_rate))
    app.add_handler(CommandHandler("summary", summary))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("ledger", ledger))
    app.add_handler(CommandHandler("clear", clear))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_tx))

    print("🚀 PAYUTECH BOT RUNNING")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
