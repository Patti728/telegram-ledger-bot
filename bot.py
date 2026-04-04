import os
import psycopg2
import re
from datetime import datetime, timezone, timedelta

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

# ================= CONFIG ================= #

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
BOT_NAME = os.getenv("BOT_NAME", "PAYUTECH")

# IST (UTC+5:30)
IST = timezone(timedelta(hours=5, minutes=30))
def now_ist():
    return datetime.now(IST)

# ================= DB ================= #

def get_conn():
    return psycopg2.connect(DATABASE_URL, sslmode="require")

def init_db():
    conn = get_conn()
    cur = conn.cursor()

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

    cur.execute("""
    CREATE TABLE IF NOT EXISTS group_settings (
        chat_id TEXT PRIMARY KEY,
        rate FLOAT NOT NULL DEFAULT 100
    )
    """)

    # ── SPLIT LOGIC ──
    # Stores USDT pending when rate changes
    # positive = USDT is owed (someone needs to pay INR)
    # negative = USDT was overpaid (someone paid extra INR)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS carried_pending (
        id SERIAL PRIMARY KEY,
        chat_id TEXT NOT NULL,
        pending_usdt FLOAT NOT NULL DEFAULT 0,
        from_rate FLOAT NOT NULL,
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

def calculate_current_pending_usdt(cur, chat_id):
    """
    From current ledger entries, calculate net USDT pending.
    
    Logic:
      total USDT received → this much USDT needs to be paid for in INR
      total INR paid → convert to USDT at each entry's own rate
      pending_usdt = total_usdt - (sum of INR/rate for each INR entry)
    
    positive = USDT still owed (need more INR)
    negative = overpaid (paid more INR than USDT value)
    """
    cur.execute("""
        SELECT currency, amount, rate FROM ledger WHERE chat_id=%s ORDER BY id
    """, (chat_id,))
    rows = cur.fetchall()

    total_usdt = 0.0
    inr_as_usdt = 0.0

    for currency, amount, rate in rows:
        if currency == "USDT":
            total_usdt += amount
        else:
            # Convert INR to USDT at the rate it was entered
            inr_as_usdt += (amount / rate)

    pending_usdt = total_usdt - inr_as_usdt
    return pending_usdt, total_usdt, inr_as_usdt

def set_rate_db(chat_id, new_rate):
    """
    SPLIT LOGIC:
    1. Calculate current pending as USDT
    2. Store it in carried_pending
    3. Clear current ledger entries
    4. Update rate
    """
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT rate FROM group_settings WHERE chat_id=%s", (chat_id,))
    row = cur.fetchone()
    old_rate = row[0] if row else 100.0

    pending_usdt_snapshot = 0.0

    if old_rate != new_rate:
        pending_usdt, _, _ = calculate_current_pending_usdt(cur, chat_id)

        if abs(pending_usdt) > 0.001:
            # Store as USDT (not INR!)
            cur.execute("""
                INSERT INTO carried_pending (chat_id, pending_usdt, from_rate)
                VALUES (%s, %s, %s)
            """, (chat_id, pending_usdt, old_rate))
            pending_usdt_snapshot = pending_usdt

        # Clear current entries — now captured in carried_pending
        cur.execute("DELETE FROM ledger WHERE chat_id=%s", (chat_id,))

    cur.execute("""
        INSERT INTO group_settings (chat_id, rate) VALUES (%s, %s)
        ON CONFLICT (chat_id) DO UPDATE SET rate = EXCLUDED.rate
    """, (chat_id, new_rate))

    conn.commit()
    conn.close()
    return old_rate, pending_usdt_snapshot

def get_total_carried_usdt(cur, chat_id):
    """Sum all carried forward USDT pending."""
    cur.execute("""
        SELECT COALESCE(SUM(pending_usdt), 0) FROM carried_pending WHERE chat_id=%s
    """, (chat_id,))
    return cur.fetchone()[0]

def get_carried_entries(cur, chat_id):
    """Get individual carried forward entries for display."""
    cur.execute("""
        SELECT pending_usdt, from_rate FROM carried_pending
        WHERE chat_id=%s ORDER BY id
    """, (chat_id,))
    return cur.fetchall()

def get_full_summary(chat_id, rate):
    """
    Calculate everything needed for balance/ledger display.
    
    Total USDT pending = carried_usdt + current_pending_usdt
    INR equivalent = total_usdt_pending × current_rate
    """
    conn = get_conn()
    cur = conn.cursor()

    # Carried forward (as USDT)
    carried_usdt = get_total_carried_usdt(cur, chat_id)

    # Current entries
    cur.execute("SELECT currency, amount, rate FROM ledger WHERE chat_id=%s", (chat_id,))
    rows = cur.fetchall()

    total_usdt_in = sum(a for c, a, r in rows if c == "USDT")
    total_inr_in = sum(a for c, a, r in rows if c == "INR")

    # Current pending in USDT terms
    inr_as_usdt = sum(a / r for c, a, r in rows if c == "INR")
    current_pending_usdt = total_usdt_in - inr_as_usdt

    # Grand total
    grand_pending_usdt = carried_usdt + current_pending_usdt
    grand_pending_inr = grand_pending_usdt * rate

    # Carried entries for display
    carried_entries = get_carried_entries(cur, chat_id)

    # All ledger entries for ledger display
    cur.execute("""
        SELECT user_name, currency, amount, rate FROM ledger
        WHERE chat_id=%s ORDER BY id
    """, (chat_id,))
    ledger_rows = cur.fetchall()

    conn.close()

    return {
        "total_usdt": total_usdt_in,
        "total_inr": total_inr_in,
        "usdt_value_inr": total_usdt_in * rate,
        "carried_usdt": carried_usdt,
        "carried_entries": carried_entries,
        "current_pending_usdt": current_pending_usdt,
        "grand_pending_usdt": grand_pending_usdt,
        "grand_pending_inr": grand_pending_inr,
        "ledger_rows": ledger_rows,
        "rate": rate,
    }

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

# ================= COMMANDS ================= #

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.type == "private":
        await update.message.reply_text(
f"""
✦ ━━━━━━━━━━━━━━━━━━━━━ ✦
      ⚡  {BOT_NAME}  ⚡
       FINTECH  LEDGER
✦ ━━━━━━━━━━━━━━━━━━━━━ ✦

  📋  𝗖𝗢𝗠𝗠𝗔𝗡𝗗𝗦

  /rate ‹amt›    ─  💱 Set rate
  /ledger          ─  📒 Full ledger
  /balance        ─  📊 Quick balance
  /undo            ─  ↩️ Remove last
  /clear            ─  🗑 Clear all

  💱  𝗘𝗡𝗧𝗥𝗜𝗘𝗦

  5000u     ➜  ＋5000 USDT 💵
  89000     ➜  ＋₹89,000 💰
  -2000u   ➜  ﹣2000 USDT 🔻
  -50000   ➜  ﹣₹50,000 🔻

✦ ━━━━━━━━━━━━━━━━━━━━━ ✦
  👉 Add me to a group
  👉 Make me admin
  👉 Type /start to activate
✦ ━━━━━━━━━━━━━━━━━━━━━ ✦"""
        )
        return
    if not await is_admin(update, context):
        return
    await update.message.reply_text(
f"""⚡ {BOT_NAME} 𝗔𝗖𝗧𝗜𝗩𝗘

💱 Set rate ➜ /rate 95
📒 View ledger ➜ /ledger"""
    )

# ── RATE ──

async def rate_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return
    chat_id = str(update.message.chat.id)

    if not context.args:
        current = get_rate(chat_id)
        await update.message.reply_text(f"💱 Current Rate : ₹{current}\n\nUsage ➜ /rate 95")
        return

    try:
        new_rate = float(context.args[0])
        if new_rate <= 0:
            raise ValueError
        old_rate, pending_snapshot = set_rate_db(chat_id, new_rate)

        if old_rate != new_rate:
            msg = f"✅ 𝗥𝗮𝘁𝗲 𝗨𝗽𝗱𝗮𝘁𝗲𝗱\n\n"
            msg += f"   ₹{old_rate}  ➜  ₹{new_rate}"
            await update.message.reply_text(msg)
        else:
            await update.message.reply_text(f"💱 Rate is already ₹{new_rate}")

    except (ValueError, IndexError):
        await update.message.reply_text("❌ Invalid. Usage ➜ /rate 95")

# ── CLEAR ──

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

    await update.message.reply_text("🗑 𝗟𝗲𝗱𝗴𝗲𝗿 𝗖𝗹𝗲𝗮𝗿𝗲𝗱\n\nAll entries & carried pending removed.")

# ── UNDO ──

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
        await update.message.reply_text(f"↩️ Removed  💵 {amount:.2f} U  ·  {user_name}")
    else:
        await update.message.reply_text(f"↩️ Removed  💰 ₹{abs(amount):,.0f}  ·  {user_name}")

# ================= LEDGER (PREMIUM UI) ================= #

async def ledger(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return

    chat_id = str(update.message.chat.id)
    rate = get_rate(chat_id)
    s = get_full_summary(chat_id, rate)
    today = now_ist().strftime('%d %b %Y')

    usdt_entries = [(u, a, r) for u, c, a, r in s["ledger_rows"] if c == "USDT"]
    inr_entries = [(u, a, r) for u, c, a, r in s["ledger_rows"] if c == "INR"]

    t = ""
    t += f"✦━━━━━━━━━━━━━━━━━━━━━━━━━✦\n"
    t += f"     📊  {BOT_NAME} LEDGER\n"
    t += f"     🗓  {today}\n"
    t += f"✦━━━━━━━━━━━━━━━━━━━━━━━━━✦\n"

    # ── CARRIED FORWARD ──
    if s["carried_entries"]:
        t += f"\n  📌  𝗖𝗔𝗥𝗥𝗜𝗘𝗗 𝗙𝗢𝗥𝗪𝗔𝗥𝗗\n\n"
        for i, (usdt_pending, from_rate) in enumerate(s["carried_entries"], 1):
            if usdt_pending > 0:
                t += f"   {i}.  💵 {usdt_pending:,.2f} U pending\n"
                t += f"        ↳ from rate ₹{from_rate}\n"
            else:
                t += f"   {i}.  💰 {abs(usdt_pending):,.2f} U overpaid\n"
                t += f"        ↳ from rate ₹{from_rate}\n"
        t += f"\n  ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─\n"

    # ── USDT ──
    if usdt_entries:
        t += f"\n  💵  𝗨𝗦𝗗𝗧\n\n"
        for i, (user, amount, entry_rate) in enumerate(usdt_entries, 1):
            if amount >= 0:
                t += f"   {i}.  ＋ {amount:,.2f} U  →  {user}\n"
            else:
                t += f"   {i}.  ﹣ {abs(amount):,.2f} U  →  {user}\n"
        t += f"\n  ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─\n"

    # ── INR ──
    if inr_entries:
        t += f"\n  💰  𝗜𝗡𝗥\n\n"
        for i, (user, amount, entry_rate) in enumerate(inr_entries, 1):
            if amount >= 0:
                t += f"   {i}.  ＋ ₹{amount:,.0f}  →  {user}\n"
            else:
                t += f"   {i}.  ﹣ ₹{abs(amount):,.0f}  →  {user}\n"
        t += f"\n  ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─\n"

    if not s["ledger_rows"] and not s["carried_entries"]:
        t += f"\n  📭 No entries yet\n"
        t += f"\n  ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─\n"

    # ── SUMMARY ──
    gp_usdt = s["grand_pending_usdt"]
    gp_inr = s["grand_pending_inr"]

    if gp_usdt > 0.001:
        status = "🔴 Pending"
        display_usdt = gp_usdt
        display_inr = gp_inr
    elif gp_usdt < -0.001:
        status = "🟡 Overpaid"
        display_usdt = abs(gp_usdt)
        display_inr = abs(gp_inr)
    else:
        status = "🟢 Settled"
        display_usdt = 0
        display_inr = 0

    t += f"\n  📈  𝗦𝗨𝗠𝗠𝗔𝗥𝗬\n\n"
    t += f"   💵 USDT          :  {s['total_usdt']:,.2f} U\n"
    t += f"   💰 INR             :  ₹{s['total_inr']:,.0f}\n"
    if abs(s["carried_usdt"]) > 0.001:
        t += f"   📌 Carried       :  {s['carried_usdt']:,.2f} U\n"
    t += f"\n   💱 Rate            :  ₹{rate}\n"
    t += f"   💵 Value          :  ₹{s['usdt_value_inr']:,.0f}\n"
    t += f"\n   🏦 INR Pending  :  ₹{display_inr:,.0f}\n"
    t += f"   🔄 USDT Pending :  {display_usdt:,.2f} U\n"
    t += f"\n   Status : {status}\n"
    t += f"\n✦━━━━━━━━━━━━━━━━━━━━━━━━━✦\n"
    t += f"     ⚡ {BOT_NAME} Ledger\n"
    t += f"✦━━━━━━━━━━━━━━━━━━━━━━━━━✦"

    await update.message.reply_text(t)

# ================= BALANCE ================= #

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return

    chat_id = str(update.message.chat.id)
    rate = get_rate(chat_id)
    s = get_full_summary(chat_id, rate)
    today = now_ist().strftime('%d %b %Y')

    gp_usdt = s["grand_pending_usdt"]
    gp_inr = s["grand_pending_inr"]

    if gp_usdt > 0.001:
        status = "🔴 Pending"
        display_usdt = gp_usdt
        display_inr = gp_inr
    elif gp_usdt < -0.001:
        status = "🟡 Overpaid"
        display_usdt = abs(gp_usdt)
        display_inr = abs(gp_inr)
    else:
        status = "🟢 Settled"
        display_usdt = 0
        display_inr = 0

    t = ""
    t += f"✦━━━━━━━━━━━━━━━━━━━━━━━━━✦\n"
    t += f"     📊  {BOT_NAME} BALANCE\n"
    t += f"     🗓  {today}\n"
    t += f"✦━━━━━━━━━━━━━━━━━━━━━━━━━✦\n\n"
    t += f"   💵 USDT Total     :  {s['total_usdt']:,.2f} U\n"
    t += f"   💰 INR Total       :  ₹{s['total_inr']:,.0f}\n"
    if abs(s["carried_usdt"]) > 0.001:
        t += f"   📌 Carried Fwd   :  {s['carried_usdt']:,.2f} U\n"
    t += f"\n   💱 Rate              :  ₹{rate}\n"
    t += f"   💵 USDT Value    :  ₹{s['usdt_value_inr']:,.0f}\n"
    t += f"\n   🏦 INR Pending   :  ₹{display_inr:,.0f}\n"
    t += f"   🔄 USDT Pending :  {display_usdt:,.2f} U\n"
    t += f"\n   Status : {status}\n"
    t += f"\n✦━━━━━━━━━━━━━━━━━━━━━━━━━✦\n"
    t += f"     ⚡ {BOT_NAME} Ledger\n"
    t += f"✦━━━━━━━━━━━━━━━━━━━━━━━━━✦"

    await update.message.reply_text(t)

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

    is_negative = text.startswith("-")
    sign = -1 if is_negative else 1

    match = re.findall(r"[\d,\.]+", text)
    if not match:
        return

    try:
        amount = float(match[0].replace(",", "")) * sign
    except ValueError:
        return

    if amount == 0:
        return

    text_clean = text.lower().replace(" ", "")
    if "u" in text_clean:
        currency = "USDT"
    else:
        currency = "INR"

    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO ledger (chat_id, user_name, currency, amount, rate) VALUES (%s,%s,%s,%s,%s)",
        (chat_id, user, currency, amount, rate)
    )
    conn.commit()
    conn.close()

    # ── Attractive confirmations ──
    if currency == "USDT":
        if amount > 0:
            await update.message.reply_text(f"✅  💵 ＋{amount:,.2f} U  added @ ₹{rate}")
        else:
            await update.message.reply_text(f"🔻  💵 ﹣{abs(amount):,.2f} U  deducted @ ₹{rate}")
    else:
        if amount > 0:
            await update.message.reply_text(f"✅  💰 ＋₹{amount:,.0f}  added")
        else:
            await update.message.reply_text(f"🔻  💰 ﹣₹{abs(amount):,.0f}  deducted")

# ================= MAIN ================= #

def main():
    init_db()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

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
