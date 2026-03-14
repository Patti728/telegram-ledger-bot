import telebot
from datetime import datetime

TOKEN = "8764568325:AAFs-6ErToz0zDv7AZTubizeSx9m7UnvNp8"

bot = telebot.TeleBot(TOKEN)

rate = 0
inr_balance = 0
usdt_balance = 0

inr_transactions = []
usdt_transactions = []


def parse_amount(text):
    return float(
        text.replace(",", "")
        .replace("inr", "")
        .replace("u", "")
        .replace("+", "")
        .replace("-", "")
        .strip()
    )


def format_inr(value):
    return f"₹{value:,.2f}"


def format_usdt(value):
    return f"{value:,.2f} U"


@bot.message_handler(func=lambda message: True)
def handle(message):

    global rate, inr_balance, usdt_balance

    text = message.text.lower()
    user = message.from_user.first_name
    time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:

        # INR ADD
        if text.startswith("+") and "inr" in text:

            amount = parse_amount(text)
            inr_balance += amount

            inr_transactions.append(f"{time} | {user} | 🟢 +₹{amount:,.2f}")

            bot.reply_to(
                message,
                f"""
💰 INR CREDITED

👤 {user}
🕒 {time}

Amount: +{format_inr(amount)}

━━━━━━━━━━━━
💼 BALANCE

🇮🇳 INR : {format_inr(inr_balance)}
💵 USDT : {format_usdt(usdt_balance)}

⚡ Powered by Astryx Pay
""",
            )

        # INR DEDUCT
        elif text.startswith("-") and "inr" in text:

            amount = parse_amount(text)
            inr_balance -= amount

            inr_transactions.append(f"{time} | {user} | 🔴 -₹{amount:,.2f}")

            bot.reply_to(
                message,
                f"""
💸 INR DEBITED

👤 {user}
🕒 {time}

Amount: -{format_inr(amount)}

━━━━━━━━━━━━
💼 BALANCE

🇮🇳 INR : {format_inr(inr_balance)}
💵 USDT : {format_usdt(usdt_balance)}

⚡ Powered by Astryx Pay
""",
            )

        # USDT ADD
        elif text.startswith("+") and "u" in text:

            amount = parse_amount(text)
            usdt_balance += amount

            usdt_transactions.append(f"{time} | {user} | 🟢 +{amount}U")

            bot.reply_to(
                message,
                f"""
💎 USDT RECEIVED

👤 {user}
🕒 {time}

Amount: +{format_usdt(amount)}

━━━━━━━━━━━━
💼 BALANCE

💵 USDT : {format_usdt(usdt_balance)}
🇮🇳 INR : {format_inr(inr_balance)}

⚡ Powered by Astryx Pay
""",
            )

        # USDT SEND
        elif text.startswith("-") and "u" in text:

            amount = parse_amount(text)
            usdt_balance -= amount

            usdt_transactions.append(f"{time} | {user} | 🔴 -{amount}U")

            bot.reply_to(
                message,
                f"""
💎 USDT SENT

👤 {user}
🕒 {time}

Amount: -{format_usdt(amount)}

━━━━━━━━━━━━
💼 BALANCE

💵 USDT : {format_usdt(usdt_balance)}
🇮🇳 INR : {format_inr(inr_balance)}

⚡ Powered by Astryx Pay
""",
            )

        # SET RATE
        elif text.startswith("rate"):

            rate = float(text.split()[1])

            bot.reply_to(
                message,
                f"""
📊 RATE UPDATED

1 USDT = ₹{rate}

⚡ Powered by Astryx Pay
""",
            )

        # BALANCE
        elif text == "balance":

            usdt_value = usdt_balance * rate
            outstanding = usdt_value - inr_balance

            bot.reply_to(
                message,
                f"""
📊 ASTRYX PAY SUMMARY

💵 USDT Balance : {format_usdt(usdt_balance)}
💰 INR Balance : {format_inr(inr_balance)}

💱 Rate : ₹{rate}

💎 USDT Value : {format_inr(usdt_value)}

⚠ Outstanding : {format_inr(outstanding)}

━━━━━━━━━━━━
⚡ Powered by Astryx Pay
""",
            )

        # REPORT
        elif text == "report":

            inr_log = "\n".join(inr_transactions[-10:])
            usdt_log = "\n".join(usdt_transactions[-10:])

            bot.reply_to(
                message,
                f"""
📊 ASTRYX PAY LEDGER

━━━━━━━━━━━━

💎 USDT Transactions
{usdt_log}

━━━━━━━━━━━━

💰 INR Transactions
{inr_log}

━━━━━━━━━━━━
⚡ Powered by Astryx Pay
""",
            )

    except:
        bot.reply_to(message, "❌ Invalid command")


print("Bot Running...")
bot.infinity_polling()
