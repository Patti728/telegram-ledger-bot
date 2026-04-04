import os
import psycopg2
import re
import traceback
from datetime import datetime, timezone, timedelta

from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, ContextTypes, filters

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
        print(f"[ADMIN ERROR] {e}")
        return False

def get_user_display(update: Update):
    user = update.message.from_user
    return user.username or user.first_name or "Unknown"

def get_full_summary(chat_id, rate):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT COALESCE(SUM(pending_usdt), 0) FROM carried_pending WHERE chat_id=%s", (chat_id,))
    carried_usdt = cur.fetchone()[0]

    cur.execute("SELECT pending_usdt, from_rate FROM carried_pending WHERE chat_id=%s ORDER BY id", (chat_id,))
    carried_entries = cur.fetchall()

    cur.execute("SELECT currency, amount, rate FROM ledger WHERE chat_id=%s", (chat_id,))
    rows = cur.fetchall()

    total_usdt = sum(a for c, a, r in rows if c == "USDT")
    total_inr = sum(a for c, a, r in rows if c == "INR")
    inr_as_usdt = sum(a / r for c, a, r in rows if c == "INR")
    current_pending_usdt = total_usdt - inr_as_usdt
    grand_pending_usdt = carried_usdt + current_pending_usdt

    cur.execute("SELECT user_name, currency, amount, rate FROM ledger WHERE chat_id=%s ORDER BY id", (chat_id,))
    ledger_rows = cur.fetchall()
    conn.close()

    return {
        "total_usdt": total_usdt,
        "total_inr": total_inr,
        "usdt_value_inr": total_usdt * rate,
        "carried_usdt": carried_usdt,
        "carried_entries": carried_entries,
        "grand_pending_usdt": grand_pending_usdt,
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

    t = f"✦━━━━━━━━━━━━━━━━━━━━━━━━━✦\n"
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
            sym = "＋" if amount >= 0 else "﹣"
            t += f"   {i}.  {sym} {abs(amount):,.2f} U  →  {user}\n"
        t += f"\n  ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─\n"

    if inr_entries:
        t += f"\n  💰  𝗜𝗡𝗥\n\n"
        for i, (user, amount, _) in enumerate(inr_entries, 1):
            sym = "＋" if amount >= 0 else "﹣"
            t += f"   {i}.  {sym} ₹{abs(amount):,.0f}  →  {user}\n"
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

    t = f"✦━━━━━━━━━━━━━━━━━━━━━━━━━✦\n"
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

    if not rows:
        return "📭 No entries yet"

    today = now_ist().strftime('%d %b %Y')
    t = f"✦━━━━━━━━━━━━━━━━━━━━━━━━━✦\n"
    t += f"      📝  {BOT_NAME} ENTRIES\n"
    t += f"      🗓  {today}\n"
    t += f"✦━━━━━━━━━━━━━━━━━━━━━━━━━✦\n\n"

    for i, (user, currency, amount, rate, created) in enumerate(rows, 1):
        sym = "＋" if amount >= 0 else "﹣"
        if currency == "USDT":
            t += f"  {i}. {sym} 💵 {abs(amount):,.2f} U @ ₹{rate}\n"
        else:
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

# ================= SINGLE MESSAGE HANDLER ================= #

async def handle_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    ONE handler for EVERYTHING.
    No CommandHandlers — all routing done here.
    """
    try:
        if update.message is None or update.message.text is None:
            return

        text = update.message.text.strip()
        chat_id = str(update.message.chat.id)

        print(f"[MSG] chat={chat_id} user={update.message.from_user.first_name} text={text[:50]}")

        # ── COMMAND ROUTING ──
        # Strip @botname from commands like /ledger@payutech_bot
        cmd = text.lower().split("@")[0].split()[0] if text.startswith("/") else None

        if cmd == "/start":
            if update.message.chat.type == "private":
                await update.message.reply_text(
f"""✦━━━━━━━━━━━━━━━━━━━━━━━━━✦
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

✦━━━━━━━━━━━━━━━━━━━━━━━━━✦""")
            else:
                await update.message.reply_text(f"⚡ {BOT_NAME} 𝗔𝗖𝗧𝗜𝗩𝗘\n\n💱 /rate 95\n📒 /ledger\n📊 /balance")
            return

        elif cmd == "/ledger":
            rate = get_rate(chat_id)
            await update.message.reply_text(build_ledger_text(chat_id, rate))
            return

        elif cmd == "/balance" or cmd == "/accounts":
            rate = get_rate(chat_id)
            await update.message.reply_text(build_balance_text(chat_id, rate))
            return

        elif cmd == "/entries":
            await update.message.reply_text(build_entries_text(chat_id))
            return

        elif cmd == "/total":
            rate = get_rate(chat_id)
            await update.message.reply_text(build_total_text(chat_id, rate))
            return

        elif cmd == "/rate":
            if not await is_admin(update, context):
                await update.message.reply_text("🔒 Admin only")
                return
            parts = text.split()
            if len(parts) < 2:
                current = get_rate(chat_id)
                await update.message.reply_text(f"💱 Current Rate : ₹{current}\n\nUsage ➜ /rate 95")
                return
            try:
                new_rate = float(parts[1])
                if new_rate <= 0:
                    raise ValueError
                old_rate = set_rate_db(chat_id, new_rate)
                if old_rate != new_rate:
                    await update.message.reply_text(f"✅ 𝗥𝗮𝘁𝗲 𝗨𝗽𝗱𝗮𝘁𝗲𝗱\n\n   ₹{old_rate}  ➜  ₹{new_rate}")
                else:
                    await update.message.reply_text(f"💱 Rate is already ₹{new_rate}")
            except (ValueError, IndexError):
                await update.message.reply_text("❌ Invalid. Usage ➜ /rate 95")
            return

        elif cmd == "/clear":
            if not await is_admin(update, context):
                await update.message.reply_text("🔒 Admin only")
                return
            conn = get_conn()
            cur = conn.cursor()
            cur.execute("DELETE FROM ledger WHERE chat_id=%s", (chat_id,))
            cur.execute("DELETE FROM carried_pending WHERE chat_id=%s", (chat_id,))
            conn.commit()
            conn.close()
            await update.message.reply_text("🗑 𝗟𝗲𝗱𝗴𝗲𝗿 𝗖𝗹𝗲𝗮𝗿𝗲𝗱")
            return

        elif cmd == "/undo":
            if not await is_admin(update, context):
                await update.message.reply_text("🔒 Admin only")
                return
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
            return

        elif cmd is not None:
            # Unknown command — ignore
            return

        # ── TRANSACTION HANDLING ──
        if update.message.chat.type == "private":
            return
        if not await is_admin(update, context):
            return

        user = get_user_display(update)
        rate = get_rate(chat_id)

        sign = -1 if text.startswith("-") else 1

        match = re.findall(r"[\d,\.]+", text)
        if not match:
            return

        try:
            amount = float(match[0].replace(",", "")) * sign
        except ValueError:
            return

        if amount == 0:
            return

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

    except Exception as e:
        print(f"[ERROR] {e}")
        traceback.print_exc()

# ================= MAIN ================= #

def main():
    init_db()
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # ONE handler. No CommandHandlers. Catches everything.
    app.add_handler(MessageHandler(filters.TEXT, handle_all))

    print(f"⚡ {BOT_NAME} BOT RUNNING")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
