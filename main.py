# main.py
import os
import threading
import logging
import json
import asyncio
import hashlib
import re
import secrets
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
from pymongo import MongoClient
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from functools import wraps

# --- Load Environment Variables ---
TOKEN = os.environ.get("BOT_TOKEN", "")
MONGO_URI = os.environ.get("MONGO_URI", "")
ADMIN_TRIGGER = os.environ.get("ADMIN_TRIGGER", "Admin@000")
ADMIN_USER_IDS = [int(x) for x in os.environ.get("ADMIN_USER_IDS", "").split(",") if x]

# Validate required environment variables
if not TOKEN:
    raise ValueError("BOT_TOKEN environment variable is not set!")
if not MONGO_URI:
    raise ValueError("MONGO_URI environment variable is not set!")

# --- Logging ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Configuration ---
MAX_TASKS_PER_DAY = int(os.environ.get("MAX_TASKS_PER_DAY", "50"))
MAX_VISIT_TASKS_PER_DAY = int(os.environ.get("MAX_VISIT_TASKS_PER_DAY", "30"))
MAX_WITHDRAWAL_ATTEMPTS = int(os.environ.get("MAX_WITHDRAWAL_ATTEMPTS", "3"))
REFERRAL_BONUS = float(os.environ.get("REFERRAL_BONUS", "2.0"))
MIN_WITHDRAWAL_UPI = float(os.environ.get("MIN_WITHDRAWAL_UPI", "10"))
MIN_WITHDRAWAL_BANK = float(os.environ.get("MIN_WITHDRAWAL_BANK", "50"))
MIN_WITHDRAWAL_CRYPTO = float(os.environ.get("MIN_WITHDRAWAL_CRYPTO", "150"))
MIN_WITHDRAWAL_GIFT = float(os.environ.get("MIN_WITHDRAWAL_GIFT", "10"))

# Daily Bonus Settings
DAILY_BONUS_BASE = float(os.environ.get("DAILY_BONUS_BASE", "0.10"))
DAILY_BONUS_MAX = float(os.environ.get("DAILY_BONUS_MAX", "1.00"))
DAILY_BONUS_STREAK_BONUS = float(os.environ.get("DAILY_BONUS_STREAK_BONUS", "0.05"))

# --- MongoDB Connection ---
try:
    client = MongoClient(MONGO_URI)
    db = client['earning_bot_db']
    users_collection = db['users']
    tasks_collection = db['tasks']
    visit_tasks_collection = db['visit_tasks']
    task_submissions = db['task_submissions']
    withdrawals_collection = db['withdrawals']
    user_task_history = db['user_task_history']
    user_visit_history = db['user_visit_history']
    user_sessions = db['user_sessions']
    fraud_alerts = db['fraud_alerts']
    active_visits = db['active_visits']
    daily_stats = db['daily_stats']
    print("✅ MongoDB Connected Successfully!")
except Exception as e:
    print(f"❌ MongoDB Connection Error: {e}")
    client = None

# --- Flask for Railway ---
server = Flask(__name__)

@server.route('/')
def health_check():
    return jsonify({
        "status": "Bot is Live!",
        "timestamp": datetime.now().isoformat(),
        "users": users_collection.count_documents({}) if client else 0
    }), 200

@server.route('/webhook', methods=['POST'])
def webhook():
    return jsonify({"status": "ok"}), 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    server.run(host='0.0.0.0', port=port)

# --- Keyboards ---
USER_KEYBOARD = [
    ['📝 Tasks', '🔗 Visit & Earn'],
    ['💰 My Balance', '💸 Withdraw'],
    ['👥 Referral Program', '📊 My Stats'],
    ['📜 Task History', '💳 Withdrawal History'],
    ['🎁 Daily Bonus', '🏆 Leaderboard'],
    ['🗑️ Clear Chat', '❓ Help']
]

ADMIN_KEYBOARD = [
    ['📊 Dashboard', '👥 User Stats'],
    ['💰 Financial Stats', '💸 Withdrawal Requests'],
    ['📋 Pending Submissions', '📢 Broadcast'],
    ['➕ Add Task', '➕ Add Visit Task'],
    ['📜 All Tasks', '📊 Task Analytics'],
    ['🚫 Fraud Alerts', '⚙️ System Settings'],
    ['🔙 Exit Admin']
]

WITHDRAWAL_METHODS = ['UPI', 'Bank Transfer', 'Crypto (Bitcoin)', 'Google Play Gift Card', 'Amazon Gift Card']
WITHDRAWAL_LIMITS = {
    'UPI': MIN_WITHDRAWAL_UPI,
    'Bank Transfer': MIN_WITHDRAWAL_BANK,
    'Crypto (Bitcoin)': MIN_WITHDRAWAL_CRYPTO,
    'Google Play Gift Card': MIN_WITHDRAWAL_GIFT,
    'Amazon Gift Card': MIN_WITHDRAWAL_GIFT
}

GOOGLE_PLAY_AMOUNTS = [10, 20, 25, 50, 100]
AMAZON_AMOUNTS = [10, 20, 25, 50, 100]

# --- Helper Functions ---
def get_user(chat_id):
    return users_collection.find_one({"user_id": chat_id})

def update_user_balance(chat_id, amount):
    users_collection.update_one(
        {"user_id": chat_id},
        {"$inc": {"balance": amount}}
    )

def is_admin(chat_id):
    if chat_id in ADMIN_USER_IDS:
        return True
    user = get_user(chat_id)
    return user and user.get('is_admin', False)

def check_task_limit(task):
    """Check if task has reached its completion limit"""
    if task.get('max_completions') and task.get('total_completions', 0) >= task['max_completions']:
        return False
    return True

def update_task_completion(task_id, task_type='regular'):
    """Update task completion count"""
    collection = tasks_collection if task_type == 'regular' else visit_tasks_collection
    collection.update_one(
        {"task_id": task_id},
        {"$inc": {"total_completions": 1}}
    )
    
    # Check if task should expire
    task = collection.find_one({"task_id": task_id})
    if task.get('max_completions') and task.get('total_completions', 0) >= task['max_completions']:
        collection.update_one(
            {"task_id": task_id},
            {"$set": {"status": "expired"}}
        )
        return True
    return False

def clear_user_context(context):
    """Clear all user context data"""
    keys_to_clear = [
        'awaiting_withdrawal_amount',
        'awaiting_withdrawal_details',
        'withdrawal_method',
        'withdrawal_amount',
        'admin_action',
        'task_step',
        'current_task',
        'awaiting_screenshot',
        'current_visit'
    ]
    for key in keys_to_clear:
        context.user_data.pop(key, None)

# --- Bot Functions ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    args = context.args
    
    # Clear any existing context
    clear_user_context(context)
    
    user_in_db = users_collection.find_one({"user_id": chat_id})
    
    if not user_in_db:
        referred_by = None
        if args and args[0].isdigit() and args[0] != str(chat_id):
            referred_by = int(args[0])
            ref_user = get_user(referred_by)
            if ref_user and not ref_user.get('is_admin', False):
                update_user_balance(referred_by, REFERRAL_BONUS)
                users_collection.update_one(
                    {"user_id": referred_by},
                    {"$inc": {"referrals": 1, "total_earned": REFERRAL_BONUS}}
                )
                try:
                    await context.bot.send_message(
                        referred_by,
                        f"🎉 New user joined using your referral link! +{REFERRAL_BONUS} INR added to your balance."
                    )
                except:
                    pass
        
        new_user = {
            "user_id": chat_id,
            "username": user.username or "NoUsername",
            "name": user.first_name,
            "balance": 0.0,
            "referrals": 0,
            "tasks_done": 0,
            "visit_tasks_done": 0,
            "total_earned": 0.0,
            "total_withdrawn": 0.0,
            "status": "active",
            "joined_date": datetime.now(),
            "referred_by": referred_by,
            "is_admin": False,
            "last_active": datetime.now(),
            "daily_bonus_claimed": None,
            "bonus_streak": 0,
            "total_points": 0
        }
        users_collection.insert_one(new_user)
        
        # Update daily stats
        daily_stats.update_one(
            {"date": datetime.now().strftime('%Y-%m-%d')},
            {"$inc": {"new_users": 1}},
            upsert=True
        )
        
        welcome_msg = f"👋 Hello {user.first_name}! Welcome to the Earning Bot!\n\n🎁 Complete tasks and earn money!\n💰 Earn {REFERRAL_BONUS} INR per referral!\n🎁 Daily bonus available!"
        
        if referred_by:
            welcome_msg += f"\n\n✅ You were referred by a user!"
    else:
        welcome_msg = f"👋 Welcome back {user.first_name}!\n💰 Your balance: {user_in_db.get('balance', 0):.2f} INR"
        users_collection.update_one({"user_id": chat_id}, {"$set": {"last_active": datetime.now()}})

    reply_markup = ReplyKeyboardMarkup(USER_KEYBOARD, resize_keyboard=True)
    await update.message.reply_text(welcome_msg, reply_markup=reply_markup)

async def daily_bonus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = get_user(chat_id)
    
    # Clear any pending context
    clear_user_context(context)
    
    last_claimed = user.get('daily_bonus_claimed')
    today = datetime.now().date()
    
    if last_claimed and last_claimed.date() == today:
        await update.message.reply_text(
            "🎁 *Daily Bonus*\n\n"
            f"You've already claimed your daily bonus today!\n"
            f"Come back tomorrow for more!",
            parse_mode="Markdown"
        )
        return
    
    # Calculate bonus based on streak
    streak = user.get('bonus_streak', 0)
    if last_claimed and last_claimed.date() == today - timedelta(days=1):
        streak += 1
    else:
        streak = 1
    
    # Calculate bonus amount (base + streak bonus, max limit)
    bonus_amount = DAILY_BONUS_BASE + (streak - 1) * DAILY_BONUS_STREAK_BONUS
    bonus_amount = min(bonus_amount, DAILY_BONUS_MAX)
    
    update_user_balance(chat_id, bonus_amount)
    users_collection.update_one(
        {"user_id": chat_id},
        {
            "$set": {
                "daily_bonus_claimed": datetime.now(),
                "bonus_streak": streak
            },
            "$inc": {"total_earned": bonus_amount}
        }
    )
    
    # Calculate next bonus info
    next_bonus = DAILY_BONUS_BASE + streak * DAILY_BONUS_STREAK_BONUS
    next_bonus = min(next_bonus, DAILY_BONUS_MAX)
    
    await update.message.reply_text(
        f"🎁 *Daily Bonus Claimed!*\n\n"
        f"💰 +{bonus_amount:.2f} INR added to your balance!\n"
        f"🔥 Streak: {streak} days\n"
        f"📈 Tomorrow's bonus: {next_bonus:.2f} INR\n\n"
        f"Come back tomorrow for more!",
        parse_mode="Markdown"
    )

async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    
    # Clear any pending context
    clear_user_context(context)
    
    # Top earners
    top_earners = list(users_collection.find(
        {"is_admin": False}
    ).sort("total_earned", -1).limit(10))
    
    # Top referrers
    top_referrers = list(users_collection.find(
        {"is_admin": False}
    ).sort("referrals", -1).limit(10))
    
    message = "🏆 *Leaderboard*\n\n"
    
    message += "*💰 Top Earners:*\n"
    for i, user in enumerate(top_earners, 1):
        name = user.get('name', 'Unknown')[:20]
        message += f"{i}. {name} - {user.get('total_earned', 0):.2f} INR\n"
    
    message += "\n*👥 Top Referrers:*\n"
    for i, user in enumerate(top_referrers, 1):
        name = user.get('name', 'Unknown')[:20]
        message += f"{i}. {name} - {user.get('referrals', 0)} referrals\n"
    
    await update.message.reply_text(message, parse_mode="Markdown")

async def clear_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    
    # Clear any pending context
    clear_user_context(context)
    
    await update.message.reply_text(
        "🗑️ *Clear Chat*\n\n"
        "To clear your chat history:\n"
        "1. Open Telegram settings\n"
        "2. Go to 'Clear History'\n"
        "3. Select 'Clear All'\n\n"
        "Or use /start to begin fresh!",
        parse_mode="Markdown"
    )

# --- Task Functions ---
async def show_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    page = context.user_data.get('task_page', 0)
    tasks_per_page = 5
    
    # Clear any pending withdrawal/other context
    clear_user_context(context)
    
    # Get completed task IDs
    completed_tasks = [t['task_id'] for t in user_task_history.find({
        "user_id": chat_id,
        "status": {"$in": ["approved", "pending"]}
    })]
    
    tasks = list(tasks_collection.find({
        "status": "active",
        "expires_at": {"$gt": datetime.now()},
        "task_id": {"$nin": completed_tasks}
    }).skip(page * tasks_per_page).limit(tasks_per_page))
    
    # Filter tasks that haven't reached max completions
    available_tasks = [t for t in tasks if check_task_limit(t)]
    
    if not available_tasks:
        if page == 0:
            await update.message.reply_text("📝 No tasks available at the moment! Check back later!")
        else:
            await update.message.reply_text("No more tasks!")
        return
    
    for task in available_tasks:
        keyboard = [
            [InlineKeyboardButton("🎯 Start Task", callback_data=f"start_task_{task['task_id']}")],
            [InlineKeyboardButton("📸 Submit Screenshot", callback_data=f"submit_screenshot_{task['task_id']}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        message = f"📌 *{task['name']}*\n\n"
        message += f"💰 *Reward:* {task['amount']} INR\n"
        message += f"📝 *Description:* {task['description']}\n"
        message += f"⏰ *Expires:* {task['expires_at'].strftime('%Y-%m-%d %H:%M')}"
        
        if task.get('max_completions'):
            remaining = task['max_completions'] - task.get('total_completions', 0)
            message += f"\n🎯 *Remaining Slots:* {remaining}"
        
        if task.get('image_id'):
            try:
                await update.message.reply_photo(
                    photo=task['image_id'],
                    caption=message,
                    reply_markup=reply_markup,
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Error sending photo: {e}")
                await update.message.reply_text(message, reply_markup=reply_markup, parse_mode="Markdown")
        else:
            await update.message.reply_text(message, reply_markup=reply_markup, parse_mode="Markdown")
    
    # Navigation buttons
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("◀️ Previous", callback_data="task_prev"))
    
    next_tasks = list(tasks_collection.find({
        "status": "active",
        "expires_at": {"$gt": datetime.now()},
        "task_id": {"$nin": completed_tasks}
    }).skip((page + 1) * tasks_per_page).limit(1))
    
    if next_tasks:
        nav_buttons.append(InlineKeyboardButton("Next ▶️", callback_data="task_next"))
    
    if nav_buttons:
        nav_markup = InlineKeyboardMarkup([nav_buttons])
        await update.message.reply_text("📋 *Navigation*", reply_markup=nav_markup, parse_mode="Markdown")

async def start_task(update: Update, context: ContextTypes.DEFAULT_TYPE, task_id):
    query = update.callback_query
    await query.answer()
    chat_id = query.from_user.id
    
    # Clear any pending context
    clear_user_context(context)
    
    task = tasks_collection.find_one({"task_id": task_id})
    
    if not task or task['status'] != 'active' or task['expires_at'] < datetime.now():
        await query.edit_message_text("❌ This task is no longer available!")
        return
    
    if not check_task_limit(task):
        await query.edit_message_text("❌ This task has reached its maximum completion limit!")
        return
    
    existing = user_task_history.find_one({"user_id": chat_id, "task_id": task_id, "status": {"$in": ["approved", "pending"]}})
    if existing:
        await query.edit_message_text("❌ You've already completed or submitted this task!")
        return
    
    context.user_data['current_task'] = task_id
    context.user_data['task_start_time'] = datetime.now()
    
    keyboard = [[InlineKeyboardButton("🔗 Visit Link", url=task['link'])]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        f"📌 *{task['name']}*\n\n"
        f"1️⃣ Click the button below to visit the website\n"
        f"2️⃣ Complete the required action\n"
        f"3️⃣ Take a screenshot as proof\n"
        f"4️⃣ Click 'Submit Screenshot' button\n\n"
        f"⚠️ *Note:* After completing, submit your screenshot proof!",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )
    
    context.user_data['awaiting_screenshot'] = True

async def submit_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE, task_id):
    query = update.callback_query
    await query.answer()
    
    # Clear other context but keep screenshot context
    keys_to_clear = ['awaiting_withdrawal_amount', 'awaiting_withdrawal_details', 'withdrawal_method', 'withdrawal_amount']
    for key in keys_to_clear:
        context.user_data.pop(key, None)
    
    context.user_data['current_task'] = task_id
    context.user_data['awaiting_screenshot'] = True
    
    await query.edit_message_text(
        "📸 *Screenshot Submission*\n\n"
        "Please send the screenshot of completed task.\n\n"
        "Make sure the screenshot clearly shows the completed action.",
        parse_mode="Markdown"
    )

async def handle_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    
    if not context.user_data.get('awaiting_screenshot'):
        return
    
    if not update.message.photo:
        await update.message.reply_text("❌ Please send a screenshot photo!")
        return
    
    task_id = context.user_data.get('current_task')
    if not task_id:
        await update.message.reply_text("❌ No task selected! Please go to Tasks menu.")
        context.user_data['awaiting_screenshot'] = False
        return
    
    existing = task_submissions.find_one({"user_id": chat_id, "task_id": task_id, "status": "pending"})
    if existing:
        await update.message.reply_text("❌ You already have a pending submission for this task!")
        context.user_data['awaiting_screenshot'] = False
        return
    
    task = tasks_collection.find_one({"task_id": task_id})
    if not task:
        await update.message.reply_text("❌ Task no longer exists!")
        context.user_data['awaiting_screenshot'] = False
        return
    
    photo = update.message.photo[-1]
    file_id = photo.file_id
    
    submission_hash = hashlib.md5(f"{chat_id}{task_id}{datetime.now().timestamp()}".encode()).hexdigest()[:10]
    
    submission = {
        "submission_id": f"sub_{submission_hash}",
        "task_id": task_id,
        "task_name": task['name'],
        "user_id": chat_id,
        "username": update.effective_user.username or "NoUsername",
        "user_name": update.effective_user.first_name,
        "amount": task['amount'],
        "screenshot_id": file_id,
        "status": "pending",
        "submitted_at": datetime.now()
    }
    
    task_submissions.insert_one(submission)
    
    user_task_history.insert_one({
        "user_id": chat_id,
        "task_id": task_id,
        "task_name": task['name'],
        "amount": task['amount'],
        "status": "pending",
        "submitted_at": datetime.now()
    })
    
    context.user_data['awaiting_screenshot'] = False
    context.user_data['current_task'] = None
    
    await update.message.reply_text(
        f"✅ *Screenshot received!*\n\n"
        f"Your submission for task '{task['name']}' is now pending admin approval.\n"
        f"💰 Amount: {task['amount']} INR\n\n"
        f"You will be notified once approved/rejected.",
        parse_mode="Markdown"
    )
    
    # Notify admins
    for admin_id in ADMIN_USER_IDS:
        try:
            await context.bot.send_message(
                admin_id,
                f"📋 *New Task Submission!*\n\n"
                f"👤 User: {update.effective_user.first_name}\n"
                f"🆔 ID: `{chat_id}`\n"
                f"📌 Task: {task['name']}\n"
                f"💰 Amount: {task['amount']} INR",
                parse_mode="Markdown"
            )
        except:
            pass

# --- Visit Task Functions ---
async def show_visit_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    page = context.user_data.get('visit_page', 0)
    tasks_per_page = 5
    
    # Clear any pending context
    clear_user_context(context)
    
    user_completed_today = [v['task_id'] for v in user_visit_history.find({
        "user_id": chat_id,
        "completed_at": {"$gt": datetime.now() - timedelta(hours=24)}
    })]
    
    tasks = list(visit_tasks_collection.find({
        "status": "active",
        "expires_at": {"$gt": datetime.now()}
    }).skip(page * tasks_per_page).limit(tasks_per_page))
    
    available_tasks = []
    for t in tasks:
        if t['task_id'] not in user_completed_today and check_task_limit(t):
            available_tasks.append(t)
    
    if not available_tasks:
        await update.message.reply_text("🔗 No visit tasks available at the moment! Check back later.")
        return
    
    for task in available_tasks:
        keyboard = [[InlineKeyboardButton("🔗 Visit Website", callback_data=f"visit_task_{task['task_id']}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        message = f"🔗 *{task['name']}*\n\n"
        message += f"💰 *Reward:* {task['amount']} INR\n"
        message += f"⏱️ *Time Required:* {task['visit_time']} seconds\n"
        message += f"🔄 *Cooldown:* 24 hours"
        
        if task.get('max_completions'):
            remaining = task['max_completions'] - task.get('total_completions', 0)
            message += f"\n🎯 *Remaining Slots:* {remaining}"
        
        if task.get('image_id'):
            try:
                await update.message.reply_photo(photo=task['image_id'], caption=message, reply_markup=reply_markup, parse_mode="Markdown")
            except:
                await update.message.reply_text(message, reply_markup=reply_markup, parse_mode="Markdown")
        else:
            await update.message.reply_text(message, reply_markup=reply_markup, parse_mode="Markdown")

async def visit_task(update: Update, context: ContextTypes.DEFAULT_TYPE, task_id):
    """Handle visit task when user clicks visit button"""
    query = update.callback_query
    await query.answer()
    chat_id = query.from_user.id
    
    # Clear any pending context
    clear_user_context(context)
    
    task = visit_tasks_collection.find_one({"task_id": task_id})
    
    if not task or task['status'] != 'active' or task['expires_at'] < datetime.now():
        await query.edit_message_text("❌ This task is no longer available!")
        return
    
    if not check_task_limit(task):
        await query.edit_message_text("❌ This task has reached its maximum completion limit!")
        return
    
    # Check cooldown
    recent_completion = user_visit_history.find_one({
        "user_id": chat_id,
        "task_id": task_id,
        "completed_at": {"$gt": datetime.now() - timedelta(hours=24)}
    })
    
    if recent_completion:
        next_available = recent_completion['completed_at'] + timedelta(hours=24)
        time_left = next_available - datetime.now()
        hours = int(time_left.total_seconds() // 3600)
        minutes = int((time_left.total_seconds() % 3600) // 60)
        await query.edit_message_text(f"⏰ You can only complete this task once every 24 hours!\n\nNext available in: {hours}h {minutes}m")
        return
    
    # Create session
    session_id = secrets.token_hex(16)
    end_time = datetime.now() + timedelta(seconds=task['visit_time'])
    
    active_visits.insert_one({
        "session_id": session_id,
        "user_id": chat_id,
        "task_id": task_id,
        "task_name": task['name'],
        "amount": task['amount'],
        "visit_time": task['visit_time'],
        "start_time": datetime.now(),
        "end_time": end_time,
        "status": "active",
        "message_id": query.message.message_id
    })
    
    context.user_data['current_visit'] = {
        "session_id": session_id,
        "task_id": task_id,
        "start_time": datetime.now(),
        "required_time": task['visit_time']
    }
    
    # Send visit link
    keyboard = [[InlineKeyboardButton("🌐 Click to Visit Website", url=task['link'])]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        f"🔗 *{task['name']}*\n\n"
        f"⏱️ *Required Time:* {task['visit_time']} seconds\n"
        f"💰 *Reward:* {task['amount']} INR\n\n"
        f"📋 *Instructions:*\n\n"
        f"1️⃣ Click the button below to visit the website\n"
        f"2️⃣ **STAY on the website for the entire {task['visit_time']} seconds**\n"
        f"3️⃣ After the time is complete, close the website\n"
        f"4️⃣ Come back here and click the completion button\n\n"
        f"⚠️ *Important:* If you close the website early, the task will be INVALID!\n"
        f"⏱️ Timer starts when you click the link!",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )
    
    # Schedule completion button
    asyncio.create_task(send_completion_button(context, chat_id, session_id, task['visit_time']))

async def send_completion_button(context: ContextTypes.DEFAULT_TYPE, chat_id: int, session_id: str, delay: int):
    """Send completion button after the required time"""
    await asyncio.sleep(delay)
    
    # Check if session still exists
    session = active_visits.find_one({"session_id": session_id, "status": "active"})
    if session:
        keyboard = [[InlineKeyboardButton("✅ Complete Task & Claim Reward", callback_data=f"complete_visit_{session_id}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await context.bot.send_message(
            chat_id,
            f"✅ *Time's Up!*\n\n"
            f"The required {delay} seconds have passed.\n\n"
            f"If you stayed on the website for the full time, click the button below to claim your reward!\n\n"
            f"⚠️ *Note:* Only click if you actually stayed the full time!",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )

async def complete_visit_task(update: Update, context: ContextTypes.DEFAULT_TYPE, session_id):
    """Complete visit task and claim reward"""
    query = update.callback_query
    await query.answer()
    chat_id = query.from_user.id
    
    session = active_visits.find_one({"session_id": session_id, "user_id": chat_id, "status": "active"})
    
    if not session:
        await query.edit_message_text("❌ No active visit task found! Please start a new task.")
        return
    
    # Calculate time spent
    time_spent = (datetime.now() - session['start_time']).total_seconds()
    required_time = session['visit_time']
    
    if time_spent >= required_time - 2:  # 2 seconds tolerance
        task = visit_tasks_collection.find_one({"task_id": session['task_id']})
        
        if not task or task['status'] != 'active':
            await query.edit_message_text("❌ This task is no longer available!")
            active_visits.update_one({"session_id": session_id}, {"$set": {"status": "expired"}})
            return
        
        # Add reward
        update_user_balance(chat_id, task['amount'])
        
        # Update user stats
        users_collection.update_one(
            {"user_id": chat_id},
            {
                "$inc": {
                    "visit_tasks_done": 1,
                    "tasks_done": 1,
                    "total_earned": task['amount']
                }
            }
        )
        
        # Record completion
        user_visit_history.insert_one({
            "user_id": chat_id,
            "task_id": session['task_id'],
            "task_name": task['name'],
            "amount": task['amount'],
            "completed_at": datetime.now(),
            "time_spent": time_spent
        })
        
        # Update task analytics
        update_task_completion(session['task_id'], 'visit')
        
        # Update session
        active_visits.update_one(
            {"session_id": session_id},
            {"$set": {"status": "completed", "completed_at": datetime.now(), "time_spent": time_spent}}
        )
        
        await query.edit_message_text(
            f"✅ *Task Completed Successfully!*\n\n"
            f"Task: {task['name']}\n"
            f"⏱️ Time spent: {int(time_spent)} seconds\n"
            f"💰 +{task['amount']} INR added to your balance!\n\n"
            f"You can complete this task again after 24 hours.",
            parse_mode="Markdown"
        )
        
        # Show new balance
        user = get_user(chat_id)
        await context.bot.send_message(chat_id, f"💰 Your new balance: {user.get('balance', 0):.2f} INR")
        
    else:
        remaining = required_time - time_spent
        await query.edit_message_text(
            f"❌ *Task Failed!*\n\n"
            f"You completed the task too early!\n"
            f"⏱️ Required: {required_time} seconds\n"
            f"⏱️ Your time: {int(time_spent)} seconds\n"
            f"⏱️ Remaining: {int(remaining)} seconds\n\n"
            f"Please start the task again and stay for the full time.",
            parse_mode="Markdown"
        )
        
        active_visits.update_one({"session_id": session_id}, {"$set": {"status": "failed"}})

# --- Withdrawal Functions ---
async def withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = get_user(chat_id)
    
    # Clear any pending context before starting new withdrawal
    clear_user_context(context)
    
    keyboard = []
    for method in WITHDRAWAL_METHODS:
        limit = WITHDRAWAL_LIMITS[method]
        keyboard.append([InlineKeyboardButton(f"{method} (Min: {limit} INR)", callback_data=f"withdraw_method_{method}")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "💸 *Withdrawal*\n\n"
        f"💰 Your balance: {user.get('balance', 0):.2f} INR\n\n"
        "Select withdrawal method:",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

async def process_withdrawal(update: Update, context: ContextTypes.DEFAULT_TYPE, method):
    query = update.callback_query
    await query.answer()
    
    # Clear any existing withdrawal context
    keys_to_clear = ['awaiting_withdrawal_amount', 'awaiting_withdrawal_details', 'withdrawal_amount']
    for key in keys_to_clear:
        context.user_data.pop(key, None)
    
    context.user_data['withdrawal_method'] = method
    
    if method in ['Google Play Gift Card', 'Amazon Gift Card']:
        amounts = GOOGLE_PLAY_AMOUNTS if method == 'Google Play Gift Card' else AMAZON_AMOUNTS
        keyboard = [[InlineKeyboardButton(f"{amount} INR", callback_data=f"gift_amount_{amount}")] for amount in amounts]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            f"💸 *{method} Withdrawal*\n\nSelect amount:",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
    else:
        context.user_data['awaiting_withdrawal_amount'] = True
        await query.edit_message_text(
            f"💸 *{method} Withdrawal*\n\n"
            f"Minimum amount: {WITHDRAWAL_LIMITS[method]} INR\n\n"
            f"Please enter the amount you want to withdraw:",
            parse_mode="Markdown"
        )

async def handle_withdrawal_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    
    if not context.user_data.get('awaiting_withdrawal_amount'):
        return
    
    try:
        amount = float(update.message.text.strip())
        method = context.user_data.get('withdrawal_method')
        user = get_user(chat_id)
        balance = user.get('balance', 0)
        min_amount = WITHDRAWAL_LIMITS[method]
        
        if amount < min_amount:
            await update.message.reply_text(f"❌ Amount must be at least {min_amount} INR!\n\nPlease try again with a valid amount.")
            return
        
        if amount > balance:
            await update.message.reply_text(f"❌ Insufficient balance! Your balance: {balance:.2f} INR\n\nPlease try again with a lower amount.")
            return
        
        context.user_data['withdrawal_amount'] = amount
        context.user_data['awaiting_withdrawal_amount'] = False
        context.user_data['awaiting_withdrawal_details'] = True
        
        method_details = {
            'UPI': 'Please send your UPI ID (e.g., name@okhdfcbank)',
            'Bank Transfer': 'Please send in this format:\n🏦 Bank Name\n🔢 Account Number\n🔑 IFSC Code\n👤 Account Holder Name',
            'Crypto (Bitcoin)': 'Please send your Bitcoin wallet address'
        }
        
        await update.message.reply_text(
            f"💸 *{method} Withdrawal*\n\n"
            f"Amount: {amount} INR\n\n"
            f"{method_details[method]}\n\n"
            f"Please send your details:",
            parse_mode="Markdown"
        )
        
    except ValueError:
        await update.message.reply_text("❌ Please enter a valid number!\n\nExample: 100")

async def handle_gift_amount(update: Update, context: ContextTypes.DEFAULT_TYPE, amount):
    query = update.callback_query
    await query.answer()
    
    context.user_data['withdrawal_amount'] = amount
    context.user_data['awaiting_withdrawal_details'] = True
    
    await query.edit_message_text(
        f"💸 *{context.user_data['withdrawal_method']} Withdrawal*\n\n"
        f"Amount: {amount} INR\n\n"
        f"Please send your email address:",
        parse_mode="Markdown"
    )

async def handle_withdrawal_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    
    if not context.user_data.get('awaiting_withdrawal_details'):
        return
    
    method = context.user_data.get('withdrawal_method')
    details = update.message.text.strip()
    amount = context.user_data.get('withdrawal_amount')
    
    if not amount:
        await update.message.reply_text("❌ Invalid withdrawal request! Please start over with /withdraw")
        clear_user_context(context)
        return
    
    # Validate based on method
    if method == 'UPI':
        if not re.match(r"^[a-zA-Z0-9.\-_]{2,256}@[a-zA-Z]{3,}$", details):
            await update.message.reply_text("❌ Invalid UPI ID format!\n\nPlease send valid UPI ID (e.g., name@okhdfcbank)")
            return
    elif method in ['Google Play Gift Card', 'Amazon Gift Card']:
        if not re.match(r"[^@]+@[^@]+\.[^@]+", details):
            await update.message.reply_text("❌ Invalid email address!\n\nPlease send a valid email address.")
            return
    
    # Create withdrawal request
    withdrawal_hash = hashlib.md5(f"{chat_id}{method}{datetime.now().timestamp()}".encode()).hexdigest()[:10]
    
    withdrawal = {
        "withdrawal_id": f"wd_{withdrawal_hash}",
        "user_id": chat_id,
        "username": update.effective_user.username or "NoUsername",
        "name": update.effective_user.first_name,
        "method": method,
        "details": details,
        "amount": amount,
        "status": "pending",
        "requested_at": datetime.now()
    }
    
    withdrawals_collection.insert_one(withdrawal)
    update_user_balance(chat_id, -amount)
    
    # Clear all withdrawal context
    clear_user_context(context)
    
    await update.message.reply_text(
        f"✅ *Withdrawal Request Submitted!*\n\n"
        f"💰 Amount: {amount} INR\n"
        f"💳 Method: {method}\n"
        f"📝 Details: {details}\n\n"
        f"Your request is pending admin approval.\n"
        f"You will be notified once processed.",
        parse_mode="Markdown"
    )
    
    # Notify admins
    for admin_id in ADMIN_USER_IDS:
        try:
            await context.bot.send_message(
                admin_id,
                f"💸 *New Withdrawal Request!*\n\n"
                f"👤 User: {update.effective_user.first_name}\n"
                f"🆔 ID: `{chat_id}`\n"
                f"💰 Amount: {amount} INR\n"
                f"💳 Method: {method}\n"
                f"📝 Details: {details}",
                parse_mode="Markdown"
            )
        except:
            pass

# --- History Functions ---
async def task_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    
    # Clear any pending context
    clear_user_context(context)
    
    submissions = list(task_submissions.find({"user_id": chat_id}).sort("submitted_at", -1).limit(20))
    
    if not submissions:
        await update.message.reply_text("📜 No task history found!")
        return
    
    message = "📜 *Task History*\n\n"
    pending = [s for s in submissions if s['status'] == 'pending']
    approved = [s for s in submissions if s['status'] == 'approved']
    rejected = [s for s in submissions if s['status'] == 'rejected']
    
    if pending:
        message += "*⏳ Pending:*\n"
        for sub in pending[:5]:
            message += f"• {sub['task_name']} - {sub['amount']} INR\n"
        message += "\n"
    
    if approved:
        message += "*✅ Approved:*\n"
        for sub in approved[:5]:
            message += f"• {sub['task_name']} - +{sub['amount']} INR\n"
        message += "\n"
    
    if rejected:
        message += "*❌ Rejected:*\n"
        for sub in rejected[:5]:
            message += f"• {sub['task_name']} - {sub['amount']} INR\n"
    
    await update.message.reply_text(message, parse_mode="Markdown")

async def withdrawal_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    
    # Clear any pending context
    clear_user_context(context)
    
    withdrawals = list(withdrawals_collection.find({"user_id": chat_id}).sort("requested_at", -1).limit(20))
    
    if not withdrawals:
        await update.message.reply_text("💳 No withdrawal history found!")
        return
    
    message = "💳 *Withdrawal History*\n\n"
    for wd in withdrawals[:10]:
        status_emoji = {'pending': '⏳', 'approved': '✅', 'rejected': '❌'}.get(wd['status'], '❓')
        message += f"{status_emoji} *{wd['amount']} INR* - {wd['method']}\n"
        message += f"   Status: {wd['status'].upper()}\n"
        message += f"   Date: {wd['requested_at'].strftime('%Y-%m-%d')}\n\n"
    
    await update.message.reply_text(message, parse_mode="Markdown")

# --- Main Message Handler ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    chat_id = update.effective_chat.id
    
    # Admin trigger
    if text == ADMIN_TRIGGER:
        users_collection.update_one(
            {"user_id": chat_id},
            {"$set": {"is_admin": True}},
            upsert=True
        )
        reply_markup = ReplyKeyboardMarkup(ADMIN_KEYBOARD, resize_keyboard=True)
        await update.message.reply_text("⚡ *Admin Panel Activated*", reply_markup=reply_markup, parse_mode="Markdown")
        return
    
    # Check for admin action
    if context.user_data.get('admin_action'):
        await handle_admin_input(update, context)
        return
    
    # Check for withdrawal flows
    if context.user_data.get('awaiting_withdrawal_amount'):
        await handle_withdrawal_amount(update, context)
        return
    
    if context.user_data.get('awaiting_withdrawal_details'):
        await handle_withdrawal_details(update, context)
        return
    
    # Check for screenshot
    if context.user_data.get('awaiting_screenshot') and update.message.photo:
        await handle_screenshot(update, context)
        return
    
    # Menu handlers
    if text == '🎁 Daily Bonus':
        await daily_bonus(update, context)
    elif text == '🏆 Leaderboard':
        await leaderboard(update, context)
    elif text == '🗑️ Clear Chat':
        await clear_chat(update, context)
    elif text == '📝 Tasks':
        await show_tasks(update, context)
    elif text == '🔗 Visit & Earn':
        await show_visit_tasks(update, context)
    elif text == '💰 My Balance':
        user = get_user(chat_id)
        await update.message.reply_text(f"💰 *Balance:* {user.get('balance', 0):.2f} INR", parse_mode="Markdown")
    elif text == '💸 Withdraw':
        await withdraw(update, context)
    elif text == '📊 My Stats':
        user = get_user(chat_id)
        stats = (
            f"📊 *Your Stats*\n\n"
            f"👤 {user.get('name')}\n"
            f"💰 Balance: {user.get('balance', 0):.2f} INR\n"
            f"👥 Referrals: {user.get('referrals', 0)}\n"
            f"📝 Tasks: {user.get('tasks_done', 0)}\n"
            f"🔗 Visit Tasks: {user.get('visit_tasks_done', 0)}\n"
            f"💵 Total Earned: {user.get('total_earned', 0):.2f} INR\n"
            f"💸 Withdrawn: {user.get('total_withdrawn', 0):.2f} INR"
        )
        await update.message.reply_text(stats, parse_mode="Markdown")
    elif text == '👥 Referral Program':
        bot_info = await context.bot.get_me()
        ref_link = f"https://t.me/{bot_info.username}?start={chat_id}"
        user = get_user(chat_id)
        await update.message.reply_text(
            f"👥 *Referral Program*\n\n"
            f"Earn {REFERRAL_BONUS} INR per referral!\n\n"
            f"🔗 Your link:\n`{ref_link}`\n\n"
            f"📊 Total: {user.get('referrals', 0)} referrals\n"
            f"💰 Earned: {user.get('referrals', 0) * REFERRAL_BONUS} INR",
            parse_mode="Markdown"
        )
    elif text == '📜 Task History':
        await task_history(update, context)
    elif text == '💳 Withdrawal History':
        await withdrawal_history(update, context)
    elif text == '❓ Help':
        help_text = (
            "❓ *Help Guide*\n\n"
            "📝 *Tasks:* Complete & submit screenshot\n"
            "🔗 *Visit:* Stay on website for required time\n"
            "👥 *Referral:* Invite friends\n"
            "🎁 *Daily Bonus:* Claim every day (starts at 0.10 INR)\n"
            "💰 *Withdraw:* Multiple methods available\n\n"
            "Minimum withdrawals:\n"
            f"• UPI: {MIN_WITHDRAWAL_UPI} INR\n"
            f"• Bank: {MIN_WITHDRAWAL_BANK} INR\n"
            f"• Crypto: {MIN_WITHDRAWAL_CRYPTO} INR\n"
            f"• Gift Cards: {MIN_WITHDRAWAL_GIFT} INR"
        )
        await update.message.reply_text(help_text, parse_mode="Markdown")
    elif text == 'ℹ️ About':
        about_text = (
            "ℹ️ *About*\n\n"
            "🤖 Version: 5.0\n"
            "💰 Secure earning platform\n"
            "🔒 Advanced fraud detection\n"
            "✅ Real-time verification\n\n"
            "Features: Tasks | Visit & Earn | Referrals | Daily Bonus | Leaderboard"
        )
        await update.message.reply_text(about_text, parse_mode="Markdown")
    elif text == '🔙 Exit Admin':
        reply_markup = ReplyKeyboardMarkup(USER_KEYBOARD, resize_keyboard=True)
        await update.message.reply_text("Back to User Menu.", reply_markup=reply_markup)
    
    # Admin panel options
    elif is_admin(chat_id):
        if text == '📊 Dashboard':
            await admin_dashboard(update, context)
        elif text == '📋 Pending Submissions':
            await pending_submissions(update, context)
        elif text == '💸 Withdrawal Requests':
            await pending_withdrawals(update, context)
        elif text == '➕ Add Task':
            await add_task(update, context)
        elif text == '➕ Add Visit Task':
            await add_visit_task(update, context)
        elif text == '📢 Broadcast':
            context.user_data['admin_action'] = 'broadcast'
            await update.message.reply_text("📢 Send broadcast message:")
        elif text == '📊 Task Analytics':
            await task_analytics(update, context)
        elif text == '🚫 Fraud Alerts':
            await fraud_alerts_view(update, context)

# --- Admin Functions (continued) ---
async def admin_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_chat.id):
        return
    
    total_users = users_collection.count_documents({})
    active_users = users_collection.count_documents({"last_active": {"$gt": datetime.now() - timedelta(days=7)}})
    total_earned = users_collection.aggregate([{"$group": {"_id": None, "total": {"$sum": "$total_earned"}}}]).next().get('total', 0)
    total_withdrawn = users_collection.aggregate([{"$group": {"_id": None, "total": {"$sum": "$total_withdrawn"}}}]).next().get('total', 0)
    pending_submissions = task_submissions.count_documents({"status": "pending"})
    pending_withdrawals = withdrawals_collection.count_documents({"status": "pending"})
    pending_visits = active_visits.count_documents({"status": "active"})
    
    dashboard = (
        f"📊 *Admin Dashboard*\n\n"
        f"👥 Total Users: {total_users}\n"
        f"🟢 Active Users (7d): {active_users}\n"
        f"💰 Total Earned: {total_earned:.2f} INR\n"
        f"💸 Total Withdrawn: {total_withdrawn:.2f} INR\n"
        f"📋 Pending Submissions: {pending_submissions}\n"
        f"💸 Pending Withdrawals: {pending_withdrawals}\n"
        f"🔄 Active Visits: {pending_visits}\n\n"
        f"📈 Platform Balance: {total_earned - total_withdrawn:.2f} INR"
    )
    
    await update.message.reply_text(dashboard, parse_mode="Markdown")

async def add_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_chat.id):
        return
    
    context.user_data['admin_action'] = 'add_task'
    context.user_data['task_step'] = 1
    await update.message.reply_text(
        "📝 *Add New Task*\n\n"
        "Send the task name:",
        parse_mode="Markdown"
    )

async def add_visit_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_chat.id):
        return
    
    context.user_data['admin_action'] = 'add_visit_task'
    context.user_data['task_step'] = 1
    await update.message.reply_text(
        "🔗 *Add New Visit Task*\n\n"
        "Send the task name:",
        parse_mode="Markdown"
    )

async def handle_admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    
    if not context.user_data.get('admin_action'):
        return
    
    action = context.user_data['admin_action']
    
    if action == 'add_task':
        step = context.user_data.get('task_step', 1)
        
        if step == 1:
            context.user_data['task_name'] = update.message.text
            context.user_data['task_step'] = 2
            await update.message.reply_text("📝 Send task description:")
            
        elif step == 2:
            context.user_data['task_description'] = update.message.text
            context.user_data['task_step'] = 3
            await update.message.reply_text("💰 Send reward amount (INR):")
            
        elif step == 3:
            try:
                amount = float(update.message.text)
                context.user_data['task_amount'] = amount
                context.user_data['task_step'] = 4
                await update.message.reply_text("🔗 Send task link:")
            except:
                await update.message.reply_text("❌ Send a valid number!")
                
        elif step == 4:
            context.user_data['task_link'] = update.message.text
            context.user_data['task_step'] = 5
            await update.message.reply_text("📸 Send task image OR type 'skip':")
            
        elif step == 5:
            image_id = None
            text = update.message.text
            
            if text.lower() != 'skip':
                if update.message.photo:
                    image_id = update.message.photo[-1].file_id
                else:
                    await update.message.reply_text("Please send a photo or type 'skip'")
                    return
            
            context.user_data['task_image'] = image_id
            context.user_data['task_step'] = 6
            await update.message.reply_text("🎯 Max completions (0 for unlimited):")
            
        elif step == 6:
            try:
                max_completions = int(update.message.text)
                
                task_id = f"task_{datetime.now().timestamp()}"
                task = {
                    "task_id": task_id,
                    "name": context.user_data['task_name'],
                    "description": context.user_data['task_description'],
                    "amount": context.user_data['task_amount'],
                    "link": context.user_data['task_link'],
                    "image_id": context.user_data.get('task_image'),
                    "status": "active",
                    "expires_at": datetime.now() + timedelta(days=30),
                    "total_completions": 0,
                    "total_spent": 0,
                    "max_completions": max_completions if max_completions > 0 else None,
                    "created_at": datetime.now()
                }
                
                tasks_collection.insert_one(task)
                
                limit_msg = "Unlimited" if max_completions == 0 else str(max_completions)
                await update.message.reply_text(
                    f"✅ *Task Created!*\n\n"
                    f"📌 {task['name']}\n"
                    f"💰 {task['amount']} INR\n"
                    f"🎯 Limit: {limit_msg}",
                    parse_mode="Markdown"
                )
                
                clear_user_context(context)
                
            except:
                await update.message.reply_text("❌ Send a valid number!")
    
    elif action == 'add_visit_task':
        step = context.user_data.get('task_step', 1)
        
        if step == 1:
            context.user_data['task_name'] = update.message.text
            context.user_data['task_step'] = 2
            await update.message.reply_text("💰 Send reward amount (INR):")
            
        elif step == 2:
            try:
                amount = float(update.message.text)
                context.user_data['task_amount'] = amount
                context.user_data['task_step'] = 3
                await update.message.reply_text("⏱️ Send time required (seconds):")
            except:
                await update.message.reply_text("❌ Send a valid number!")
                
        elif step == 3:
            try:
                visit_time = int(update.message.text)
                context.user_data['visit_time'] = visit_time
                context.user_data['task_step'] = 4
                await update.message.reply_text("🔗 Send website link:")
            except:
                await update.message.reply_text("❌ Send a valid number!")
                
        elif step == 4:
            context.user_data['task_link'] = update.message.text
            context.user_data['task_step'] = 5
            await update.message.reply_text("📸 Send task image OR type 'skip':")
            
        elif step == 5:
            image_id = None
            text = update.message.text
            
            if text.lower() != 'skip':
                if update.message.photo:
                    image_id = update.message.photo[-1].file_id
                else:
                    await update.message.reply_text("Please send a photo or type 'skip'")
                    return
            
            context.user_data['task_image'] = image_id
            context.user_data['task_step'] = 6
            await update.message.reply_text("🎯 Max completions (0 for unlimited):")
            
        elif step == 6:
            try:
                max_completions = int(update.message.text)
                
                task_id = f"visit_{datetime.now().timestamp()}"
                task = {
                    "task_id": task_id,
                    "name": context.user_data['task_name'],
                    "amount": context.user_data['task_amount'],
                    "visit_time": context.user_data['visit_time'],
                    "link": context.user_data['task_link'],
                    "image_id": context.user_data.get('task_image'),
                    "status": "active",
                    "expires_at": datetime.now() + timedelta(days=30),
                    "total_completions": 0,
                    "total_spent": 0,
                    "max_completions": max_completions if max_completions > 0 else None,
                    "created_at": datetime.now()
                }
                
                visit_tasks_collection.insert_one(task)
                
                limit_msg = "Unlimited" if max_completions == 0 else str(max_completions)
                await update.message.reply_text(
                    f"✅ *Visit Task Created!*\n\n"
                    f"📌 {task['name']}\n"
                    f"💰 {task['amount']} INR\n"
                    f"⏱️ {task['visit_time']}s\n"
                    f"🎯 Limit: {limit_msg}",
                    parse_mode="Markdown"
                )
                
                clear_user_context(context)
                
            except:
                await update.message.reply_text("❌ Send a valid number!")

# --- Additional Admin Functions ---
async def pending_submissions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_chat.id):
        return
    
    submissions = list(task_submissions.find({"status": "pending"}).sort("submitted_at", -1).limit(20))
    
    if not submissions:
        await update.message.reply_text("No pending submissions!")
        return
    
    for sub in submissions:
        keyboard = [
            [InlineKeyboardButton("✅ Approve", callback_data=f"approve_sub_{sub['submission_id']}"),
             InlineKeyboardButton("❌ Reject", callback_data=f"reject_sub_{sub['submission_id']}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        message = (
            f"📋 *Pending Submission*\n\n"
            f"👤 User: {sub.get('user_name', 'Unknown')}\n"
            f"🆔 ID: `{sub['user_id']}`\n"
            f"📌 Task: {sub['task_name']}\n"
            f"💰 Amount: {sub['amount']} INR"
        )
        
        try:
            if sub.get('screenshot_id'):
                await update.message.reply_photo(photo=sub['screenshot_id'], caption=message, reply_markup=reply_markup, parse_mode="Markdown")
            else:
                await update.message.reply_text(message, reply_markup=reply_markup, parse_mode="Markdown")
        except:
            await update.message.reply_text(message, reply_markup=reply_markup, parse_mode="Markdown")

async def pending_withdrawals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_chat.id):
        return
    
    withdrawals = list(withdrawals_collection.find({"status": "pending"}).sort("requested_at", -1))
    
    if not withdrawals:
        await update.message.reply_text("No pending withdrawals!")
        return
    
    for wd in withdrawals:
        keyboard = [
            [InlineKeyboardButton("✅ Approve", callback_data=f"approve_wd_{wd['withdrawal_id']}"),
             InlineKeyboardButton("❌ Reject", callback_data=f"reject_wd_{wd['withdrawal_id']}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        message = (
            f"💸 *Withdrawal Request*\n\n"
            f"👤 User: {wd.get('name', 'Unknown')}\n"
            f"💰 Amount: {wd['amount']} INR\n"
            f"💳 Method: {wd['method']}\n"
            f"📝 Details: {wd['details']}"
        )
        
        await update.message.reply_text(message, reply_markup=reply_markup, parse_mode="Markdown")

async def task_analytics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_chat.id):
        return
    
    tasks = list(tasks_collection.find())
    visit_tasks = list(visit_tasks_collection.find())
    
    message = "📊 *Task Analytics*\n\n"
    
    if tasks:
        message += "*Regular Tasks:*\n"
        for task in tasks[:10]:
            message += f"📌 {task['name']}\n"
            message += f"   💰 {task['amount']} INR | ✅ {task.get('total_completions', 0)}\n"
            if task.get('max_completions'):
                message += f"   🎯 Limit: {task['max_completions']}\n"
    
    if visit_tasks:
        message += "\n*Visit Tasks:*\n"
        for task in visit_tasks[:10]:
            message += f"📌 {task['name']}\n"
            message += f"   💰 {task['amount']} INR | ⏱️ {task['visit_time']}s | ✅ {task.get('total_completions', 0)}\n"
    
    await update.message.reply_text(message, parse_mode="Markdown")

async def fraud_alerts_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_chat.id):
        return
    
    alerts = list(fraud_alerts.find({"resolved": False}).sort("timestamp", -1).limit(20))
    
    if not alerts:
        await update.message.reply_text("No active fraud alerts!")
        return
    
    message = "🚫 *Fraud Alerts*\n\n"
    for alert in alerts[:15]:
        message += f"👤 User ID: `{alert['user_id']}`\n"
        message += f"⚠️ Alert: {', '.join(alert['alerts'])}\n"
        message += f"🕐 Time: {alert['timestamp'].strftime('%Y-%m-%d %H:%M')}\n"
        message += "---\n"
    
    await update.message.reply_text(message, parse_mode="Markdown")

# --- Callback Handlers ---
async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    
    if data.startswith("start_task_"):
        task_id = data.replace("start_task_", "")
        await start_task(update, context, task_id)
    elif data.startswith("submit_screenshot_"):
        task_id = data.replace("submit_screenshot_", "")
        await submit_screenshot(update, context, task_id)
    elif data.startswith("visit_task_"):
        task_id = data.replace("visit_task_", "")
        await visit_task(update, context, task_id)
    elif data.startswith("complete_visit_"):
        session_id = data.replace("complete_visit_", "")
        await complete_visit_task(update, context, session_id)
    elif data.startswith("approve_sub_"):
        submission_id = data.replace("approve_sub_", "")
        await approve_submission(update, context, submission_id)
    elif data.startswith("reject_sub_"):
        submission_id = data.replace("reject_sub_", "")
        await reject_submission(update, context, submission_id)
    elif data.startswith("approve_wd_"):
        withdrawal_id = data.replace("approve_wd_", "")
        await approve_withdrawal(update, context, withdrawal_id)
    elif data.startswith("reject_wd_"):
        withdrawal_id = data.replace("reject_wd_", "")
        await reject_withdrawal(update, context, withdrawal_id)
    elif data.startswith("gift_amount_"):
        amount = int(data.replace("gift_amount_", ""))
        await handle_gift_amount(update, context, amount)
    elif data.startswith("withdraw_method_"):
        method = data.replace("withdraw_method_", "")
        await process_withdrawal(update, context, method)
    elif data == "task_next":
        context.user_data['task_page'] = context.user_data.get('task_page', 0) + 1
        await query.message.delete()
        await show_tasks(update, context)
    elif data == "task_prev":
        context.user_data['task_page'] = max(0, context.user_data.get('task_page', 0) - 1)
        await query.message.delete()
        await show_tasks(update, context)

async def approve_submission(update: Update, context: ContextTypes.DEFAULT_TYPE, submission_id):
    query = update.callback_query
    await query.answer()
    
    submission = task_submissions.find_one({"submission_id": submission_id})
    if not submission:
        await query.edit_message_text("❌ Submission not found!")
        return
    
    # Add balance
    update_user_balance(submission['user_id'], submission['amount'])
    
    # Update user stats
    users_collection.update_one(
        {"user_id": submission['user_id']},
        {"$inc": {"tasks_done": 1, "total_earned": submission['amount']}}
    )
    
    # Update submission
    task_submissions.update_one(
        {"submission_id": submission_id},
        {"$set": {"status": "approved", "processed_at": datetime.now()}}
    )
    
    # Update task completion
    update_task_completion(submission['task_id'], 'regular')
    
    await query.edit_message_text(f"✅ Approved! +{submission['amount']} INR added.")
    
    try:
        await context.bot.send_message(
            submission['user_id'],
            f"✅ *Task Approved!*\n\nTask: {submission['task_name']}\n💰 +{submission['amount']} INR",
            parse_mode="Markdown"
        )
    except:
        pass

async def reject_submission(update: Update, context: ContextTypes.DEFAULT_TYPE, submission_id):
    query = update.callback_query
    await query.answer()
    
    submission = task_submissions.find_one({"submission_id": submission_id})
    if not submission:
        await query.edit_message_text("❌ Submission not found!")
        return
    
    task_submissions.update_one(
        {"submission_id": submission_id},
        {"$set": {"status": "rejected", "processed_at": datetime.now()}}
    )
    
    await query.edit_message_text("❌ Submission rejected!")
    
    try:
        await context.bot.send_message(
            submission['user_id'],
            f"❌ *Task Rejected*\n\nTask: {submission['task_name']}\nReason: Invalid proof",
            parse_mode="Markdown"
        )
    except:
        pass

async def approve_withdrawal(update: Update, context: ContextTypes.DEFAULT_TYPE, withdrawal_id):
    query = update.callback_query
    await query.answer()
    
    withdrawal = withdrawals_collection.find_one({"withdrawal_id": withdrawal_id})
    if not withdrawal:
        await query.edit_message_text("❌ Withdrawal not found!")
        return
    
    withdrawals_collection.update_one(
        {"withdrawal_id": withdrawal_id},
        {"$set": {"status": "approved", "processed_at": datetime.now()}}
    )
    
    users_collection.update_one(
        {"user_id": withdrawal['user_id']},
        {"$inc": {"total_withdrawn": withdrawal['amount']}}
    )
    
    await query.edit_message_text(f"✅ Withdrawal approved! Amount: {withdrawal['amount']} INR")
    
    try:
        await context.bot.send_message(
            withdrawal['user_id'],
            f"✅ *Withdrawal Approved!*\n\nAmount: {withdrawal['amount']} INR\nMethod: {withdrawal['method']}",
            parse_mode="Markdown"
        )
    except:
        pass

async def reject_withdrawal(update: Update, context: ContextTypes.DEFAULT_TYPE, withdrawal_id):
    query = update.callback_query
    await query.answer()
    
    withdrawal = withdrawals_collection.find_one({"withdrawal_id": withdrawal_id})
    if not withdrawal:
        await query.edit_message_text("❌ Withdrawal not found!")
        return
    
    withdrawals_collection.update_one(
        {"withdrawal_id": withdrawal_id},
        {"$set": {"status": "rejected", "processed_at": datetime.now()}}
    )
    
    # Refund amount
    update_user_balance(withdrawal['user_id'], withdrawal['amount'])
    
    await query.edit_message_text("❌ Withdrawal rejected! Amount refunded.")
    
    try:
        await context.bot.send_message(
            withdrawal['user_id'],
            f"❌ *Withdrawal Rejected*\n\nAmount: {withdrawal['amount']} INR\nAmount refunded to balance.",
            parse_mode="Markdown"
        )
    except:
        pass

# --- Run the Bot ---
if __name__ == '__main__':
    # Start Flask in background for Railway
    threading.Thread(target=run_flask, daemon=True).start()
    
    app = ApplicationBuilder().token(TOKEN).build()
    
    # Command handlers
    app.add_handler(CommandHandler("start", start))
    
    # Message handlers
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_message))
    
    # Callback query handler
    app.add_handler(CallbackQueryHandler(handle_callback_query))
    
    print("🚀 Bot is running with MongoDB...")
    print("✅ Environment variables loaded securely!")
    print("✅ All features active!")
    print("   - Task System with Screenshot Verification")
    print("   - Visit & Earn with Time Tracking")
    print("   - Daily Bonus System (0.10 INR base, increases with streak)")
    print("   - Leaderboard System")
    print("   - Referral Program")
    print("   - Multiple Withdrawal Methods")
    print("   - Admin Panel")
    print("\n📌 Fixed Issues:")
    print("   - Context clearing on button clicks")
    print("   - Daily bonus proper calculation")
    print("   - Withdrawal flow fixed")
    
    app.run_polling()