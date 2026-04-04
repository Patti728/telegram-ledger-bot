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

# ================= SUMMARY ================= #

def get_summary(chat_id):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT COALESCE(SUM(amount),0) FROM ledger WHERE chat_id=%s AND currency='USDT'", (chat_id,))
    usdt = cur.fetchone()[0]

    cur.execute("SELECT COALESCE(SUM(amount),0) FROM ledger WHERE chat_id=%s AND currency='INR'", (chat_id,))
    inr = cur.fetchone()[0]

    conn.close()

    value = usdt * RATE
    inr_pending = max(value - inr, 0)
    usdt_pending = max((inr - value) / RATE, 0) if inr > value else 0

    status = "🟢 Balanced" if inr_pending == 0 and usdt_pending == 0 else "🔴 Pending"

    return usdt, inr, value, inr_pending, usdt_pending, status

# ================= COMMANDS ================= #

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.type == "private":
        return
    if not await is_admin(update, context):
        return
    await update.message.reply_text("🚀 PAYUTECH BOT ACTIVE")

async def summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return

    chat_id = str(update.message.chat.id)
    usdt, inr, value, inr_p, usdt_p, status = get_summary(chat_id)

    await update.message.reply_text(f"""
📊 PAYUTECH SUMMARY | 📅 {datetime.now().strftime("%d %b %Y")}

💵 USDT : {usdt:.2f} U
💰 INR  : ₹{inr:,.0f}

💱 Rate : ₹{RATE}
💵 Value: ₹{value:,.0f}

⚖️ INR Pending : ₹{inr_p:,.0f}
🔄 USDT Pending: {usdt_p:.2f} U

Status : {status}
""")

async def ledger(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

async def set_rate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global RATE

    if not await is_admin(update, context):
        return

    try:
        RATE = float(context.args[0])
        await update.message.reply_text(f"💱 Rate set to ₹{RATE}")
    except:
        await update.message.reply_text("❌ Usage: /rate 100")

# ================= TRANSACTION ================= #

async def handle_tx(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.type == "private":
        return
    if not await is_admin(update, context):
        return

    text = update.message.text.lower().strip()
    user = update.message.from_user.username or update.message.from_user.first_name
    chat_id = str(update.message.chat.id)

    match = re.match(r"([+-]?)([\d,\.]+)\s*(u|inr)?$", text)
    if not match:
        return

    sign = match.group(1)
    amount = float(match.group(2).replace(",", ""))
    currency = match.group(3) or "inr"

    if sign == "-":
        amount = -amount

    conn = get_conn()
    cur = conn.cursor()

    if currency == "u":
        cur.execute("INSERT INTO ledger VALUES (DEFAULT,%s,%s,'USDT',%s,DEFAULT)", (chat_id, user, amount))
        msg = f"{'➖' if amount<0 else '✅'} 💵 {abs(amount):.2f} U {'deducted' if amount<0 else 'added'}"

    else:
        cur.execute("INSERT INTO ledger VALUES (DEFAULT,%s,%s,'INR',%s,DEFAULT)", (chat_id, user, amount))
        msg = f"{'➖' if amount<0 else '✅'} 💰 ₹{abs(amount):,.0f} {'deducted' if amount<0 else 'added'}"

    conn.commit()
    conn.close()

    await update.message.reply_text(msg)
    await summary(update, context)

# ================= MAIN ================= #

def main():
    if not BOT_TOKEN or not DATABASE_URL:
        raise Exception("Missing ENV variables")

    init_db()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("summary", summary))
    app.add_handler(CommandHandler("ledger", ledger))
    app.add_handler(CommandHandler("clear", clear))
    app.add_handler(CommandHandler("rate", set_rate))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_tx))

    print("🚀 PAYUTECH BOT RUNNING")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
