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
    cur.execute("SELECT currency, amount, rate FROM ledger WHERE chat_id=%s ORDER BY id", (chat_id,))
    rows = cur.fetchall()
    total_usdt = sum(a for c, a, r in rows if c == "USDT")
    inr_as_usdt = sum(a / r for c, a, r in rows if c == "INR")
    return total_usdt - inr_as_usdt

def set_rate_db(chat_id, new_rate):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT rate FROM group_settings WHERE chat_id=%s", (chat_id,))
    row = cur.fetchone()
    old_rate = row[0] if row else 100.0

    if old_rate != new_rate:
        pending_usdt = calculate_current_pending_usdt(cur, chat_id)
        if abs(pending_usdt) > 0.001:
            cur.execute("""
                INSERT INTO carried_pending (chat_id, pending_usdt, from_rate)
                VALUES (%s, %s, %s)
            """, (chat_id, pending_usdt, old_rate))
        cur.execute("DELETE FROM ledger WHERE chat_id=%s", (chat_id,))

    cur.execute("""
        INSERT INTO group_settings (chat_id, rate) VALUES (%s, %s)
        ON CONFLICT (chat_id) DO UPDATE SET rate = EXCLUDED.rate
    """, (chat_id, new_rate))

    conn.commit()
    conn.close()
    return old_rate

async def is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if update.message.chat.type == "private":
            return True
        member = await context.bot.get_chat_member(
            update.message.chat.id,
            update.message.from_user.id
        )
        return member.status in ["administrator", "creator"]
    except Exception as e:
        print(f"[ADMIN CHECK ERROR] {e}")
        return False

def get_user_display(update: Update):
    user = update.message.from_user
    return user.username or user.first_name or "Unknown"

def get_total_carried_usdt(cur, chat_id):
    cur.execute("SELECT COALESCE(SUM(pending_usdt), 0) FROM carried_pending WHERE chat_id=%s", (chat_id,))
    return cur.fetchone()[0]

def get_carried_entries(cur, chat_id):
    cur.execute("SELECT pending_usdt, from_rate FROM carried_pending WHERE chat_id=%s ORDER BY id", (chat_id,))
    return cur.fetchall()

def get_full_summary(chat_id, rate):
    conn = get_conn()
    cur = conn.cursor()

    carried_usdt = get_total_carried_usdt(cur, chat_id)
    carried_entries = get_carried_entries(cur, chat_id)

    cur.execute("SELECT currency, amount, rate FROM ledger WHERE chat_id=%s", (chat_id,))
    rows = cur.fetchall()

    total_usdt = sum(a for c, a, r in rows if c == "USDT")
    total_inr = sum(a for c, a, r in rows if c == "INR")
    inr_as_usdt = sum(a / r for c, a, r in rows if c == "INR")
    current_pending_usdt = total_usdt - inr_as_usdt

    grand_pending_usdt = carried_usdt + current_pending_usdt
    grand_pending_inr = grand_pending_usdt * rate

    cur.execute("""
        SELECT user_name, currency, amount, rate FROM ledger
        WHERE chat_id=%s ORDER BY id
    """, (chat_id,))
    ledger_rows = cur.fetchall()

    conn.close()

    return {
        "total_usdt": total_usdt,
        "total_inr": total_inr,
        "usdt_value_inr": total_usdt * rate,
        "carried_usdt": carried_usdt,
        "carried_entries": carried_entries,
        "grand_pending_usdt": grand_pending_usdt,
        "grand_pending_inr": grand_pending_inr,
        "ledger_rows": ledger_rows,
        "rate": rate,
    }

# ================= FORMAT ================= #

def format_status(gp_usdt):
    if gp_usdt > 0.001:
        return "🔴 Pending", gp_usdt
    elif gp_usdt < -0.001:
        return "🟡 Overpaid", abs(gp_usdt)
    else:
        return "🟢 Settled", 0

def build_ledger_text(chat_id, rate):
    s = get_full_summary(chat_id, rate)
    today = now_ist().strftime('%d %b %Y')

    usdt_entries = [(u, a, r) for u, c, a, r in s["ledger_rows"] if c == "USDT"]
    inr_entries = [(u, a, r) for u, c, a, r in s["ledger_rows"] if c == "INR"]

    t = ""
    t += f"✦━━━━━━━━━━━━━━━━━━━━━━━━━✦\n"
    t += f"      📊  {BOT_NAME} LEDGER\n"
    t += f"      🗓  {today}\n"
    t += f"✦━━━━━━━━━━━━━━━━━━━━━━━━━✦\n"

    if s["carried_entries"]:
        t += f"\n  📌  𝗖𝗔𝗥𝗥𝗜𝗘𝗗 𝗙𝗢𝗥𝗪𝗔𝗥𝗗\n\n"
        for i, (usdt_p, from_r) in enumerate(s["carried_entries"], 1):
            if usdt_p > 0:
                t += f"   {i}.  💵 {usdt_p:,.2f} U pending\n"
                t += f"        ↳ from ₹{from_r}\n"
            else:
                t += f"   {i}.  💰 {abs(usdt_p):,.2f} U overpaid\n"
                t += f"        ↳ from ₹{from_r}\n"
        t += f"\n  ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─\n"

    if usdt_entries:
        t += f"\n  💵  𝗨𝗦𝗗𝗧\n\n"
        for i, (user, amount, _) in enumerate(usdt_entries, 1):
            if amount >= 0:
                t += f"   {i}.  ＋ {amount:,.2f} U  →  {user}\n"
            else:
                t += f"   {i}.  ﹣ {abs(amount):,.2f} U  →  {user}\n"
        t += f"\n  ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─\n"

    if inr_entries:
        t += f"\n  💰  𝗜𝗡𝗥\n\n"
        for i, (user, amount, _) in enumerate(inr_entries, 1):
            if amount >= 0:
                t += f"   {i}.  ＋ ₹{amount:,.0f}  →  {user}\n"
            else:
                t += f"   {i}.  ﹣ ₹{abs(amount):,.0f}  →  {user}\n"
        t += f"\n  ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─\n"

    if not s["ledger_rows"] and not s["carried_entries"]:
        t += f"\n  📭 No entries yet\n"
        t += f"\n  ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─\n"

    status, display_usdt = format_status(s["grand_pending_usdt"])
    display_inr = display_usdt * rate

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
    t += f"      ⚡ {BOT_NAME} Ledger\n"
    t += f"✦━━━━━━━━━━━━━━━━━━━━━━━━━✦"
    return t

def build_balance_text(chat_id, rate):
    s = get_full_summary(chat_id, rate)
    today = now_ist().strftime('%d %b %Y')

    status, display_usdt = format_status(s["grand_pending_usdt"])
    display_inr = display_usdt * rate

    t = ""
    t += f"✦━━━━━━━━━━━━━━━━━━━━━━━━━✦\n"
    t += f"      📊  {BOT_NAME} BALANCE\n"
    t += f"      🗓  {today}\n"
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
    t += f"      ⚡ {BOT_NAME} Ledger\n"
    t += f"✦━━━━━━━━━━━━━━━━━━━━━━━━━✦"
    return t

def build_entries_text(chat_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT user_name, currency, amount, rate, created_at
        FROM ledger WHERE chat_id=%s ORDER BY id
    """, (chat_id,))
    rows = cur.fetchall()
    conn.close()

    today = now_ist().strftime('%d %b %Y')
    if not rows:
        return f"📭 No entries yet"

    t = f"✦━━━━━━━━━━━━━━━━━━━━━━━━━✦\n"
    t += f"      📝  {BOT_NAME} ENTRIES\n"
    t += f"      🗓  {today}\n"
    t += f"✦━━━━━━━━━━━━━━━━━━━━━━━━━✦\n\n"

    for i, (user, currency, amount, rate, created) in enumerate(rows, 1):
        if currency == "USDT":
            sym = "＋" if amount >= 0 else "﹣"
            t += f"  {i}. {sym} 💵 {abs(amount):,.2f} U @ ₹{rate}\n"
        else:
            sym = "＋" if amount >= 0 else "﹣"
            t += f"  {i}. {sym} 💰 ₹{abs(amount):,.0f}\n"
        t += f"      {user}  ·  {created.strftime('%d %b %H:%M')}\n\n"

    t += f"✦━━━━━━━━━━━━━━━━━━━━━━━━━✦\n"
    t += f"      ⚡ {BOT_NAME} Ledger\n"
    t += f"✦━━━━━━━━━━━━━━━━━━━━━━━━━✦"
    return t

def build_total_text(chat_id, rate):
    s = get_full_summary(chat_id, rate)
    status, display_usdt = format_status(s["grand_pending_usdt"])
    display_inr = display_usdt * rate

    t = f"📊 {BOT_NAME} TOTAL\n\n"
    t += f"  💵 USDT  :  {s['total_usdt']:,.2f} U\n"
    t += f"  💰 INR    :  ₹{s['total_inr']:,.0f}\n"
    t += f"  💱 Rate   :  ₹{rate}\n\n"
    t += f"  🔄 Pending :  {display_usdt:,.2f} U  ≈  ₹{display_inr:,.0f}\n"
    t += f"  Status : {status}"
    return t

# ================= COMMAND HANDLERS ================= #

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.type == "private":
        await update.message.reply_text(
f"""
✦━━━━━━━━━━━━━━━━━━━━━━━━━✦
       ⚡  {BOT_NAME}  ⚡
✦━━━━━━━━━━━━━━━━━━━━━━━━━✦

  📋  𝗖𝗢𝗠𝗠𝗔𝗡𝗗𝗦

  /rate ‹amt›    ─  💱 Set rate
  /ledger          ─  📒 Full ledger
  /balance        ─  📊 Balance
  /entries         ─  📝 All entries
  /total             ─  📊 Quick total
  /undo            ─  ↩️ Remove last
  /clear            ─  🗑 Clear all

  💱  𝗘𝗡𝗧𝗥𝗜𝗘𝗦

  5000u     ➜  ＋5000 USDT 💵
  89000     ➜  ＋₹89,000 💰
  -2000u   ➜  ﹣2000 USDT 🔻
  -50000   ➜  ﹣₹50,000 🔻

✦━━━━━━━━━━━━━━━━━━━━━━━━━✦"""
        )
        return
    await update.message.reply_text(f"⚡ {BOT_NAME} 𝗔𝗖𝗧𝗜𝗩𝗘\n\n💱 /rate 95\n📒 /ledger\n📊 /balance")

async def cmd_rate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        await update.message.reply_text("🔒 Admin only")
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
        old_rate = set_rate_db(chat_id, new_rate)
        if old_rate != new_rate:
            await update.message.reply_text(f"✅ 𝗥𝗮𝘁𝗲 𝗨𝗽𝗱𝗮𝘁𝗲𝗱\n\n   ₹{old_rate}  ➜  ₹{new_rate}")
        else:
            await update.message.reply_text(f"💱 Rate is already ₹{new_rate}")
    except (ValueError, IndexError):
        await update.message.reply_text("❌ Invalid. Usage ➜ /rate 95")

async def cmd_ledger(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.message.chat.id)
    rate = get_rate(chat_id)
    await update.message.reply_text(build_ledger_text(chat_id, rate))

async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.message.chat.id)
    rate = get_rate(chat_id)
    await update.message.reply_text(build_balance_text(chat_id, rate))

async def cmd_entries(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.message.chat.id)
    await update.message.reply_text(build_entries_text(chat_id))

async def cmd_total(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.message.chat.id)
    rate = get_rate(chat_id)
    await update.message.reply_text(build_total_text(chat_id, rate))

async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        await update.message.reply_text("🔒 Admin only")
        return
    chat_id = str(update.message.chat.id)
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM ledger WHERE chat_id=%s", (chat_id,))
    cur.execute("DELETE FROM carried_pending WHERE chat_id=%s", (chat_id,))
    conn.commit()
    conn.close()
    await update.message.reply_text("🗑 𝗟𝗲𝗱𝗴𝗲𝗿 𝗖𝗹𝗲𝗮𝗿𝗲𝗱")

async def cmd_undo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        await update.message.reply_text("🔒 Admin only")
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

# ================= TEXT HANDLER (entries + fallback commands) ================= #

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles ALL text messages:
    1. If it looks like a command (/ledger, /balance etc) → run command
    2. If it looks like a transaction (+5000u, -89000) → add entry
    3. Otherwise → ignore
    """
    if update.message is None or update.message.text is None:
        return

    text = update.message.text.strip()
    chat_id = str(update.message.chat.id)

    # ── FALLBACK COMMAND HANDLING ──
    # This catches commands that CommandHandler might miss in groups
    text_lower = text.lower().split("@")[0]  # Remove @botname suffix

    if text_lower == "/ledger":
        await cmd_ledger(update, context)
        return
    elif text_lower == "/balance":
        await cmd_balance(update, context)
        return
    elif text_lower == "/entries":
        await cmd_entries(update, context)
        return
    elif text_lower == "/total":
        await cmd_total(update, context)
        return
    elif text_lower == "/accounts":
        await cmd_balance(update, context)
        return
    elif text_lower == "/clear":
        await cmd_clear(update, context)
        return
    elif text_lower == "/undo":
        await cmd_undo(update, context)
        return
    elif text_lower.startswith("/rate"):
        # Extract rate value from text
        parts = text.split()
        if len(parts) > 1:
            context.args = parts[1:]
        else:
            context.args = []
        await cmd_rate(update, context)
        return
    elif text_lower.startswith("/"):
        # Unknown command — ignore
        return

    # ── TRANSACTION HANDLING (admin only) ──
    if update.message.chat.type == "private":
        return
    if not await is_admin(update, context):
        return

    user = get_user_display(update)
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

    # Currency detection — check remaining text after removing numbers/symbols
    remaining = re.sub(r"[\d,\.\+\-\s]", "", text.lower())

    if remaining in ["u", "usdt", "usd"] or remaining.endswith("u") or remaining.startswith("u"):
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

    if currency == "USDT":
        if amount > 0:
            await update.message.reply_text(f"✅  💵  ＋ {amount:,.2f} U  added @ ₹{rate}")
        else:
            await update.message.reply_text(f"🔻  💵  ﹣ {abs(amount):,.2f} U  deducted @ ₹{rate}")
    else:
        if amount > 0:
            await update.message.reply_text(f"✅  💰  ＋ ₹{amount:,.0f}  added")
        else:
            await update.message.reply_text(f"🔻  💰  ﹣ ₹{abs(amount):,.0f}  deducted")

# ================= MAIN ================= #

def main():
    init_db()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Command handlers (primary)
    commands = {
        "start": cmd_start,
        "rate": cmd_rate,
        "ledger": cmd_ledger,
        "balance": cmd_balance,
        "entries": cmd_entries,
        "total": cmd_total,
        "accounts": cmd_balance,
        "clear": cmd_clear,
        "undo": cmd_undo,
    }

    for cmd, handler in commands.items():
        app.add_handler(CommandHandler(cmd, handler))

    # Text handler — catches EVERYTHING (transactions + fallback commands)
    # This is the key fix: if CommandHandler misses a command in a group,
    # this MessageHandler will catch it as text and route it properly
    app.add_handler(MessageHandler(filters.TEXT, handle_message))

    print(f"⚡ {BOT_NAME} BOT RUNNING")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
