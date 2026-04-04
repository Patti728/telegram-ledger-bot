import os
import psycopg2
import re
from datetime import datetime

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

# ================= CONFIG ================= #

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

BOT_NAME = os.getenv("BOT_NAME", "LYNPIK PAY")  # Customize your bot branding

# ================= DB ================= #

def get_conn():
    return psycopg2.connect(DATABASE_URL, sslmode="require")

def init_db():
    conn = get_conn()
    cur = conn.cursor()

    # Main ledger table
    cur.execute("""
    CREATE TABLE IF NOT EXISTS ledger (
        id SERIAL PRIMARY KEY,
        chat_id TEXT NOT NULL,
        user_name TEXT NOT NULL,
        currency TEXT NOT NULL,
        amount FLOAT NOT NULL,
        rate FLOAT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # Per-group rate storage
    cur.execute("""
    CREATE TABLE IF NOT EXISTS group_settings (
        chat_id TEXT PRIMARY KEY,
        rate FLOAT NOT NULL DEFAULT 100
    )
    """)

    conn.commit()
    conn.close()

# ================= HELPERS ================= #

def get_rate(chat_id):
    """Get the rate for a specific group (stored in DB, not global variable)."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT rate FROM group_settings WHERE chat_id=%s", (chat_id,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else 100.0

def set_rate_db(chat_id, rate):
    """Set rate per group in DB."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO group_settings (chat_id, rate) VALUES (%s, %s)
        ON CONFLICT (chat_id) DO UPDATE SET rate = EXCLUDED.rate
    """, (chat_id, rate))
    conn.commit()
    conn.close()

async def is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check if the user is a group admin or creator."""
    try:
        member = await context.bot.get_chat_member(
            update.message.chat.id,
            update.message.from_user.id
        )
        return member.status in ["administrator", "creator"]
    except Exception:
        return False

def get_user_display(update: Update):
    """Get a display name for the user."""
    user = update.message.from_user
    if user.username:
        return user.username
    return user.first_name or "Unknown"

# ================= COMMANDS ================= #

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Activate the bot in a group."""
    if update.message.chat.type == "private":
        await update.message.reply_text(
            f"⚡ {BOT_NAME} LEDGER BOT\n\n"
            "Add me to a group and make me admin to start tracking.\n\n"
            "Commands:\n"
            "/rate <amount> - Set USDT→INR rate\n"
            "/ledger - View full ledger\n"
            "/balance - View balance summary\n"
            "/undo - Remove last entry\n"
            "/clear - Clear all entries\n\n"
            "To add entries, just type:\n"
            "• 5000u or 5000U → adds USDT\n"
            "• 89000 or 89000r → adds INR\n"
            "• -5000u → removes/reverses USDT\n"
            "• -89000 → removes/reverses INR"
        )
        return
    if not await is_admin(update, context):
        return
    await update.message.reply_text(f"⚡ {BOT_NAME} BOT ACTIVE\n\nType /rate to set exchange rate.")

async def rate_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set the USDT→INR rate for this group."""
    if not await is_admin(update, context):
        return

    chat_id = str(update.message.chat.id)

    if not context.args:
        current = get_rate(chat_id)
        await update.message.reply_text(f"💱 Current Rate: ₹{current}\n\nUse: /rate 95")
        return

    try:
        new_rate = float(context.args[0])
        if new_rate <= 0:
            raise ValueError
        set_rate_db(chat_id, new_rate)
        await update.message.reply_text(f"✅ Rate set to ₹{new_rate}")
    except (ValueError, IndexError):
        await update.message.reply_text("❌ Invalid rate. Use: /rate 95")

async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear all ledger entries for this group."""
    if not await is_admin(update, context):
        return

    chat_id = str(update.message.chat.id)

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM ledger WHERE chat_id=%s", (chat_id,))
    conn.commit()
    conn.close()

    await update.message.reply_text("🗑 Ledger Cleared")

async def undo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove the last ledger entry."""
    if not await is_admin(update, context):
        return

    chat_id = str(update.message.chat.id)

    conn = get_conn()
    cur = conn.cursor()

    # Get last entry details before deleting
    cur.execute("""
        SELECT id, currency, amount, user_name FROM ledger
        WHERE chat_id=%s ORDER BY id DESC LIMIT 1
    """, (chat_id,))
    row = cur.fetchone()

    if not row:
        conn.close()
        await update.message.reply_text("📭 No entries to undo.")
        return

    entry_id, currency, amount, user_name = row
    cur.execute("DELETE FROM ledger WHERE id=%s", (entry_id,))
    conn.commit()
    conn.close()

    if currency == "USDT":
        await update.message.reply_text(f"↩️ Removed: {amount:.2f} U by {user_name}")
    else:
        await update.message.reply_text(f"↩️ Removed: ₹{amount:,.0f} by {user_name}")

# ================= LEDGER (MATCHING SCREENSHOT STYLE) ================= #

async def ledger(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the full ledger with USDT/INR sections and summary."""
    if not await is_admin(update, context):
        return

    chat_id = str(update.message.chat.id)
    rate = get_rate(chat_id)

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT user_name, currency, amount, rate
        FROM ledger WHERE chat_id=%s ORDER BY id
    """, (chat_id,))
    rows = cur.fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text("📭 Ledger is empty. Start adding entries!")
        return

    today = datetime.now().strftime('%d %b %Y')

    # Separate USDT and INR entries
    usdt_entries = [(u, a, r) for u, c, a, r in rows if c == "USDT"]
    inr_entries = [(u, a, r) for u, c, a, r in rows if c == "INR"]

    # Build the message (matching screenshot layout)
    text = f"📊 {BOT_NAME} LEDGER | 📅 {today}\n"
    text += "━━━━━━━━━━━━━━━━━━━━\n\n"

    # USDT Section
    if usdt_entries:
        text += "🤑 USDT\n"
        for i, (user, amount, entry_rate) in enumerate(usdt_entries, 1):
            text += f"{i}.  🤑 {amount:.2f} U → {user}\n"
        text += "\n━━━━━━━━━━━━━━━━━━━━\n\n"

    # INR Section
    if inr_entries:
        text += "💰 INR\n"
        for i, (user, amount, entry_rate) in enumerate(inr_entries, 1):
            text += f"{i}.  💰 ₹{amount:,.0f} → {user}\n"
        text += "\n━━━━━━━━━━━━━━━━━━━━\n\n"

    # Summary Section
    total_usdt = sum(a for _, a, _ in usdt_entries)
    total_inr = sum(a for _, a, _ in inr_entries)
    usdt_value_inr = total_usdt * rate

    # Pending calculation
    # Positive = more USDT value than INR paid → INR is pending
    # Negative = more INR paid than USDT value → USDT is pending
    diff = usdt_value_inr - total_inr

    if diff > 0:
        inr_pending = diff
        usdt_pending = 0.0
    elif diff < 0:
        inr_pending = 0.0
        usdt_pending = abs(diff) / rate
    else:
        inr_pending = 0.0
        usdt_pending = 0.0

    status = "🔴 Pending" if (inr_pending > 0 or usdt_pending > 0) else "🟢 Balanced"

    text += "📈 SUMMARY\n\n"
    text += f"🤑 USDT           : {total_usdt:,.2f} U\n"
    text += f"💰 INR             : ₹{total_inr:,.0f}\n\n"
    text += f"💱 Rate            : ₹{rate}\n"
    text += f"🤑 Value           : ₹{usdt_value_inr:,.0f}\n\n"
    text += f"🏦 INR Pending   : ₹{inr_pending:,.0f}\n"
    text += f"🔄 USDT Pending : {usdt_pending:.2f} U\n\n"
    text += f"Status : {status}\n"
    text += "━━━━━━━━━━━━━━━━━━━━\n"
    text += f"⚡ {BOT_NAME} Fintech Ledger"

    await update.message.reply_text(text)

# ================= BALANCE (QUICK VIEW) ================= #

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show a quick balance summary."""
    if not await is_admin(update, context):
        return

    chat_id = str(update.message.chat.id)
    rate = get_rate(chat_id)

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT currency, amount FROM ledger WHERE chat_id=%s
    """, (chat_id,))
    rows = cur.fetchall()
    conn.close()

    total_usdt = sum(a for c, a in rows if c == "USDT")
    total_inr = sum(a for c, a in rows if c == "INR")
    usdt_value_inr = total_usdt * rate

    diff = usdt_value_inr - total_inr

    if diff > 0:
        inr_pending = diff
        usdt_pending = 0.0
    elif diff < 0:
        inr_pending = 0.0
        usdt_pending = abs(diff) / rate
    else:
        inr_pending = 0.0
        usdt_pending = 0.0

    status = "🔴 Pending" if (inr_pending > 0 or usdt_pending > 0) else "🟢 Balanced"

    today = datetime.now().strftime('%d %b %Y')

    await update.message.reply_text(f"""📊 {BOT_NAME} BALANCE | 📅 {today}
━━━━━━━━━━━━━━━━━━━━

🤑 USDT Total    : {total_usdt:,.2f} U
💰 INR Total      : ₹{total_inr:,.0f}

💱 Rate             : ₹{rate}
🤑 USDT Value   : ₹{usdt_value_inr:,.0f}

🏦 INR Pending   : ₹{inr_pending:,.0f}
🔄 USDT Pending : {usdt_pending:.2f} U

Status : {status}
━━━━━━━━━━━━━━━━━━━━
⚡ {BOT_NAME} Fintech Ledger""")

# ================= TRANSACTION HANDLER ================= #

async def handle_tx(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Parse natural text input to add USDT or INR entries."""
    if update.message.chat.type == "private":
        return
    if not await is_admin(update, context):
        return

    text = update.message.text.strip()
    user = get_user_display(update)
    chat_id = str(update.message.chat.id)
    rate = get_rate(chat_id)

    # Detect sign (negative = reversal)
    sign = -1 if text.startswith("-") else 1

    # Extract number (supports commas like 4,50,000)
    match = re.findall(r"[\d,\.]+", text)
    if not match:
        return

    try:
        amount = float(match[0].replace(",", "")) * sign
    except ValueError:
        return

    # Skip zero amounts
    if amount == 0:
        return

    # Detect currency: "u" or "U" anywhere → USDT, otherwise INR
    text_lower = text.lower()
    if "u" in text_lower:
        currency = "USDT"
    else:
        currency = "INR"

    # Insert into ledger
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO ledger (chat_id, user_name, currency, amount, rate) VALUES (%s,%s,%s,%s,%s)",
        (chat_id, user, currency, amount, rate)
    )
    conn.commit()
    conn.close()

    # Confirmation
    if currency == "USDT":
        emoji = "🤑" if amount > 0 else "↩️"
        await update.message.reply_text(f"{emoji} {amount:.2f} U added @ ₹{rate}")
    else:
        emoji = "💰" if amount > 0 else "↩️"
        await update.message.reply_text(f"{emoji} ₹{amount:,.0f} added")

# ================= MAIN ================= #

def main():
    init_db()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("rate", rate_cmd))
    app.add_handler(CommandHandler("ledger", ledger))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("clear", clear))
    app.add_handler(CommandHandler("undo", undo))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_tx))

    print(f"⚡ {BOT_NAME} BOT RUNNING")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
