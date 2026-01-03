# file: improved_bot.py
import os
import logging
import asyncio
import sqlite3
from typing import Tuple

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
    filters,
)

# ---------- تنظیمات ----------
# توصیه: توکن را در متغیر محیطی بگذار: export BOT_TOKEN="توکن_ربات"
# اگر اصرار داری توکن داخل کد باشه، مقدارش را اینجا قرار بده (خطر امنیتی).
TOKEN = os.getenv("7572200133:AAEDAnslQifBjVxRDwqiEcKRF1gAfca8nWE") or "7572200133:AAEDAnslQifBjVxRDwqiEcKRF1gAfca8nWE"   # <-- جایگزین کن اگر لازم شد
BOT_USERNAME = "Drop_trx_rbot"
CHANNEL_ID = "@varizitrxdrop"
REGISTER_REWARD = 0.5
INVITE_REWARD = 0.5
MIN_WITHDRAW = 5
ADMINS = [6960872391]

# ---------- لاگ ----------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# ---------- دیتابیس ----------
# check_same_thread=False چون از asyncio و لوک استفاده می‌کنیم
conn = sqlite3.connect("users.db", check_same_thread=False)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

cur.execute(
    """
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    balance REAL DEFAULT 0,
    invited_by INTEGER,
    invites INTEGER DEFAULT 0
)
"""
)
cur.execute(
    """
CREATE TABLE IF NOT EXISTS withdrawals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    wallet TEXT,
    amount REAL,
    status TEXT DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""
)
conn.commit()

# asyncio lock برای جلوگیری از دسترسی هم‌زمان از داخل async handlers
db_lock = asyncio.Lock()

# ---------- کیبورد‌ها ----------
def get_main_keyboard(user_id: int):
    buttons = [
        [KeyboardButton("💰 موجودی"), KeyboardButton("📥 برداشت")],
        [KeyboardButton("📢 لینک دعوت")],
    ]
    if user_id in ADMINS:
        buttons.append([KeyboardButton("⚙️ پنل ادمین")])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

def get_admin_keyboard():
    buttons = [
        [KeyboardButton("📊 آمار کاربران")],
        [KeyboardButton("💸 لیست برداشت‌ها")],
        [KeyboardButton("🎁 هدیه به کاربر")],
        [KeyboardButton("🔙 بازگشت")],
    ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

# ---------- Conversation states for withdraw ----------
AMOUNT, WALLET = range(2)

# ---------- دستورات ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    first_name = user.first_name or "دوست"

    # check args for inviter
    inviter_id = None
    if context.args:
        try:
            inviter_id = int(context.args[0])
        except:
            inviter_id = None

    async with db_lock:
        cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
        if cur.fetchone():
            await update.message.reply_text(
                f"🚨 {first_name} عزیز، شما قبلاً ثبت‌نام کردید.",
                reply_markup=get_main_keyboard(user_id),
            )
            return

        cur.execute(
            "INSERT INTO users (user_id, balance, invited_by) VALUES (?, ?, ?)",
            (user_id, REGISTER_REWARD, inviter_id),
        )
        if inviter_id and inviter_id != user_id:
            cur.execute(
                "UPDATE users SET balance = balance + ?, invites = invites + 1 WHERE user_id=?",
                (INVITE_REWARD, inviter_id),
            )
        conn.commit()

    if inviter_id and inviter_id != user_id:
        try:
            await context.bot.send_message(
                chat_id=inviter_id,
                text=f"🙌 شما یک نفر را دعوت کردید و {INVITE_REWARD} TRX به موجودی‌تان اضافه شد!",
            )
        except Exception as e:
            logger.info(f"Could not notify inviter {inviter_id}: {e}")

    await update.message.reply_text(
        f"🎉 سلام {first_name}! خوش اومدی 💎\n💰 همین الان {REGISTER_REWARD} TRX به حسابت اضافه شد!",
        reply_markup=get_main_keyboard(user_id),
    )

# ---------- موجودی ----------
async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    async with db_lock:
        cur.execute("SELECT balance, invites FROM users WHERE user_id=?", (user_id,))
        result = cur.fetchone()
    if result:
        balance_val, invites = result["balance"], result["invites"]
        referral_link = f"https://t.me/{BOT_USERNAME}?start={user_id}"
        await update.message.reply_text(
            f"💰 موجودی: {balance_val} TRX\n👥 تعداد دوستان دعوت‌شده: {invites}\n\n📢 لینک دعوت اختصاصی:\n{referral_link}\n\n✨ وقتی موجودیت به {MIN_WITHDRAW} TRX برسه می‌تونی برداشت بزنی 🙌",
            reply_markup=get_main_keyboard(user_id),
        )
    else:
        await update.message.reply_text(
            "❌ شما هنوز ثبت‌نام نکردید.", reply_markup=get_main_keyboard(user_id)
        )

# ---------- شروع فرآیند برداشت (Conversation) ----------
async def withdraw_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    async with db_lock:
        cur.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
        r = cur.fetchone()
    if not r:
        await update.message.reply_text(
            "❌ شما ثبت‌نام نکردید.", reply_markup=get_main_keyboard(user_id)
        )
        return ConversationHandler.END

    balance_val = r["balance"]
    if balance_val < MIN_WITHDRAW:
        await update.message.reply_text(
            f"🚨 حداقل برداشت {MIN_WITHDRAW} TRX است.\n💰 موجودی: {balance_val}",
            reply_markup=get_main_keyboard(user_id),
        )
        return ConversationHandler.END

    await update.message.reply_text(
        f"📥 موجودی: {balance_val} TRX\n✅ لطفاً مقدار برداشت را وارد کنید (عدد).",
        reply_markup=get_main_keyboard(user_id),
    )
    return AMOUNT

async def withdraw_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    try:
        amount = float(text)
    except:
        await update.message.reply_text(
            "❌ لطفاً عدد معتبر وارد کنید.", reply_markup=get_main_keyboard(user_id)
        )
        return AMOUNT

    async with db_lock:
        cur.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
        r = cur.fetchone()
    if not r:
        await update.message.reply_text("❌ حساب شما پیدا نشد.", reply_markup=get_main_keyboard(user_id))
        return ConversationHandler.END

    balance_val = r["balance"]
    if amount < MIN_WITHDRAW:
        await update.message.reply_text(
            f"🚨 حداقل برداشت {MIN_WITHDRAW} TRX است.", reply_markup=get_main_keyboard(user_id)
        )
        return AMOUNT
    if amount > balance_val:
        await update.message.reply_text(
            f"🚨 موجودی کافی ندارید.\n💰 موجودی: {balance_val}", reply_markup=get_main_keyboard(user_id)
        )
        return AMOUNT

    context.user_data["withdraw_amount"] = amount
    await update.message.reply_text("📥 مقدار ثبت شد. لطفاً آدرس کیف پول خود را ارسال کنید.")
    return WALLET

async def withdraw_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    wallet = update.message.text.strip()
    amount = context.user_data.get("withdraw_amount")
    if amount is None:
        await update.message.reply_text("❌ خطا: مقدار برداشت پیدا نشد.", reply_markup=get_main_keyboard(user_id))
        return ConversationHandler.END

    async with db_lock:
        # insert withdrawal and decrement balance atomically
        cur.execute(
            "INSERT INTO withdrawals (user_id, wallet, amount, status) VALUES (?, ?, ?, 'pending')",
            (user_id, wallet, amount),
        )
        wid = cur.lastrowid
        cur.execute("UPDATE users SET balance = balance - ? WHERE user_id=?", (amount, user_id))
        conn.commit()

    await update.message.reply_text(
        f"🎉 درخواست برداشتت ثبت شد!\n💰 {amount} TRX\n📥 {wallet}\n⏳ در صف بررسی ...", reply_markup=get_main_keyboard(user_id)
    )

    # send to channel and admins with buttons that include withdrawal id
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ تایید", callback_data=f"approve:{wid}"),
                InlineKeyboardButton("❌ رد", callback_data=f"reject:{wid}"),
            ]
        ]
    )
    try:
        await context.bot.send_message(
            chat_id=CHANNEL_ID,
            text=f"📢 برداشت جدید:\n👤 {user_id}\n💰 {amount} TRX\n📥 {wallet}\n⏳ در صف پرداخت",
            reply_markup=keyboard,
        )
    except Exception as e:
        logger.info(f"Could not post to channel {CHANNEL_ID}: {e}")

    for admin in ADMINS:
        try:
            await context.bot.send_message(
                chat_id=admin,
                text=f"📢 برداشت جدید:\n👤 {user_id}\n💰 {amount} TRX\n📥 {wallet}\n⏳ در صف پرداخت",
                reply_markup=keyboard,
            )
        except Exception as e:
            logger.info(f"Could not notify admin {admin}: {e}")

    # clear temp
    context.user_data.pop("withdraw_amount", None)
    return ConversationHandler.END

async def withdraw_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ برداشت لغو شد.", reply_markup=get_main_keyboard(update.effective_user.id))
    context.user_data.pop("withdraw_amount", None)
    return ConversationHandler.END

# ---------- هندلر تایید یا رد برداشت ----------
async def handle_approval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    admin_id = query.from_user.id

    if admin_id not in ADMINS:
        await query.edit_message_text("❌ شما ادمین نیستید.")
        return

    try:
        action, wid_str = data.split(":")
        wid = int(wid_str)
    except Exception:
        await query.edit_message_text("❌ دادهٔ معتبر نیست.")
        return

    async with db_lock:
        cur.execute("SELECT id, user_id, amount, wallet, status FROM withdrawals WHERE id=?", (wid,))
        wd = cur.fetchone()
        if not wd:
            await query.edit_message_text("⏳ درخواست وجود ندارد یا قبلاً پردازش شده.")
            return

        if wd["status"] != "pending":
            await query.edit_message_text(f"⏳ این درخواست قبلاً پردازش شده ({wd['status']}).")
            return

        uid = wd["user_id"]
        amount = wd["amount"]
        wallet = wd["wallet"]

        if action == "approve":
            cur.execute("UPDATE withdrawals SET status='paid' WHERE id=?", (wid,))
            conn.commit()
            await query.edit_message_text(f"✅ برداشت {amount} TRX توسط ادمین تایید شد.")
            try:
                await context.bot.send_message(chat_id=uid, text=f"🎉 برداشت شما به مبلغ {amount} TRX توسط ادمین تایید شد و پرداخت انجام شد!")
                await context.bot.send_message(chat_id=CHANNEL_ID, text=f"💸 برداشت کاربر {uid} ✅ پرداخت شد.\n💰 {amount} TRX\n📥 {wallet}")
            except Exception as e:
                logger.info(f"Error notifying user/channel: {e}")

        elif action == "reject":
            # برگرداندن موجودی و علامت زدن به عنوان rejected
            cur.execute("UPDATE withdrawals SET status='rejected' WHERE id=?", (wid,))
            cur.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, uid))
            conn.commit()
            await query.edit_message_text(f"❌ برداشت {amount} TRX توسط ادمین رد شد.")
            try:
                await context.bot.send_message(chat_id=uid, text=f"❌ برداشت شما به مبلغ {amount} TRX توسط ادمین رد شد و موجودی به حسابت بازگشت داده شد.")
                await context.bot.send_message(chat_id=CHANNEL_ID, text=f"💸 برداشت کاربر {uid} ❌ رد شد.\n💰 {amount} TRX\n📥 {wallet}")
            except Exception as e:
                logger.info(f"Error notifying user/channel: {e}")

# ---------- پنل ادمین ----------
async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMINS:
        await update.message.reply_text("❌ شما ادمین نیستید.", reply_markup=get_main_keyboard(user_id))
        return
    async with db_lock:
        cur.execute("SELECT COUNT(*) as c FROM users")
        total_users = cur.fetchone()["c"]
        cur.execute("SELECT COUNT(*) as c, COALESCE(SUM(amount),0) as s FROM withdrawals WHERE status='pending'")
        row = cur.fetchone()
        wd_count = row["c"]
        total_amount = row["s"]
    await update.message.reply_text(
        f"📊 آمار سیستم:\n👥 کاربران ثبت‌نامی: {total_users}\n💸 درخواست‌های برداشت در صف: {wd_count}\n✅ مجموع مبلغ در صف: {total_amount} TRX",
        reply_markup=get_admin_keyboard(),
    )

async def admin_withdrawals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMINS:
        await update.message.reply_text("❌ شما ادمین نیستید.", reply_markup=get_main_keyboard(user_id))
        return
    async with db_lock:
        cur.execute("SELECT id, user_id, amount, wallet, status FROM withdrawals ORDER BY id DESC LIMIT 5")
        rows = cur.fetchall()
    if not rows:
        await update.message.reply_text("⏳ هیچ درخواستی نیست.", reply_markup=get_admin_keyboard())
        return
    for r in rows:
        wid, uid, amount, wallet, status = r["id"], r["user_id"], r["amount"], r["wallet"], r["status"]
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("✅ تایید", callback_data=f"approve:{wid}"),
                    InlineKeyboardButton("❌ رد", callback_data=f"reject:{wid}"),
                ]
            ]
        )
        msg = f"👤 {uid} | 💰 {amount} TRX | 📥 {wallet} | ⏳ وضعیت: {status}"
        await update.message.reply_text(msg, reply_markup=keyboard)

# ---------- هدیه به کاربر توسط ادمین ----------
async def gift(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMINS:
        await update.message.reply_text("❌ شما ادمین نیستید.", reply_markup=get_main_keyboard(user_id))
        return

    args = context.args
    if len(args) != 2:
        await update.message.reply_text("❌ دستور درست: /gift <user_id> <amount>", reply_markup=get_admin_keyboard())
        return
    try:
        target_user = int(args[0])
        amount = float(args[1])
    except:
        await update.message.reply_text("❌ مقدار یا آی‌دی معتبر نیست.", reply_markup=get_admin_keyboard())
        return

    async with db_lock:
        cur.execute("SELECT balance FROM users WHERE user_id=?", (target_user,))
        if not cur.fetchone():
            await update.message.reply_text("❌ کاربر وجود ندارد.", reply_markup=get_admin_keyboard())
            return
        cur.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, target_user))
        conn.commit()
    await update.message.reply_text(f"✅ {amount} TRX به کاربر {target_user} هدیه داده شد.", reply_markup=get_admin_keyboard())
    try:
        await context.bot.send_message(chat_id=target_user, text=f"🎁 {amount} TRX از طرف ادمین دریافت کردید!")
    except:
        pass

# ---------- هندلر منو ----------
async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text

    if text == "💰 موجودی":
        await balance(update, context)
    elif text == "📥 برداشت":
        return await withdraw_start(update, context)
    elif text == "📢 لینک دعوت":
        await balance(update, context)
    elif text == "⚙️ پنل ادمین" and user_id in ADMINS:
        await update.message.reply_text("⚙️ پنل مدیریت:", reply_markup=get_admin_keyboard())
    elif text == "📊 آمار کاربران" and user_id in ADMINS:
        await admin_stats(update, context)
    elif text == "💸 لیست برداشت‌ها" and user_id in ADMINS:
        await admin_withdrawals(update, context)
    elif text == "🎁 هدیه به کاربر" and user_id in ADMINS:
        await update.message.reply_text("📌 دستور:\n/gift <user_id> <amount>", reply_markup=get_admin_keyboard())
    elif text == "🔙 بازگشت":
        await update.message.reply_text("⬅️ بازگشت به منو اصلی", reply_markup=get_main_keyboard(user_id))
    else:
        await update.message.reply_text("⚠️ دستور نامشخص. از منو گزینه‌ای انتخاب کن.", reply_markup=get_main_keyboard(user_id))

# ---------- راه‌اندازی ----------
def main():
    if TOKEN == "<YOUR_TOKEN_HERE>":
        logger.warning("توکن در کد قرار داده نشده — لطفاً BOT_TOKEN را ست کن یا مقدار TOKEN را در فایل قرار بده.")
    app = Application.builder().token(TOKEN).build()

    # Conversation handler برای برداشت
    conv_withdraw = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📥 برداشت$"), withdraw_start)],
        states={
            AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, withdraw_amount)],
            WALLET: [MessageHandler(filters.TEXT & ~filters.COMMAND, withdraw_wallet)],
        },
        fallbacks=[CommandHandler("cancel", withdraw_cancel)],
        per_user=True,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("gift", gift))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_handler))
    app.add_handler(CallbackQueryHandler(handle_approval, pattern="^(approve:|reject:).*"))
    app.add_handler(conv_withdraw)

    logger.info("✅ ربات روشن شد ...")
    app.run_polling()

if __name__ == "__main__":
    main()
