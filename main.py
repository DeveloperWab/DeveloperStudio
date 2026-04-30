import os
import threading
import logging
from flask import Flask
from pymongo import MongoClient
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# --- Logging ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- Configuration ---
TOKEN = "8384600981:AAHhAm-cD1qjiav6UikKsII4FGNsAwzon2o"
# Aapka MongoDB URI
MONGO_URI = "mongodb+srv://Vansh:Vansh000@cluster0.tqmuzxc.mongodb.net/?appName=Cluster0"
ADMIN_TRIGGER = "Vansh@000"

# --- MongoDB Connection ---
try:
    client = MongoClient(MONGO_URI)
    db = client['earning_bot_db']
    users_collection = db['users']
    print("✅ MongoDB Connected Successfully!")
except Exception as e:
    print(f"❌ MongoDB Connection Error: {e}")

# --- Railway Health Check ---
server = Flask(__name__)
@server.route('/')
def health_check():
    return "Bot is Live with MongoDB!", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    server.run(host='0.0.0.0', port=port)

# --- Keyboards ---
USER_KEYBOARD = [
    ['📝 Tasks', '🔗 Visit & Earn'],
    ['💰 My Balance', '💸 Withdraw'],
    ['👥 Referral Program', '📊 My Stats'],
    ['❓ Help', 'ℹ️ About']
]

ADMIN_KEYBOARD = [
    ['📊 Dashboard', '👥 User Stats'],
    ['💰 Financial Stats', '💸 Withdrawal Requests'],
    ['📋 Pending Submissions', '📢 Broadcast'],
    ['➕ Add Task', '➕ Add Visit Task'],
    ['🔙 Exit Admin']
]

# --- Bot Functions ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id

    # Check if user exists in MongoDB
    user_in_db = users_collection.find_one({"user_id": chat_id})
    
    if not user_in_db:
        # Naya user create karein
        new_user = {
            "user_id": chat_id,
            "username": user.username or "NoUsername",
            "name": user.first_name,
            "balance": 0.0,
            "referrals": 0,
            "tasks_done": 0,
            "status": "active"
        }
        users_collection.insert_one(new_user)
        welcome_msg = f"👋 Hello {user.first_name}! Aapka account database me register ho gaya hai."
    else:
        welcome_msg = f"👋 Welcome back {user.first_name}!"

    reply_markup = ReplyKeyboardMarkup(USER_KEYBOARD, resize_keyboard=True)
    await update.message.reply_text(welcome_msg, reply_markup=reply_markup)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    chat_id = update.effective_chat.id

    # Admin Panel Secret Trigger
    if text == ADMIN_TRIGGER:
        reply_markup = ReplyKeyboardMarkup(ADMIN_KEYBOARD, resize_keyboard=True)
        await update.message.reply_text("⚡ *Admin Panel Active*", reply_markup=reply_markup, parse_mode="Markdown")
        return

    if text == '🔙 Exit Admin':
        reply_markup = ReplyKeyboardMarkup(USER_KEYBOARD, resize_keyboard=True)
        await update.message.reply_text("Back to User Menu.", reply_markup=reply_markup)
        return

    # Database se user ka current data nikalna
    user_data = users_collection.find_one({"user_id": chat_id})
    
    if not user_data:
        await update.message.reply_text("❌ Error: Please /start the bot first.")
        return

    if text == '💰 My Balance':
        bal = user_data.get('balance', 0.0)
        await update.message.reply_text(f"💰 *Your Current Balance:* {bal:.2f} INR", parse_mode="Markdown")

    elif text == '📊 My Stats':
        stats = (
            f"📊 *User Statistics*\\n\\n"
            f"👤 Name: {user_data.get('name')}\\n"
            f"💰 Balance: {user_data.get('balance'):.2f} INR\\n"
            f"👥 Total Referrals: {user_data.get('referrals')}\\n"
            f"📝 Tasks Completed: {user_data.get('tasks_done')}"
        )
        await update.message.reply_text(stats, parse_mode="Markdown")

    elif text == '👥 Referral Program':
        bot_info = await context.bot.get_me()
        ref_link = f"https://t.me/{bot_info.username}?start={chat_id}"
        await update.message.reply_text(f"👥 *Referral Program*\\n\\nApne doston ko invite karein aur per referral 2 INR kamayein!\\n\\nAapka Link: {ref_link}", parse_mode="Markdown")

    elif text in ['📝 Tasks', '🔗 Visit & Earn']:
        await update.message.reply_text("🛠️ Tasks feature jaldi hi update hoga database ke saath.")

    elif text == '💸 Withdraw':
        await update.message.reply_text("💸 Minimum Withdrawal: 100 INR")

# --- Run the Bot ---
if __name__ == '__main__':
    # Start Flask in background for Railway
    threading.Thread(target=run_flask, daemon=True).start()
    
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    
    print("🚀 Bot is running with MongoDB...")
    app.run_polling()
