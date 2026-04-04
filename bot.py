import os
import psycopg2
import re
from datetime import datetime

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

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

# ================= SPLIT LOGIC (USDT ONLY) ================= #

def calculate_pending(chat_id):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT currency, amount, rate
        FROM ledger
        WHERE chat_id=%s
        ORDER BY id
    """, (chat_id,))

    rows = cur.fetchall()
    conn.close()

    pending_usdt = 0

    for currency, amount, rate in rows:
        if currency == "USDT":
            pending_usdt += amount
        else:
            pending_usdt -= amount / rate

    return pending_usdt

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
        SELECT user_name, currency, amount
        FROM ledger
        WHERE chat_id=%s
        ORDER BY id
    """, (chat_id,))

    rows = cur.fetchall()
    conn.close()

    usdt_list, inr_list = [], []
    total_usdt, total_inr = 0, 0

    for user, currency, amount in rows:
        if currency == "USDT":
            usdt_list.append((user, amount))
            total_usdt += amount
        else:
            inr_list.append((user, amount))
            total_inr += amount

    # 🔥 CORRECT FINAL LOGIC
    pending_usdt = calculate_pending(chat_id)

    usdt_pending = abs(pending_usdt) if pending_usdt != 0 else 0
    status = "🔴 Pending" if usdt_pending > 0 else "🟢 Balanced"

    # ================= UI ================= #

    text = f"""📊 PECUPAY LEDGER | 📅 {datetime.now().strftime('%d %b %Y')}

━━━━━━━━━━━━━━

💵 USDT
"""

    for i, (user, amt) in enumerate(usdt_list, 1):
        text += f"{i}. 💵 {amt:.2f} U → {user}\n"

    text += "\n━━━━━━━━━━━━━━\n\n💰 INR\n"

    for i, (user, amt) in enumerate(inr_list, 1):
        text += f"{i}. 💰 ₹{amt:,.0f} → {user}\n"

    text += f"""

━━━━━━━━━━━━━━

📈 SUMMARY

💵 USDT : {total_usdt:.2f} U
💰 INR  : ₹{total_inr:,.0f}

💱 Rate : ₹{RATE}
💰 Value: ₹{total_usdt * RATE:,.0f}

🔄 USDT Pending: {usdt_pending:.2f} U

Status : {status}

━━━━━━━━━━━━━━
⚡ PAYUTECH BOT
"""

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

    sign = -1 if text.startswith("-") else 1

    match = re.findall(r"[\d,\.]+", text)
    if not match:
        return

    amount = float(match[0].replace(",", "")) * sign

    if "u" in text:
        currency = "USDT"
    else:
        currency = "INR"

    conn = get_conn()
    cur = conn.cursor()

    cur.execute(
        "INSERT INTO ledger (chat_id,user_name,currency,amount,rate) VALUES (%s,%s,%s,%s,%s)",
        (chat_id, user, currency, amount, RATE)
    )

    conn.commit()
    conn.close()

    # 🔥 ALWAYS SHOW FULL LEDGER
    await ledger(update, context)

# ================= MAIN ================= #

def main():
    init_db()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("rate", set_rate))
    app.add_handler(CommandHandler("ledger", ledger))
    app.add_handler(CommandHandler("clear", clear))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_tx))

    print("🚀 PAYUTECH BOT RUNNING")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
