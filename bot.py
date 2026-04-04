import os
import psycopg2
import re
from datetime import datetime, timezone, timedelta

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

# ================= CONFIG ================= #

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
BOT_NAME = os.getenv("BOT_NAME", "LYNPIK PAY")

# IST Timezone (UTC+5:30)
IST = timezone(timedelta(hours=5, minutes=30))

def now_ist():
    return datetime.now(IST)

# ================= DB ================= #

def get_conn():
    return psycopg2.connect(DATABASE_URL, sslmode="require")

def init_db():
    conn = get_conn()
    cur = conn.cursor()

    # Main ledger — each entry stores the rate it was entered at
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

    # Per-group rate
    cur.execute("""
    CREATE TABLE IF NOT EXISTS group_settings (
        chat_id TEXT PRIMARY KEY,
        rate FLOAT NOT NULL DEFAULT 100
    )
    """)

    # ── SPLIT LOGIC TABLE ──
    # When rate changes, current pending is frozen and stored here
    cur.execute("""
    CREATE TABLE IF NOT EXISTS carried_pending (
        id SERIAL PRIMARY KEY,
        chat_id TEXT NOT NULL,
        pending_inr FLOAT NOT NULL DEFAULT 0,
        old_rate FLOAT NOT NULL,
        new_rate FLOAT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    conn.commit()
    conn.close()

# ================= HELPERS ================= #

def get_rate(chat_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT rate FROM group_settings WHERE chat_id=%s", (chat_id,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else 100.0

def set_rate_db(chat_id, new_rate):
    """
    SPLIT LOGIC:
    1. Calculate pending at old rate
    2. Freeze it into carried_pending table
    3. Delete old-rate entries (they're now captured)
    4. Update to new rate
    """
    conn = get_conn()
    cur = conn.cursor()

    # Get old rate
    cur.execute("SELECT rate FROM group_settings WHERE chat_id=%s", (chat_id,))
    row = cur.fetchone()
    old_rate = row[0] if row else 100.0

    if old_rate != new_rate:
        # Get all current ledger entries (at any rate still in table)
        cur.execute("""
            SELECT currency, amount, rate FROM ledger
            WHERE chat_id=%s
        """, (chat_id,))
        rows = cur.fetchall()

        if rows:
            # Calculate pending: sum USDT value - sum INR
            # Each USDT entry uses ITS OWN stored rate
            usdt_value = sum(a * r for c, a, r in rows if c == "USDT")
            total_inr = sum(a for c, a, r in rows if c == "INR")
            diff = usdt_value - total_inr  # positive = INR still owed

            if diff != 0:
                # Freeze this pending amount
                cur.execute("""
                    INSERT INTO carried_pending (chat_id, pending_inr, old_rate, new_rate)
                    VALUES (%s, %s, %s, %s)
                """, (chat_id, diff, old_rate, new_rate))

            # Clear ledger entries — they're now captured in carried_pending
            cur.execute("DELETE FROM ledger WHERE chat_id=%s", (chat_id,))

    # Update rate
    cur.execute("""
        INSERT INTO group_settings (chat_id, rate) VALUES (%s, %s)
        ON CONFLICT (chat_id) DO UPDATE SET rate = EXCLUDED.rate
    """, (chat_id, new_rate))

    conn.commit()
    conn.close()
    return old_rate

async def is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        member = await context.bot.get_chat_member(
            update.message.chat.id,
            update.message.from_user.id
        )
        return member.status in ["administrator", "creator"]
    except Exception:
        return False

def get_user_display(update: Update):
    user = update.message.from_user
    return user.username or user.first_name or "Unknown"

# ================= PENDING CALCULATION ================= #

def calculate_full_pending(chat_id, rate):
    """
    Total pending = carried forward (frozen) + current entries pending
    """
    conn = get_conn()
    cur = conn.cursor()

    # 1. All carried-forward pending (stored as INR values, frozen)
    cur.execute("""
        SELECT COALESCE(SUM(pending_inr), 0) FROM carried_pending WHERE chat_id=%s
    """, (chat_id,))
    carried_inr = cur.fetchone()[0]

    # 2. Current entries
    cur.execute("SELECT currency, amount FROM ledger WHERE chat_id=%s", (chat_id,))
    rows = cur.fetchall()

    total_usdt = sum(a for c, a in rows if c == "USDT")
    total_inr = sum(a for c, a in rows if c == "INR")

    usdt_value = total_usdt * rate
    current_diff = usdt_value - total_inr

    # 3. Grand total
    total_pending_inr = carried_inr + current_diff

    conn.close()
    return total_usdt, total_inr, carried_inr, total_pending_inr

# ================= COMMANDS ================= #

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.type == "private":
        await update.message.reply_text(
f"""⚡ {BOT_NAME} — FINTECH LEDGER

┌─────────────────────────┐
│  📋  COMMANDS                        │
├─────────────────────────┤
│  /rate ‹amt›      Set rate              │
│  /ledger            Full ledger           │
│  /balance          Quick balance       │
│  /undo              Remove last          │
│  /clear              Clear all               │
└─────────────────────────┘

┌─────────────────────────┐
│  💱  ADD ENTRIES                     │
├─────────────────────────┤
│  5000u       →  +5000 USDT        │
│  89000       →  +₹89,000            │
│  -2000u     →  -2000 USDT          │
│  -50000     →  -₹50,000             │
└─────────────────────────┘

👉 Add me to a group & make me admin"""
        )
        return
    if not await is_admin(update, context):
        return
    await update.message.reply_text(f"⚡ {BOT_NAME} ACTIVE\n\n💱 Set rate with /rate 95")

async def rate_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return
    chat_id = str(update.message.chat.id)

    if not context.args:
        current = get_rate(chat_id)
        await update.message.reply_text(f"💱 Current Rate : ₹{current}\n\nUsage → /rate 95")
        return

    try:
        new_rate = float(context.args[0])
        if new_rate <= 0:
            raise ValueError
        old_rate = set_rate_db(chat_id, new_rate)

        if old_rate != new_rate:
            await update.message.reply_text(
                f"✅ Rate updated\n\n"
                f"   ₹{old_rate}  ➜  ₹{new_rate}\n\n"
                f"📌 Previous pending locked & carried forward"
            )
        else:
            await update.message.reply_text(f"💱 Rate is already ₹{new_rate}")

    except (ValueError, IndexError):
        await update.message.reply_text("❌ Invalid. Usage → /rate 95")

async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return
    chat_id = str(update.message.chat.id)

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM ledger WHERE chat_id=%s", (chat_id,))
    cur.execute("DELETE FROM carried_pending WHERE chat_id=%s", (chat_id,))
    conn.commit()
    conn.close()

    await update.message.reply_text("🗑 Ledger cleared\n\nAll entries & carried pending removed")

async def undo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return
    chat_id = str(update.message.chat.id)

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, currency, amount, user_name FROM ledger
        WHERE chat_id=%s ORDER BY id DESC LIMIT 1
    """, (chat_id,))
    row = cur.fetchone()

    if not row:
        conn.close()
        await update.message.reply_text("📭 Nothing to undo")
        return

    entry_id, currency, amount, user_name = row
    cur.execute("DELETE FROM ledger WHERE id=%s", (entry_id,))
    conn.commit()
    conn.close()

    if currency == "USDT":
        await update.message.reply_text(f"↩️ Removed {amount:.2f} U by {user_name}")
    else:
        await update.message.reply_text(f"↩️ Removed ₹{abs(amount):,.0f} by {user_name}")

# ================= LEDGER (PREMIUM UI) ================= #

async def ledger(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

    cur.execute("""
        SELECT pending_inr, old_rate FROM carried_pending
        WHERE chat_id=%s ORDER BY id
    """, (chat_id,))
    carried_rows = cur.fetchall()
    conn.close()

    today = now_ist().strftime('%d %b %Y')

    usdt_entries = [(u, a, r) for u, c, a, r in rows if c == "USDT"]
    inr_entries = [(u, a, r) for u, c, a, r in rows if c == "INR"]

    # ── BUILD MESSAGE ──

    text = f"📊 {BOT_NAME} LEDGER\n"
    text += f"🗓 {today}\n"
    text += "━━━━━━━━━━━━━━━━━━━━━━━━\n"

    # ── CARRIED FORWARD ──
    if carried_rows:
        text += "\n📌 CARRIED FORWARD\n\n"
        for i, (pending_inr, old_rate) in enumerate(carried_rows, 1):
            if pending_inr > 0:
                usdt_equiv = pending_inr / old_rate
                text += f"  {i}.  ₹{pending_inr:,.0f} pending @ ₹{old_rate}\n"
                text += f"       ≈ {usdt_equiv:.2f} U owed\n"
            elif pending_inr < 0:
                text += f"  {i}.  ₹{abs(pending_inr):,.0f} overpaid @ ₹{old_rate}\n"
        text += "\n━━━━━━━━━━━━━━━━━━━━━━━━\n"

    # ── USDT ──
    if usdt_entries:
        text += "\n💵 USDT\n\n"
        for i, (user, amount, entry_rate) in enumerate(usdt_entries, 1):
            if amount >= 0:
                text += f"  {i}.  💵 {amount:,.2f} U  →  {user}\n"
            else:
                text += f"  {i}.  🔻 -{abs(amount):,.2f} U  →  {user}\n"
        text += "\n━━━━━━━━━━━━━━━━━━━━━━━━\n"

    # ── INR ──
    if inr_entries:
        text += "\n💰 INR\n\n"
        for i, (user, amount, entry_rate) in enumerate(inr_entries, 1):
            if amount >= 0:
                text += f"  {i}.  💰 ₹{amount:,.0f}  →  {user}\n"
            else:
                text += f"  {i}.  🔻 -₹{abs(amount):,.0f}  →  {user}\n"
        text += "\n━━━━━━━━━━━━━━━━━━━━━━━━\n"

    if not rows and not carried_rows:
        text += "\n📭 No entries yet\n"
        text += "\n━━━━━━━━━━━━━━━━━━━━━━━━\n"

    # ── SUMMARY ──
    total_usdt = sum(a for _, a, _ in usdt_entries)
    total_inr = sum(a for _, a, _ in inr_entries)
    usdt_value = total_usdt * rate
    carried_inr = sum(p for p, _ in carried_rows)
    total_pending_inr = (usdt_value - total_inr) + carried_inr

    if total_pending_inr > 0:
        inr_pending = total_pending_inr
        usdt_pending = total_pending_inr / rate
        status = "🔴 Pending"
    elif total_pending_inr < 0:
        inr_pending = abs(total_pending_inr)
        usdt_pending = abs(total_pending_inr) / rate
        status = "🟡 Overpaid"
    else:
        inr_pending = 0
        usdt_pending = 0
        status = "🟢 Settled"

    text += "\n📈 SUMMARY\n\n"
    text += f"  💵 USDT            :  {total_usdt:,.2f} U\n"
    text += f"  💰 INR              :  ₹{total_inr:,.0f}\n"
    if carried_inr != 0:
        text += f"  📌 Carried         :  ₹{carried_inr:,.0f}\n"
    text += f"\n  💱 Rate             :  ₹{rate}\n"
    text += f"  💵 Value           :  ₹{usdt_value:,.0f}\n"
    text += f"\n  🏦 INR Pending  :  ₹{inr_pending:,.0f}\n"
    text += f"  🔄 USDT Pending :  {usdt_pending:.2f} U\n"
    text += f"\n  Status : {status}\n"
    text += "\n━━━━━━━━━━━━━━━━━━━━━━━━\n"
    text += f"⚡ {BOT_NAME} Fintech Ledger"

    await update.message.reply_text(text)

# ================= BALANCE ================= #

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return

    chat_id = str(update.message.chat.id)
    rate = get_rate(chat_id)

    total_usdt, total_inr, carried_inr, total_pending_inr = calculate_full_pending(chat_id, rate)
    usdt_value = total_usdt * rate

    if total_pending_inr > 0:
        inr_pending = total_pending_inr
        usdt_pending = total_pending_inr / rate
        status = "🔴 Pending"
    elif total_pending_inr < 0:
        inr_pending = abs(total_pending_inr)
        usdt_pending = abs(total_pending_inr) / rate
        status = "🟡 Overpaid"
    else:
        inr_pending = 0
        usdt_pending = 0
        status = "🟢 Settled"

    today = now_ist().strftime('%d %b %Y')

    msg = f"📊 {BOT_NAME} BALANCE\n"
    msg += f"🗓 {today}\n"
    msg += "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    msg += f"  💵 USDT Total     :  {total_usdt:,.2f} U\n"
    msg += f"  💰 INR Total       :  ₹{total_inr:,.0f}\n"
    if carried_inr != 0:
        msg += f"  📌 Carried Fwd   :  ₹{carried_inr:,.0f}\n"
    msg += f"\n  💱 Rate              :  ₹{rate}\n"
    msg += f"  💵 USDT Value    :  ₹{usdt_value:,.0f}\n"
    msg += f"\n  🏦 INR Pending   :  ₹{inr_pending:,.0f}\n"
    msg += f"  🔄 USDT Pending :  {usdt_pending:.2f} U\n"
    msg += f"\n  Status : {status}\n"
    msg += "\n━━━━━━━━━━━━━━━━━━━━━━━━\n"
    msg += f"⚡ {BOT_NAME} Fintech Ledger"

    await update.message.reply_text(msg)

# ================= TRANSACTION HANDLER ================= #

async def handle_tx(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.type == "private":
        return
    if not await is_admin(update, context):
        return

    text = update.message.text.strip()
    user = get_user_display(update)
    chat_id = str(update.message.chat.id)
    rate = get_rate(chat_id)

    # Detect sign
    is_negative = text.startswith("-")
    sign = -1 if is_negative else 1

    # Extract number (supports 4,50,000 or 450000)
    match = re.findall(r"[\d,\.]+", text)
    if not match:
        return

    try:
        amount = float(match[0].replace(",", "")) * sign
    except ValueError:
        return

    if amount == 0:
        return

    # Detect currency — "u" or "U" → USDT, else INR
    text_clean = text.lower().replace(" ", "")
    if "u" in text_clean:
        currency = "USDT"
    else:
        currency = "INR"

    # Insert
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO ledger (chat_id, user_name, currency, amount, rate) VALUES (%s,%s,%s,%s,%s)",
        (chat_id, user, currency, amount, rate)
    )
    conn.commit()
    conn.close()

    # ── Confirmation ──
    if currency == "USDT":
        if amount > 0:
            await update.message.reply_text(f"✅ +{amount:,.2f} U added @ ₹{rate}")
        else:
            await update.message.reply_text(f"🔻 -{abs(amount):,.2f} U deducted @ ₹{rate}")
    else:
        if amount > 0:
            await update.message.reply_text(f"✅ +₹{amount:,.0f} added")
        else:
            await update.message.reply_text(f"🔻 -₹{abs(amount):,.0f} deducted")

# ================= MAIN ================= #

def main():
    init_db()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Register both lowercase and uppercase commands
    for cmd, handler in [
        ("start", start),
        ("rate", rate_cmd),
        ("ledger", ledger),
        ("balance", balance),
        ("clear", clear),
        ("undo", undo),
    ]:
        app.add_handler(CommandHandler(cmd, handler))
        app.add_handler(CommandHandler(cmd.upper(), handler))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_tx))

    print(f"⚡ {BOT_NAME} BOT RUNNING")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
