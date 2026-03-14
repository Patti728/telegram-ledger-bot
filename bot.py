from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
import sqlite3
from datetime import datetime
import re

TOKEN = "PUT_YOUR_NEW_TOKEN_HERE"

rate = 100

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


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "🚀 *Astryx Pay Ledger Bot Ready*\n\n"
        "Commands:\n"
        "+500u\n"
        "-500u\n"
        "+10000inr\n"
        "-10000inr\n\n"
        "/balance\n"
        "/rate 102\n"
        "/clear",
        parse_mode="Markdown"
    )


async def set_rate(update: Update, context: ContextTypes.DEFAULT_TYPE):

    global rate

    if len(context.args) == 0:
        await update.message.reply_text("⚠ Usage: /rate 102")
        return

    rate = float(context.args[0])

    await update.message.reply_text(
        f"💱 *Exchange Rate Updated*\n\n"
        f"1 USDT = ₹{rate}",
        parse_mode="Markdown"
    )


async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):

    chat = str(update.message.chat_id)

    cursor.execute("DELETE FROM usdt WHERE chat_id=?", (chat,))
    cursor.execute("DELETE FROM inr WHERE chat_id=?", (chat,))
    conn.commit()

    await update.message.reply_text("🧹 Ledger cleared for this group")


def build_report(chat):

    usdt_rows = cursor.execute(
        "SELECT user,amount,time FROM usdt WHERE chat_id=?",
        (chat,)
    ).fetchall()

    inr_rows = cursor.execute(
        "SELECT user,amount,time FROM inr WHERE chat_id=?",
        (chat,)
    ).fetchall()

    usdt_total = 0
    inr_total = 0

    dep_list = []
    inr_list = []

    for user, amount, time in usdt_rows:

        sign = "🟢 +" if amount >= 0 else "🔴 -"

        dep_list.append(f"{time} | {user} | {sign}{abs(amount)} U")

        usdt_total += amount

    for user, amount, time in inr_rows:

        sign = "🟢 +" if amount >= 0 else "🔴 -"

        inr_list.append(f"{time} | {user} | {sign}₹{abs(amount)}")

        inr_total += amount


    usdt_value = usdt_total * rate

    outstanding = usdt_value - inr_total


    message = f"""
💎 *ASTRYX PAY LEDGER*

━━━━━━━━━━━━━━

💵 *USDT Transactions*
Total: {len(dep_list)}

""" + "\n".join(dep_list) + f"""

━━━━━━━━━━━━━━

💰 *INR Transactions*
Total: {len(inr_list)}

""" + "\n".join(inr_list) + f"""

━━━━━━━━━━━━━━

📊 *SUMMARY*

💵 USDT Balance : {usdt_total} U
💰 INR Balance : ₹{inr_total}

💱 Rate : ₹{rate}

💎 USDT Value : ₹{usdt_value}

⚠ *Outstanding : ₹{outstanding}*

━━━━━━━━━━━━━━
⚡ Powered by Astryx Pay
"""

    return message


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):

    text = update.message.text.lower().strip()

    user = update.message.from_user.first_name

    chat = str(update.message.chat_id)

    time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    pattern = r'^([+-])\s*(\d+)\s*(inr|₹|u|usdt)?$'

    match = re.match(pattern, text)

    if not match:
        return

    sign = match.group(1)

    amount = float(match.group(2))

    currency = match.group(3)

    if sign == "-":
        amount = -amount

    if currency is None:
        currency = "u"

    if currency in ["inr","₹"]:

        cursor.execute(
            "INSERT INTO inr(chat_id,user,amount,time) VALUES(?,?,?,?)",
            (chat,user,amount,time)
        )

        conn.commit()

        await update.message.reply_text(build_report(chat), parse_mode="Markdown")

        return


    if currency in ["u","usdt"]:

        cursor.execute(
            "INSERT INTO usdt(chat_id,user,amount,time) VALUES(?,?,?,?)",
            (chat,user,amount,time)
        )

        conn.commit()

        await update.message.reply_text(build_report(chat), parse_mode="Markdown")

        return


async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):

    chat = str(update.message.chat_id)

    await update.message.reply_text(build_report(chat), parse_mode="Markdown")


app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("balance", balance))
app.add_handler(CommandHandler("rate", set_rate))
app.add_handler(CommandHandler("clear", clear))

app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))

print("🚀 BOT RUNNING")

app.run_polling()
