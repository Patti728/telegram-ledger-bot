from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
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
amount REAL,
time TEXT
)
""")

conn.commit()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Ledger bot ready")


async def set_rate(update: Update, context: ContextTypes.DEFAULT_TYPE):

    global rate

    if len(context.args) == 0:
        await update.message.reply_text("Usage: /rate 101")
        return

    rate = float(context.args[0])

    await update.message.reply_text(f"Rate set to {rate}")


async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):

    chat = str(update.message.chat_id)

    cursor.execute("DELETE FROM usdt WHERE chat_id=?", (chat,))
    cursor.execute("DELETE FROM inr WHERE chat_id=?", (chat,))
    conn.commit()

    await update.message.reply_text("Ledger cleared for this group")


def build_report(chat):

    usdt_rows = cursor.execute(
        "SELECT user,type,amount,time FROM usdt WHERE chat_id=?",
        (chat,)
    ).fetchall()

    inr_rows = cursor.execute(
        "SELECT user,amount,time FROM inr WHERE chat_id=?",
        (chat,)
    ).fetchall()

    deposits = []
    distributions = []
    inr_list = []

    total_dep = 0
    total_dist = 0
    total_inr = 0

    for r in usdt_rows:

        user, typ, amount, time = r

        if typ == "deposit":
            deposits.append(f"{time} | {user} | +{amount}U")
            total_dep += amount

        else:
            distributions.append(f"{time} | {user} | -{amount}U")
            total_dist += amount


    for r in inr_rows:

        user, amount, time = r

        sign = "+" if amount >= 0 else "-"
        inr_list.append(f"{time} | {user} | {sign}{abs(amount)} INR")

        total_inr += amount


    outstanding = (total_dep - total_dist) * rate - total_inr


    message = f"""
Deposits (Total: {len(deposits)})

""" + "\n".join(deposits) + """

----------------------

Distributions (Total: """ + str(len(distributions)) + """)

""" + "\n".join(distributions) + """

----------------------

INR Transactions (Total: """ + str(len(inr_list)) + """)

""" + "\n".join(inr_list) + f"""

Total deposits: {total_dep} U
Total distributions: {total_dist} U

INR Balance: ₹{total_inr}

Outstanding: ₹{outstanding}
"""

    return message


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):

    text = update.message.text.lower().strip()
    user = update.message.from_user.first_name
    chat = str(update.message.chat_id)

    time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    clean = text.replace(" ", "")

    if "inr" in clean:

        value = clean.replace("inr","")

        sign = 1

        if value.startswith("-"):
            sign = -1
            value = value[1:]

        elif value.startswith("+"):
            value = value[1:]

        if value.isdigit():

            amount = float(value) * sign

            cursor.execute(
                "INSERT INTO inr(chat_id,user,amount,time) VALUES(?,?,?,?)",
                (chat,user,amount,time)
            )

            conn.commit()

            await update.message.reply_text(build_report(chat))

        return


    if clean.startswith("+"):

        amount = clean.replace("+","").replace("u","")

        if amount.isdigit():

            amount = float(amount)

            cursor.execute(
                "INSERT INTO usdt(chat_id,user,type,amount,time) VALUES(?,?,?,?,?)",
                (chat,user,"deposit",amount,time)
            )

            conn.commit()

            await update.message.reply_text(build_report(chat))

        return


    if clean.startswith("-"):

        amount = clean.replace("-","").replace("u","")

        if amount.isdigit():

            amount = float(amount)

            cursor.execute(
                "INSERT INTO usdt(chat_id,user,type,amount,time) VALUES(?,?,?,?,?)",
                (chat,user,"distribution",amount,time)
            )

            conn.commit()

            await update.message.reply_text(build_report(chat))

        return


async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):

    chat = str(update.message.chat_id)

    await update.message.reply_text(build_report(chat))


app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("balance", balance))
app.add_handler(CommandHandler("rate", set_rate))
app.add_handler(CommandHandler("clear", clear))

app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))

print("BOT RUNNING")

app.run_polling()

