from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
import sqlite3
from datetime import datetime

TOKEN = "8764568325:AAFs-6ErToz0zDv7AZTubizeSx9m7UnvNp8"

rate = 101

conn = sqlite3.connect("ledger.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS usdt(
id INTEGER PRIMARY KEY AUTOINCREMENT,
chat_id TEXT,
user TEXT,
amount REAL,
time TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS inr(
id INTEGER PRIMARY KEY AUTOINCREMENT,
chat_id TEXT,
user TEXT,
amount REAL,
time TEXT
)
""")

conn.commit()


def clean_amount(text):
    return float(
        text.replace(",", "")
        .replace("inr","")
        .replace("u","")
        .replace("+","")
        .replace("-","")
        .strip()
    )


def calculate_summary():
    cursor.execute("SELECT SUM(amount) FROM usdt")
    usdt_balance = cursor.fetchone()[0] or 0

    cursor.execute("SELECT SUM(amount) FROM inr")
    inr_balance = cursor.fetchone()[0] or 0

    usdt_value = usdt_balance * rate

    inr_outstanding = 0
    usdt_outstanding = 0

    if usdt_value > inr_balance:
        inr_outstanding = usdt_value - inr_balance
    elif inr_balance > usdt_value:
        usdt_outstanding = (inr_balance - usdt_value) / rate

    return usdt_balance, inr_balance, usdt_value, inr_outstanding, usdt_outstanding


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⚡ Astryx Pay Ledger Bot Ready")


async def set_rate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global rate
    rate = float(context.args[0])
    await update.message.reply_text(f"💱 Rate set to ₹{rate}")


async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):

    usdt_balance, inr_balance, usdt_value, inr_outstanding, usdt_outstanding = calculate_summary()

    await update.message.reply_text(
f"""
📊 SUMMARY

💵 USDT Balance : {usdt_balance} U
💰 INR Balance : ₹{inr_balance:,.2f}

💱 Rate : ₹{rate}

💎 USDT Value : ₹{usdt_value:,.2f}

⚠ INR Outstanding : ₹{inr_outstanding:,.2f}
⚠ USDT Outstanding : {usdt_outstanding:.2f} U

⚡ Powered by Astryx Pay
"""
    )


async def ledger(update: Update, context: ContextTypes.DEFAULT_TYPE):

    cursor.execute("SELECT time,user,amount FROM usdt ORDER BY id")
    usdt_rows = cursor.fetchall()

    cursor.execute("SELECT time,user,amount FROM inr ORDER BY id")
    inr_rows = cursor.fetchall()

    usdt_log = "\n".join(
        [f"{r[0]} | {r[1]} | {'🟢' if r[2]>0 else '🔴'} {r[2]} U" for r in usdt_rows]
    )

    inr_log = "\n".join(
        [f"{r[0]} | {r[1]} | {'🟢' if r[2]>0 else '🔴'} ₹{abs(r[2]):,.2f}" for r in inr_rows]
    )

    usdt_balance, inr_balance, usdt_value, inr_outstanding, usdt_outstanding = calculate_summary()

    await update.message.reply_text(
f"""
💎 ASTRYX PAY LEDGER

━━━━━━━━━━━━

💵 USDT Transactions
{usdt_log}

━━━━━━━━━━━━

💰 INR Transactions
{inr_log}

━━━━━━━━━━━━

📊 SUMMARY

💵 USDT Balance : {usdt_balance} U
💰 INR Balance : ₹{inr_balance:,.2f}

💱 Rate : ₹{rate}

💎 USDT Value : ₹{usdt_value:,.2f}

⚠ INR Outstanding : ₹{inr_outstanding:,.2f}
⚠ USDT Outstanding : {usdt_outstanding:.2f} U

━━━━━━━━━━━━
⚡ Powered by Astryx Pay
"""
    )


async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):

    cursor.execute("DELETE FROM usdt")
    cursor.execute("DELETE FROM inr")
    conn.commit()

    await update.message.reply_text("🧹 Ledger cleared")


async def transactions(update: Update, context: ContextTypes.DEFAULT_TYPE):

    text = update.message.text.lower()
    user = update.message.from_user.username or update.message.from_user.first_name
    chat_id = update.message.chat.id
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:

        if text.startswith("+") and "u" in text:

            amount = clean_amount(text)
            cursor.execute("INSERT INTO usdt(chat_id,user,amount,time) VALUES(?,?,?,?)",
                           (chat_id,user,amount,now))
            conn.commit()

        elif text.startswith("-") and "u" in text:

            amount = clean_amount(text)
            cursor.execute("INSERT INTO usdt(chat_id,user,amount,time) VALUES(?,?,?,?)",
                           (chat_id,user,-amount,now))
            conn.commit()

        elif text.startswith("+") and "inr" in text:

            amount = clean_amount(text)
            cursor.execute("INSERT INTO inr(chat_id,user,amount,time) VALUES(?,?,?,?)",
                           (chat_id,user,amount,now))
            conn.commit()

        elif text.startswith("-") and "inr" in text:

            amount = clean_amount(text)
            cursor.execute("INSERT INTO inr(chat_id,user,amount,time) VALUES(?,?,?,?)",
                           (chat_id,user,-amount,now))
            conn.commit()

        else:
            return

        await balance(update, context)

    except:
        await update.message.reply_text("❌ Invalid command")


app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("rate", set_rate))
app.add_handler(CommandHandler("balance", balance))
app.add_handler(CommandHandler("ledger", ledger))
app.add_handler(CommandHandler("clear", clear))

app.add_handler(MessageHandler(filters.TEXT, transactions))

print("🚀 Astryx Pay Bot Running...")
app.run_polling()
