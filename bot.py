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
type TEXT,
amount REAL,
time TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS inr(
id INTEGER PRIMARY KEY AUTOINCREMENT,
chat_id TEXT,
user TEXT,
type TEXT,
amount REAL,
time TEXT
)
""")

conn.commit()


def parse_amount(text):
    return float(
        text.replace(",", "")
        .replace("inr", "")
        .replace("u", "")
        .replace("+", "")
        .replace("-", "")
        .strip()
    )


def format_inr(v):
    return f"₹{v:,.2f}"


def format_usdt(v):
    return f"{v:,.2f} U"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⚡ Astryx Pay Ledger Bot Ready")


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):

    global rate

    text = update.message.text.lower()
    user = update.message.from_user.username or update.message.from_user.first_name
    chat_id = update.message.chat.id
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:

        # INR ADD
        if text.startswith("+") and "inr" in text:

            amount = parse_amount(text)

            cursor.execute(
                "INSERT INTO inr(chat_id,user,amount,time) VALUES(?,?,?,?)",
                (chat_id, user, amount, now)
            )
            conn.commit()

            await update.message.reply_text(
f"""
💰 INR CREDITED

👤 {user}
🕒 {now}

Amount: +{format_inr(amount)}

⚡ Powered by Astryx Pay
"""
            )

        # INR DEDUCT
        elif text.startswith("-") and "inr" in text:

            amount = parse_amount(text)

            cursor.execute(
                "INSERT INTO inr(chat_id,user,amount,time) VALUES(?,?,?,?)",
                (chat_id, user, -amount, now)
            )
            conn.commit()

            await update.message.reply_text(
f"""
💸 INR DEBITED

👤 {user}
🕒 {now}

Amount: -{format_inr(amount)}

⚡ Powered by Astryx Pay
"""
            )

        # USDT ADD
        elif text.startswith("+") and "u" in text:

            amount = parse_amount(text)

            cursor.execute(
                "INSERT INTO usdt(chat_id,user,type,amount,time) VALUES(?,?,?,?,?)",
                (chat_id, user, "IN", amount, now)
            )
            conn.commit()

            await update.message.reply_text(
f"""
💎 USDT RECEIVED

👤 {user}
🕒 {now}

Amount: +{format_usdt(amount)}

⚡ Powered by Astryx Pay
"""
            )

        # USDT SEND
        elif text.startswith("-") and "u" in text:

            amount = parse_amount(text)

            cursor.execute(
                "INSERT INTO usdt(chat_id,user,type,amount,time) VALUES(?,?,?,?,?)",
                (chat_id, user, "OUT", amount, now)
            )
            conn.commit()

            await update.message.reply_text(
f"""
💎 USDT SENT

👤 {user}
🕒 {now}

Amount: -{format_usdt(amount)}

⚡ Powered by Astryx Pay
"""
            )

        # SET RATE
        elif text.startswith("rate"):

            rate = float(text.split()[1])

            await update.message.reply_text(
f"""
📊 RATE UPDATED

💱 1 USDT = ₹{rate}
"""
            )

        # BALANCE
        elif text == "balance":

            cursor.execute("SELECT SUM(amount) FROM usdt WHERE type='IN'")
            usdt_in = cursor.fetchone()[0] or 0

            cursor.execute("SELECT SUM(amount) FROM usdt WHERE type='OUT'")
            usdt_out = cursor.fetchone()[0] or 0

            usdt_balance = usdt_in - usdt_out

            cursor.execute("SELECT SUM(amount) FROM inr")
            inr_balance = cursor.fetchone()[0] or 0

            usdt_value = usdt_balance * rate
            outstanding = usdt_value - inr_balance

            await update.message.reply_text(
f"""
📊 ASTRYX PAY SUMMARY

💵 USDT Balance : {format_usdt(usdt_balance)}
💰 INR Balance : {format_inr(inr_balance)}

💱 Rate : ₹{rate}

💎 USDT Value : {format_inr(usdt_value)}

⚠ Outstanding : {format_inr(outstanding)}

⚡ Powered by Astryx Pay
"""
            )

        # REPORT
        elif text == "report":

            cursor.execute("SELECT time,user,amount FROM usdt ORDER BY id DESC LIMIT 10")
            usdt_rows = cursor.fetchall()

            cursor.execute("SELECT time,user,amount FROM inr ORDER BY id DESC LIMIT 10")
            inr_rows = cursor.fetchall()

            usdt_log = "\n".join([f"{r[0]} | {r[1]} | {r[2]} U" for r in usdt_rows])
            inr_log = "\n".join([f"{r[0]} | {r[1]} | ₹{r[2]}" for r in inr_rows])

            await update.message.reply_text(
f"""
📊 ASTRYX PAY LEDGER

━━━━━━━━━━

💵 USDT Transactions
{usdt_log}

━━━━━━━━━━

💰 INR Transactions
{inr_log}

⚡ Powered by Astryx Pay
"""
            )

    except:
        await update.message.reply_text("❌ Invalid Command")


app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT, handle))

print("🚀 Astryx Pay Bot Running...")
app.run_polling()
