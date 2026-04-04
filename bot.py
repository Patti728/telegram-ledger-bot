import os
import psycopg2
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

TOKEN = os.getenv("8646101570:AAGLhe9jMnBbZo_3U1SmuTOrKl0T78ebpJA")
DATABASE_URL = os.getenv("postgresql://postgres:cFYGCNhajBBfGVjIQjMmrbJhzEebvHus@junction.proxy.rlwy.net:17523/railway")
RATE = 100

# ================= DB ================= #

def get_conn():
    return psycopg2.connect(DATABASE_URL)

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

# ================= HELPERS ================= #

def clean_amount(text):
    return float(
        text.replace(",", "")
        .replace("inr", "")
        .replace("u", "")
        .replace("+", "")
        .strip()
    )

def get_summary(chat_id):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT SUM(amount) FROM ledger WHERE chat_id=%s AND currency='USDT'", (chat_id,))
    usdt = cur.fetchone()[0] or 0

    cur.execute("SELECT SUM(amount) FROM ledger WHERE chat_id=%s AND currency='INR'", (chat_id,))
    inr = cur.fetchone()[0] or 0

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

    await update.message.reply_text("🚀 PAYUTECH BOT Active")

async def summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return

    chat_id = str(update.message.chat.id)
    usdt, inr, value, inr_pending, usdt_pending, status = get_summary(chat_id)

    await update.message.reply_text(f"""
📊 PAYUTECH SUMMARY | 📅 {datetime.now().strftime("%d %b %Y")}

💵 USDT : {usdt:.2f} U
💰 INR  : ₹{inr:,.0f}

💱 Rate : ₹{RATE}
💵 Value: ₹{value:,.0f}

⚖️ INR Pending : ₹{inr_pending:,.0f}
🔄 USDT Pending: {usdt_pending:.2f} U

Status : {status}

━━━━━━━━━━━━━━
⚡ PAYUTECH BOT
""")

# ================= TRANSACTION ================= #

async def handle_tx(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.type == "private":
        return
    if not await is_admin(update, context):
        return

    text = update.message.text.lower()
    user = update.message.from_user.username or update.message.from_user.first_name
    chat_id = str(update.message.chat.id)

    conn = get_conn()
    cur = conn.cursor()

    try:
        if "u" in text:
            amount = clean_amount(text)
            cur.execute(
                "INSERT INTO ledger (chat_id,user_name,currency,amount) VALUES (%s,%s,'USDT',%s)",
                (chat_id, user, amount)
            )
            msg = f"✅ 💵 {amount} U added"

        elif "inr" in text:
            amount = clean_amount(text)
            cur.execute(
                "INSERT INTO ledger (chat_id,user_name,currency,amount) VALUES (%s,%s,'INR',%s)",
                (chat_id, user, amount)
            )
            msg = f"✅ 💰 ₹{amount:,.0f} added"

        else:
            conn.close()
            return

        conn.commit()
        conn.close()

        await update.message.reply_text(msg)
        await summary(update, context)

    except Exception as e:
        conn.close()
        print("ERROR:", e)
        await update.message.reply_text("❌ Transaction Error")

# ================= MAIN ================= #

init_db()

app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("summary", summary))
app.add_handler(MessageHandler(filters.TEXT, handle_tx))

print("🚀 PAYUTECH BOT Running Stable...")

app.run_polling(drop_pending_updates=True)
