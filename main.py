# main.py
import os
import threading
import logging
import json
import asyncio
import hashlib
import re
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
from pymongo import MongoClient
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from functools import wraps

# --- Logging ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Configuration ---
TOKEN = "8384600981:AAHhAm-cD1qjiav6UikKsII4FGNsAwzon2o"
MONGO_URI = "mongodb+srv://Vansh:Vansh000@cluster0.tqmuzxc.mongodb.net/?appName=Cluster0"
ADMIN_TRIGGER = "Vansh@000"

# Security Config
MAX_TASKS_PER_DAY = 50
MAX_VISIT_TASKS_PER_DAY = 30
MAX_WITHDRAWAL_ATTEMPTS = 3
COOLDOWN_BETWEEN_TASKS = 10  # seconds

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
    print("✅ MongoDB Connected Successfully!")
except Exception as e:
    print(f"❌ MongoDB Connection Error: {e}")
    client = None

# --- Flask for Railway ---
server = Flask(__name__)

@server.route('/')
def health_check():
    return jsonify({"status": "Bot is Live with MongoDB!", "timestamp": datetime.now().isoformat()}), 200

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
    ['🗑️ Clear Chat', '❓ Help']
]

ADMIN_KEYBOARD = [
    ['📊 Dashboard', '👥 User Stats'],
    ['💰 Financial Stats', '💸 Withdrawal Requests'],
    ['📋 Pending Submissions', '📢 Broadcast'],
    ['➕ Add Task', '➕ Add Visit Task'],
    ['📜 All Tasks', '📊 Task Analytics'],
    ['🚫 Fraud Alerts', '🔙 Exit Admin']
]

WITHDRAWAL_METHODS = ['UPI', 'Bank Transfer', 'Crypto (Bitcoin)', 'Google Play Gift Card', 'Amazon Gift Card']
WITHDRAWAL_LIMITS = {
    'UPI': 10,
    'Bank Transfer': 50,
    'Crypto (Bitcoin)': 150,
    'Google Play Gift Card': 10,
    'Amazon Gift Card': 10
}

GOOGLE_PLAY_AMOUNTS = [10, 20, 25, 50, 100]
AMAZON_AMOUNTS = [10, 20, 25, 50, 100]

# --- Security Helper Functions ---
def get_user(chat_id):
    return users_collection.find_one({"user_id": chat_id})

def update_user_balance(chat_id, amount):
    users_collection.update_one(
        {"user_id": chat_id},
        {"$inc": {"balance": amount}}
    )

def is_admin(chat_id):
    user = get_user(chat_id)
    return user and user.get('is_admin', False)

def detect_fraud(user_id, action_type):
    """Detect potential fraud activities"""
    now = datetime.now()
    alerts = []
    
    # Check for rapid task completion
    recent_tasks = list(user_task_history.find({
        "user_id": user_id,
        "completed_at": {"$gt": now - timedelta(minutes=5)}
    }))
    
    if len(recent_tasks) > 10:
        alerts.append("Rapid task completion detected")
    
    # Check for multiple withdrawals in short time
    recent_withdrawals = list(withdrawals_collection.find({
        "user_id": user_id,
        "requested_at": {"$gt": now - timedelta(hours=1)}
    }))
    
    if len(recent_withdrawals) > 3:
        alerts.append("Multiple withdrawal attempts in short time")
    
    # Check for suspicious IP/device (simplified)
    session = user_sessions.find_one({"user_id": user_id})
    if session and session.get('session_count', 0) > 5:
        alerts.append("Multiple active sessions")
    
    if alerts:
        fraud_alerts.insert_one({
            "user_id": user_id,
            "alerts": alerts,
            "action_type": action_type,
            "timestamp": now,
            "resolved": False
        })
        
        # Notify admins
        return True
    return False

def check_rate_limit(user_id, action, limit, time_window_minutes=1440):
    """Check rate limits for actions"""
    now = datetime.now()
    cutoff = now - timedelta(minutes=time_window_minutes)
    
    if action == "task":
        count = user_task_history.count_documents({
            "user_id": user_id,
            "completed_at": {"$gt": cutoff}
        })
    elif action == "visit_task":
        count = user_visit_history.count_documents({
            "user_id": user_id,
            "completed_at": {"$gt": cutoff}
        })
    elif action == "withdrawal":
        count = withdrawals_collection.count_documents({
            "user_id": user_id,
            "requested_at": {"$gt": cutoff},
            "status": {"$in": ["pending", "approved"]}
        })
    else:
        return True
    
    return count < limit

# --- Bot Functions ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    args = context.args
    
    user_in_db = users_collection.find_one({"user_id": chat_id})
    
    # Remove any Vansh@000 from display
    if not user_in_db:
        referred_by = None
        if args and args[0].isdigit() and args[0] != str(chat_id):
            referred_by = int(args[0])
            ref_user = get_user(referred_by)
            if ref_user and ref_user.get('is_admin') == False:  # Don't give admin referral bonus
                update_user_balance(referred_by, 2.0)
                users_collection.update_one(
                    {"user_id": referred_by},
                    {"$inc": {"referrals": 1}}
                )
                try:
                    await context.bot.send_message(
                        referred_by,
                        f"🎉 New user joined using your referral link! +2 INR added to your balance."
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
            "last_active": datetime.now()
        }
        users_collection.insert_one(new_user)
        welcome_msg = f"👋 Hello {user.first_name}! Welcome to the Earning Bot!\n\n🎁 Complete tasks and earn money!\n💰 Earn 2 INR per referral!"
        
        if referred_by:
            welcome_msg += f"\n\n✅ You were referred by a user!"
    else:
        welcome_msg = f"👋 Welcome back {user.first_name}!\n💰 Your balance: {user_in_db.get('balance', 0):.2f} INR"
        users_collection.update_one({"user_id": chat_id}, {"$set": {"last_active": datetime.now()}})

    reply_markup = ReplyKeyboardMarkup(USER_KEYBOARD, resize_keyboard=True)
    await update.message.reply_text(welcome_msg, reply_markup=reply_markup)

async def clear_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear chat messages (simulated - Telegram doesn't allow bulk delete)"""
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        "🗑️ *Clear Chat Feature*\n\n"
        "To clear your chat:\n"
        "1. Open Telegram settings\n"
        "2. Go to 'Clear History'\n"
        "3. Select 'Clear All'\n\n"
        "Or simply start a new chat with /start",
        parse_mode="Markdown"
    )

# --- Task Functions with Fixes ---
async def show_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    page = context.user_data.get('task_page', 0)
    tasks_per_page = 5
    
    # Get completed task IDs - FIXED: Don't show completed tasks
    completed_tasks = [t['task_id'] for t in user_task_history.find({
        "user_id": chat_id,
        "status": {"$in": ["approved", "pending"]}
    })]
    
    # Also get pending submissions
    pending_submissions_list = [s['task_id'] for s in task_submissions.find({
        "user_id": chat_id,
        "status": "pending"
    })]
    
    excluded_tasks = list(set(completed_tasks + pending_submissions_list))
    
    tasks = list(tasks_collection.find({
        "status": "active",
        "expires_at": {"$gt": datetime.now()},
        "task_id": {"$nin": excluded_tasks}
    }).skip(page * tasks_per_page).limit(tasks_per_page))
    
    if not tasks:
        if page == 0:
            await update.message.reply_text("📝 No tasks available at the moment. Check back later!")
        else:
            await update.message.reply_text("No more tasks!")
        return
    
    for task in tasks:
        # Create inline keyboard with screenshot upload button
        keyboard = [
            [InlineKeyboardButton("🎯 Start Task", callback_data=f"start_task_{task['task_id']}")],
            [InlineKeyboardButton("📸 Submit Screenshot", callback_data=f"submit_screenshot_{task['task_id']}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        message = f"📌 *{task['name']}*\n\n"
        message += f"💰 *Reward:* {task['amount']} INR\n"
        message += f"📝 *Description:* {task['description']}\n"
        message += f"⏰ *Expires:* {task['expires_at'].strftime('%Y-%m-%d %H:%M')}"
        
        if task.get('image_id'):
            try:
                await update.message.reply_photo(
                    photo=task['image_id'],
                    caption=message,
                    reply_markup=reply_markup,
                    parse_mode="Markdown"
                )
            except:
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
        "task_id": {"$nin": excluded_tasks}
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
    
    # Check rate limit
    if not check_rate_limit(chat_id, "task", MAX_TASKS_PER_DAY):
        await query.edit_message_text("❌ You've reached the daily task limit! Please try tomorrow.")
        return
    
    task = tasks_collection.find_one({"task_id": task_id})
    
    if not task or task['status'] != 'active' or task['expires_at'] < datetime.now():
        await query.edit_message_text("❌ This task is no longer available!")
        return
    
    # Check if already completed or pending
    existing = user_task_history.find_one({"user_id": chat_id, "task_id": task_id, "status": {"$in": ["approved", "pending"]}})
    if existing:
        await query.edit_message_text("❌ You've already completed or submitted this task!")
        return
    
    # Store task in context
    context.user_data['current_task'] = task_id
    
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
    """Handle screenshot submission via button"""
    query = update.callback_query
    await query.answer()
    
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
    
    # Check if already submitted
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
    
    # Check rate limit for submissions
    recent_submissions = task_submissions.count_documents({
        "user_id": chat_id,
        "submitted_at": {"$gt": datetime.now() - timedelta(hours=1)}
    })
    if recent_submissions > 10:
        await update.message.reply_text("❌ Too many submissions! Please wait before submitting more.")
        context.user_data['awaiting_screenshot'] = False
        return
    
    # Get the largest photo
    photo = update.message.photo[-1]
    file_id = photo.file_id
    
    # Create unique submission ID with hash for security
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
        "submitted_at": datetime.now(),
        "ip_hash": hashlib.md5(str(chat_id).encode()).hexdigest()  # Simple fingerprint
    }
    
    task_submissions.insert_one(submission)
    
    # Also add to user history as pending
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
        f"You will be notified once approved/rejected.\n\n"
        f"📌 Check 'Task History' for status updates.",
        parse_mode="Markdown"
    )
    
    # Notify admins
    admins = users_collection.find({"is_admin": True})
    for admin in admins:
        try:
            # Send compressed notification
            await context.bot.send_message(
                admin['user_id'],
                f"📋 *New Task Submission!*\n\n"
                f"👤 User: {update.effective_user.first_name}\n"
                f"🆔 ID: `{chat_id}`\n"
                f"📌 Task: {task['name']}\n"
                f"💰 Amount: {task['amount']} INR\n"
                f"🕐 Time: {datetime.now().strftime('%H:%M:%S')}",
                parse_mode="Markdown"
            )
        except:
            pass

# --- Visit Task Functions with Timer ---
async def show_visit_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    page = context.user_data.get('visit_page', 0)
    tasks_per_page = 5
    
    # Get active visit tasks
    user_completed_today = [v['task_id'] for v in user_visit_history.find({
        "user_id": chat_id,
        "completed_at": {"$gt": datetime.now() - timedelta(hours=24)}
    })]
    
    tasks = list(visit_tasks_collection.find({
        "status": "active",
        "expires_at": {"$gt": datetime.now()}
    }).skip(page * tasks_per_page).limit(tasks_per_page))
    
    # Filter out tasks completed in last 24 hours
    available_tasks = [t for t in tasks if t['task_id'] not in user_completed_today]
    
    if not available_tasks:
        await update.message.reply_text("🔗 No visit tasks available at the moment! Check back later.")
        return
    
    for task in available_tasks:
        keyboard = [[InlineKeyboardButton("🔗 Start Visit Task", callback_data=f"start_visit_{task['task_id']}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        message = f"🔗 *{task['name']}*\n\n"
        message += f"💰 *Reward:* {task['amount']} INR\n"
        message += f"⏱️ *Time Required:* {task['visit_time']} seconds\n"
        message += f"🔄 *Cooldown:* 24 hours"
        
        if task.get('image_id'):
            try:
                await update.message.reply_photo(photo=task['image_id'], caption=message, reply_markup=reply_markup, parse_mode="Markdown")
            except:
                await update.message.reply_text(message, reply_markup=reply_markup, parse_mode="Markdown")
        else:
            await update.message.reply_text(message, reply_markup=reply_markup, parse_mode="Markdown")

async def start_visit_task(update: Update, context: ContextTypes.DEFAULT_TYPE, task_id):
    query = update.callback_query
    await query.answer()
    chat_id = query.from_user.id
    
    # Check rate limit
    if not check_rate_limit(chat_id, "visit_task", MAX_VISIT_TASKS_PER_DAY):
        await query.edit_message_text("❌ You've reached the daily visit task limit! Please try tomorrow.")
        return
    
    task = visit_tasks_collection.find_one({"task_id": task_id})
    
    if not task or task['status'] != 'active' or task['expires_at'] < datetime.now():
        await query.edit_message_text("❌ This task is no longer available!")
        return
    
    # Check if user completed in last 24 hours
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
    
    # Create timer message
    timer_msg = await query.edit_message_text(
        f"🔗 *{task['name']}*\n\n"
        f"⏱️ *Timer: {task['visit_time']} seconds remaining*\n\n"
        f"Please visit the website and stay for the required time.\n\n"
        f"⚠️ *Warning:* If you leave early, the task will be invalid!\n"
        f"💰 *Reward:* {task['amount']} INR",
        parse_mode="Markdown"
    )
    
    context.user_data['current_visit_task'] = task_id
    context.user_data['visit_start_time'] = datetime.now()
    context.user_data['visit_timer_message_id'] = timer_msg.message_id
    context.user_data['visit_required_time'] = task['visit_time']
    
    # Send link button separately
    keyboard = [[InlineKeyboardButton("🔗 Visit Website", url=task['link'])]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await context.bot.send_message(
        chat_id,
        f"🔗 Click below to visit the website:\n\n⏱️ You need to stay for {task['visit_time']} seconds!",
        reply_markup=reply_markup
    )
    
    # Store the original query for completion
    context.user_data['visit_query'] = query
    
    # Start timer countdown
    context.user_data['visit_end_time'] = datetime.now() + timedelta(seconds=task['visit_time'])
    
    # Send complete button after time
    asyncio.create_task(send_complete_button_after_delay(context, chat_id, task['visit_time']))

async def send_complete_button_after_delay(context: ContextTypes.DEFAULT_TYPE, chat_id: int, delay_seconds: int):
    """Send completion button after the required time"""
    await asyncio.sleep(delay_seconds)
    
    # Check if task is still active
    if context.user_data.get('current_visit_task') and context.user_data.get('visit_end_time'):
        keyboard = [[InlineKeyboardButton("✅ Complete Task", callback_data="complete_visit")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await context.bot.send_message(
            chat_id,
            f"✅ *Time completed!*\n\n"
            f"Click the button below to claim your reward:",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )

async def complete_visit_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.from_user.id
    
    if not context.user_data.get('current_visit_task'):
        await query.edit_message_text("❌ No active visit task!")
        return
    
    task_id = context.user_data.get('current_visit_task')
    start_time = context.user_data.get('visit_start_time')
    end_time = context.user_data.get('visit_end_time')
    
    if not task_id or not start_time:
        await query.edit_message_text("❌ Task data missing! Please start again.")
        return
    
    task = visit_tasks_collection.find_one({"task_id": task_id})
    if not task:
        await query.edit_message_text("❌ Task no longer exists!")
        return
    
    # Check if timer completed
    now = datetime.now()
    time_elapsed = (now - start_time).total_seconds()
    
    if time_elapsed >= task['visit_time']:
        # Check for fraud
        if detect_fraud(chat_id, "visit_task_complete"):
            await query.edit_message_text(
                "⚠️ *Suspicious Activity Detected!*\n\n"
                "Your activity has been flagged for review.\n"
                "Please contact support.",
                parse_mode="Markdown"
            )
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
            "task_id": task_id,
            "task_name": task['name'],
            "amount": task['amount'],
            "completed_at": datetime.now(),
            "time_spent": time_elapsed
        })
        
        # Update task analytics
        visit_tasks_collection.update_one(
            {"task_id": task_id},
            {"$inc": {"total_completions": 1, "total_spent": task['amount']}}
        )
        
        await query.edit_message_text(
            f"✅ *Task Completed!*\n\n"
            f"Task: {task['name']}\n"
            f"💰 +{task['amount']} INR added to your balance!\n\n"
            f"You can complete this task again after 24 hours.",
            parse_mode="Markdown"
        )
        
        # Show new balance
        user = get_user(chat_id)
        await context.bot.send_message(chat_id, f"💰 Your new balance: {user.get('balance', 0):.2f} INR")
        
    else:
        await query.edit_message_text(
            f"❌ *Task Failed!*\n\n"
            f"You completed the task too early!\n"
            f"Required time: {task['visit_time']} seconds\n"
            f"You completed after: {int(time_elapsed)} seconds\n\n"
            f"Please try again and wait for the full time.",
            parse_mode="Markdown"
        )
    
    # Clear context
    context.user_data.pop('current_visit_task', None)
    context.user_data.pop('visit_start_time', None)
    context.user_data.pop('visit_end_time', None)

# --- Withdrawal Functions with Custom Amounts ---
async def withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = get_user(chat_id)
    
    # Check rate limit
    if not check_rate_limit(chat_id, "withdrawal", MAX_WITHDRAWAL_ATTEMPTS, 60):
        await update.message.reply_text("❌ Too many withdrawal attempts! Please try again later.")
        return
    
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
    chat_id = query.from_user.id
    
    context.user_data['withdrawal_method'] = method
    context.user_data['awaiting_withdrawal_details'] = True
    
    if method == 'Google Play Gift Card' or method == 'Amazon Gift Card':
        # Show predefined amounts
        amounts = GOOGLE_PLAY_AMOUNTS if method == 'Google Play Gift Card' else AMAZON_AMOUNTS
        keyboard = []
        for amount in amounts:
            keyboard.append([InlineKeyboardButton(f"{amount} INR", callback_data=f"gift_amount_{amount}")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            f"💸 *{method} Withdrawal*\n\n"
            f"Select amount:\n"
            f"Minimum: {WITHDRAWAL_LIMITS[method]} INR",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
        context.user_data['awaiting_gift_amount'] = True
    else:
        method_details = {
            'UPI': 'Please send your UPI ID (e.g., name@okhdfcbank)',
            'Bank Transfer': 'Please send in this format:\n🏦 Bank Name\n🔢 Account Number\n🔑 IFSC Code\n👤 Account Holder Name',
            'Crypto (Bitcoin)': 'Please send your Bitcoin wallet address'
        }
        
        await query.edit_message_text(
            f"💸 *{method} Withdrawal*\n\n"
            f"{method_details[method]}\n\n"
            f"Minimum amount: {WITHDRAWAL_LIMITS[method]} INR\n\n"
            f"Please send your details:",
            parse_mode="Markdown"
        )

async def handle_gift_amount(update: Update, context: ContextTypes.DEFAULT_TYPE, amount):
    query = update.callback_query
    await query.answer()
    
    context.user_data['withdrawal_amount'] = amount
    context.user_data['awaiting_gift_amount'] = False
    context.user_data['awaiting_withdrawal_details'] = True
    
    method = context.user_data.get('withdrawal_method')
    
    await query.edit_message_text(
        f"💸 *{method} Withdrawal*\n\n"
        f"Amount: {amount} INR\n\n"
        f"Please send your email address for the gift card:",
        parse_mode="Markdown"
    )

async def handle_withdrawal_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    
    if not context.user_data.get('awaiting_withdrawal_details'):
        return
    
    method = context.user_data.get('withdrawal_method')
    details = update.message.text
    user = get_user(chat_id)
    balance = user.get('balance', 0)
    min_amount = WITHDRAWAL_LIMITS[method]
    
    # Get amount (either predefined or custom)
    if method in ['Google Play Gift Card', 'Amazon Gift Card']:
        amount = context.user_data.get('withdrawal_amount')
        if not amount:
            await update.message.reply_text("❌ Invalid amount selection!")
            return
    else:
        # For UPI, Bank, Crypto - ask for amount
        context.user_data['withdrawal_details'] = details
        context.user_data['awaiting_withdrawal_amount'] = True
        
        await update.message.reply_text(
            f"💰 Your balance: {balance:.2f} INR\n"
            f"Minimum withdrawal: {min_amount} INR\n\n"
            f"Please enter the amount you want to withdraw (custom amount):"
        )
        return
    
    # Process fixed amount withdrawal for gift cards
    if amount < min_amount:
        await update.message.reply_text(f"❌ Amount must be at least {min_amount} INR!")
        context.user_data['awaiting_withdrawal_details'] = False
        return
    
    if amount > balance:
        await update.message.reply_text(f"❌ Insufficient balance! Your balance: {balance:.2f} INR")
        context.user_data['awaiting_withdrawal_details'] = False
        return
    
    # Validate email
    if not re.match(r"[^@]+@[^@]+\.[^@]+", details):
        await update.message.reply_text("❌ Please send a valid email address!")
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
    
    # Deduct from balance
    update_user_balance(chat_id, -amount)
    
    context.user_data['awaiting_withdrawal_details'] = False
    context.user_data['awaiting_withdrawal_amount'] = False
    context.user_data['withdrawal_method'] = None
    context.user_data['withdrawal_details'] = None
    context.user_data['withdrawal_amount'] = None
    
    await update.message.reply_text(
        f"✅ Withdrawal request submitted!\n\n"
        f"Amount: {amount} INR\n"
        f"Method: {method}\n"
        f"Details: {details}\n\n"
        f"Your request is pending admin approval.\n"
        f"You will be notified once processed."
    )
    
    # Notify admins
    admins = users_collection.find({"is_admin": True})
    for admin in admins:
        try:
            await context.bot.send_message(
                admin['user_id'],
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

async def process_withdrawal_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    
    if not context.user_data.get('awaiting_withdrawal_amount'):
        return
    
    try:
        amount = float(update.message.text)
        method = context.user_data.get('withdrawal_method')
        details = context.user_data.get('withdrawal_details')
        user = get_user(chat_id)
        balance = user.get('balance', 0)
        min_amount = WITHDRAWAL_LIMITS[method]
        
        if amount < min_amount:
            await update.message.reply_text(f"❌ Amount must be at least {min_amount} INR!")
            return
        
        if amount > balance:
            await update.message.reply_text(f"❌ Insufficient balance! Your balance: {balance:.2f} INR")
            return
        
        # Additional validation for UPI
        if method == 'UPI':
            if not re.match(r"^[a-zA-Z0-9.\-_]{2,256}@[a-zA-Z]{3,}$", details):
                await update.message.reply_text("❌ Invalid UPI ID format! Please send valid UPI ID (e.g., name@okhdfcbank)")
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
        
        # Deduct from balance
        update_user_balance(chat_id, -amount)
        
        context.user_data['awaiting_withdrawal_amount'] = False
        context.user_data['awaiting_withdrawal_details'] = False
        context.user_data['withdrawal_method'] = None
        context.user_data['withdrawal_details'] = None
        
        await update.message.reply_text(
            f"✅ Withdrawal request submitted!\n\n"
            f"Amount: {amount} INR\n"
            f"Method: {method}\n\n"
            f"Your request is pending admin approval.\n"
            f"You will be notified once processed."
        )
        
        # Notify admins
        admins = users_collection.find({"is_admin": True})
        for admin in admins:
            try:
                await context.bot.send_message(
                    admin['user_id'],
                    f"💸 *New Withdrawal Request!*\n\n"
                    f"👤 User: {update.effective_user.first_name}\n"
                    f"🆔 ID: `{chat_id}`\n"
                    f"💰 Amount: {amount} INR\n"
                    f"💳 Method: {method}",
                    parse_mode="Markdown"
                )
            except:
                pass
                
    except ValueError:
        await update.message.reply_text("❌ Please enter a valid number!")

# --- Admin Functions with Screenshot Display ---
async def pending_submissions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_chat.id):
        return
    
    submissions = list(task_submissions.find({"status": "pending"}).sort("submitted_at", -1).limit(20))
    
    if not submissions:
        await update.message.reply_text("📋 No pending submissions!")
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
            f"💰 Amount: {sub['amount']} INR\n"
            f"🕐 Submitted: {sub['submitted_at'].strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"🔒 Fingerprint: `{sub.get('ip_hash', 'N/A')[:8]}...`"
        )
        
        try:
            if sub.get('screenshot_id'):
                await update.message.reply_photo(
                    photo=sub['screenshot_id'],
                    caption=message,
                    reply_markup=reply_markup,
                    parse_mode="Markdown"
                )
            else:
                await update.message.reply_text(message, reply_markup=reply_markup, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Error sending screenshot: {e}")
            await update.message.reply_text(message, reply_markup=reply_markup, parse_mode="Markdown")

async def handle_submission_decision(update: Update, context: ContextTypes.DEFAULT_TYPE, submission_id, decision):
    query = update.callback_query
    await query.answer()
    
    submission = task_submissions.find_one({"submission_id": submission_id})
    if not submission:
        await query.edit_message_text("❌ Submission not found!")
        return
    
    if decision == "approve":
        # Add balance
        update_user_balance(submission['user_id'], submission['amount'])
        
        # Update user stats
        users_collection.update_one(
            {"user_id": submission['user_id']},
            {
                "$inc": {
                    "tasks_done": 1,
                    "total_earned": submission['amount']
                }
            }
        )
        
        # Update submission
        task_submissions.update_one(
            {"submission_id": submission_id},
            {
                "$set": {
                    "status": "approved",
                    "processed_at": datetime.now(),
                    "processed_by": query.from_user.id
                }
            }
        )
        
        # Update user history
        user_task_history.update_one(
            {"user_id": submission['user_id'], "task_id": submission['task_id'], "status": "pending"},
            {
                "$set": {
                    "status": "approved",
                    "completed_at": datetime.now()
                }
            },
            upsert=True
        )
        
        # Update task completion count
        tasks_collection.update_one(
            {"task_id": submission['task_id']},
            {"$inc": {"total_completions": 1, "total_spent": submission['amount']}}
        )
        
        await query.edit_message_text(f"✅ Submission approved! +{submission['amount']} INR added.")
        
        # Notify user
        try:
            await context.bot.send_message(
                submission['user_id'],
                f"✅ *Task Approved!*\n\n"
                f"Task: {submission['task_name']}\n"
                f"💰 +{submission['amount']} INR added to your balance!\n\n"
                f"Current balance: {get_user(submission['user_id']).get('balance', 0):.2f} INR",
                parse_mode="Markdown"
            )
        except:
            pass
            
    else:  # reject
        task_submissions.update_one(
            {"submission_id": submission_id},
            {
                "$set": {
                    "status": "rejected",
                    "processed_at": datetime.now(),
                    "processed_by": query.from_user.id,
                    "rejection_reason": "Invalid proof"
                }
            }
        )
        
        user_task_history.update_one(
            {"user_id": submission['user_id'], "task_id": submission['task_id'], "status": "pending"},
            {
                "$set": {
                    "status": "rejected",
                    "completed_at": datetime.now()
                }
            },
            upsert=True
        )
        
        await query.edit_message_text("❌ Submission rejected!")
        
        # Notify user
        try:
            await context.bot.send_message(
                submission['user_id'],
                f"❌ *Task Rejected*\n\n"
                f"Task: {submission['task_name']}\n"
                f"Reason: Invalid proof provided.\n\n"
                f"Please try again with valid proof.",
                parse_mode="Markdown"
            )
        except:
            pass

async def fraud_alerts_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_chat.id):
        return
    
    alerts = list(fraud_alerts.find({"resolved": False}).sort("timestamp", -1).limit(20))
    
    if not alerts:
        await update.message.reply_text("No fraud alerts detected!")
        return
    
    message = "🚫 *Fraud Alerts*\n\n"
    for alert in alerts:
        message += f"👤 User ID: `{alert['user_id']}`\n"
        message += f"⚠️ Alerts: {', '.join(alert['alerts'])}\n"
        message += f"🕐 Time: {alert['timestamp'].strftime('%Y-%m-%d %H:%M')}\n"
        message += f"🔧 Action: {alert['action_type']}\n"
        message += "---\n"
    
    await update.message.reply_text(message, parse_mode="Markdown")

# --- Main Message Handler ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    chat_id = update.effective_chat.id
    
    # Hide sensitive triggers from users
    if text == ADMIN_TRIGGER:
        # Set as admin but don't expose the trigger word
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
    if context.user_data.get('awaiting_withdrawal_details'):
        await handle_withdrawal_details(update, context)
        return
    
    if context.user_data.get('awaiting_withdrawal_amount'):
        await process_withdrawal_amount(update, context)
        return
    
    # Check if it's a screenshot submission
    if context.user_data.get('awaiting_screenshot') and update.message.photo:
        await handle_screenshot(update, context)
        return
    
    # Menu handlers
    if text == '🗑️ Clear Chat':
        await clear_chat(update, context)
    elif text == '📝 Tasks':
        await show_tasks(update, context)
    elif text == '🔗 Visit & Earn':
        await show_visit_tasks(update, context)
    elif text == '💰 My Balance':
        user = get_user(chat_id)
        bal = user.get('balance', 0.0)
        await update.message.reply_text(f"💰 *Your Current Balance:* {bal:.2f} INR", parse_mode="Markdown")
    elif text == '💸 Withdraw':
        await withdraw(update, context)
    elif text == '📊 My Stats':
        user = get_user(chat_id)
        stats = (
            f"📊 *User Statistics*\n\n"
            f"👤 Name: {user.get('name')}\n"
            f"💰 Balance: {user.get('balance', 0):.2f} INR\n"
            f"👥 Total Referrals: {user.get('referrals', 0)}\n"
            f"📝 Tasks Completed: {user.get('tasks_done', 0)}\n"
            f"🔗 Visit Tasks Done: {user.get('visit_tasks_done', 0)}\n"
            f"💵 Total Earned: {user.get('total_earned', 0):.2f} INR\n"
            f"💸 Total Withdrawn: {user.get('total_withdrawn', 0):.2f} INR"
        )
        await update.message.reply_text(stats, parse_mode="Markdown")
    elif text == '👥 Referral Program':
        bot_info = await context.bot.get_me()
        ref_link = f"https://t.me/{bot_info.username}?start={chat_id}"
        await update.message.reply_text(
            f"👥 *Referral Program*\n\n"
            f"Invite your friends and earn 2 INR per referral!\n\n"
            f"✨ *Your Referral Link:*\n`{ref_link}`\n\n"
            f"📊 Total Referrals: {get_user(chat_id).get('referrals', 0)}\n"
            f"💰 Total Earned: {get_user(chat_id).get('referrals', 0) * 2} INR",
            parse_mode="Markdown"
        )
    elif text == '📜 Task History':
        await task_history(update, context)
    elif text == '💳 Withdrawal History':
        await withdrawal_history(update, context)
    elif text == '❓ Help':
        help_text = (
            "❓ *Help Guide*\n\n"
            "📝 *Tasks:* Complete and submit screenshot proof\n"
            "🔗 *Visit & Earn:* Stay on website for required time\n"
            "👥 *Referral:* Invite friends (2 INR each)\n"
            "💰 *Withdraw:* UPI (min 10), Bank (min 50), Crypto (min 150), Gift Cards (min 10)\n"
            "🗑️ *Clear Chat:* Clear your chat history\n\n"
            "*Support:* Contact @support"
        )
        await update.message.reply_text(help_text, parse_mode="Markdown")
    elif text == 'ℹ️ About':
        about_text = (
            "ℹ️ *About This Bot*\n\n"
            "🤖 Version: 3.0\n"
            "💰 Secure earning platform\n"
            "🔒 Advanced fraud detection\n"
            "✅ Instant notifications\n\n"
            "*Features:*\n"
            "• Task completion with verification\n"
            "• Timed visit tasks\n"
            "• Referral program\n"
            "• Multiple withdrawal methods\n"
            "• Real-time fraud monitoring"
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
            await broadcast(update, context)
        elif text == '📊 Task Analytics':
            await task_analytics(update, context)
        elif text == '🚫 Fraud Alerts':
            await fraud_alerts_view(update, context)
        elif text == '👥 User Stats':
            total_users = users_collection.count_documents({})
            active_today = users_collection.count_documents({"last_active": {"$gt": datetime.now() - timedelta(days=1)}})
            await update.message.reply_text(
                f"👥 *User Statistics*\n\n"
                f"Total Users: {total_users}\n"
                f"Active Today: {active_today}",
                parse_mode="Markdown"
            )
        elif text == '💰 Financial Stats':
            total_earned = users_collection.aggregate([{"$group": {"_id": None, "total": {"$sum": "$total_earned"}}}]).next().get('total', 0)
            total_withdrawn = users_collection.aggregate([{"$group": {"_id": None, "total": {"$sum": "$total_withdrawn"}}}]).next().get('total', 0)
            platform_balance = total_earned - total_withdrawn
            await update.message.reply_text(
                f"💰 *Financial Statistics*\n\n"
                f"Total Earned: {total_earned:.2f} INR\n"
                f"Total Withdrawn: {total_withdrawn:.2f} INR\n"
                f"Platform Balance: {platform_balance:.2f} INR",
                parse_mode="Markdown"
            )
        elif text == '📜 All Tasks':
            tasks = list(tasks_collection.find())
            if not tasks:
                await update.message.reply_text("No tasks found!")
                return
            msg = "*All Tasks*\n\n"
            for task in tasks[:20]:
                msg += f"📌 {task['name']} - {task['amount']} INR\n"
            await update.message.reply_text(msg, parse_mode="Markdown")

# Task History function (for users)
async def task_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    
    submissions = list(task_submissions.find({"user_id": chat_id}).sort("submitted_at", -1).limit(20))
    
    if not submissions:
        await update.message.reply_text("📜 No task history found!")
        return
    
    # Group by status
    pending = [s for s in submissions if s['status'] == 'pending']
    approved = [s for s in submissions if s['status'] == 'approved']
    rejected = [s for s in submissions if s['status'] == 'rejected']
    
    message = "📜 *Your Task History*\n\n"
    
    if pending:
        message += "*⏳ Pending Tasks:*\n"
        for sub in pending:
            message += f"• {sub['task_name']} - {sub['amount']} INR\n"
        message += "\n"
    
    if approved:
        message += "*✅ Approved Tasks:*\n"
        for sub in approved:
            message += f"• {sub['task_name']} - +{sub['amount']} INR\n"
        message += "\n"
    
    if rejected:
        message += "*❌ Rejected Tasks:*\n"
        for sub in rejected:
            message += f"• {sub['task_name']} - {sub['amount']} INR\n"
    
    await update.message.reply_text(message, parse_mode="Markdown")

# Withdrawal History function (for users)
async def withdrawal_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    
    withdrawals = list(withdrawals_collection.find({"user_id": chat_id}).sort("requested_at", -1).limit(20))
    
    if not withdrawals:
        await update.message.reply_text("💳 No withdrawal history found!")
        return
    
    message = "💳 *Your Withdrawal History*\n\n"
    for wd in withdrawals:
        status_emoji = {
            'pending': '⏳',
            'approved': '✅',
            'rejected': '❌'
        }.get(wd['status'], '❓')
        
        message += f"{status_emoji} *{wd['amount']} INR* - {wd['method']}\n"
        message += f"   Status: {wd['status'].upper()}\n"
        message += f"   Date: {wd['requested_at'].strftime('%Y-%m-%d')}\n\n"
    
    await update.message.reply_text(message, parse_mode="Markdown")

# Admin Dashboard
async def admin_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_chat.id):
        return
    
    total_users = users_collection.count_documents({})
    active_users = users_collection.count_documents({"last_active": {"$gt": datetime.now() - timedelta(days=7)}})
    total_earned = users_collection.aggregate([{"$group": {"_id": None, "total": {"$sum": "$total_earned"}}}]).next().get('total', 0)
    total_withdrawn = users_collection.aggregate([{"$group": {"_id": None, "total": {"$sum": "$total_withdrawn"}}}]).next().get('total', 0)
    pending_submissions = task_submissions.count_documents({"status": "pending"})
    pending_withdrawals = withdrawals_collection.count_documents({"status": "pending"})
    fraud_count = fraud_alerts.count_documents({"resolved": False})
    
    dashboard = (
        f"📊 *Admin Dashboard*\n\n"
        f"👥 Total Users: {total_users}\n"
        f"🟢 Active Users (7d): {active_users}\n"
        f"💰 Total Earned: {total_earned:.2f} INR\n"
        f"💸 Total Withdrawn: {total_withdrawn:.2f} INR\n"
        f"📋 Pending Submissions: {pending_submissions}\n"
        f"💸 Pending Withdrawals: {pending_withdrawals}\n"
        f"🚫 Fraud Alerts: {fraud_count}\n\n"
        f"📈 Platform Balance: {total_earned - total_withdrawn:.2f} INR"
    )
    
    await update.message.reply_text(dashboard, parse_mode="Markdown")

# Add Task (Admin)
async def add_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_chat.id):
        return
    
    context.user_data['admin_action'] = 'add_task'
    context.user_data['task_step'] = 1
    await update.message.reply_text(
        "📝 *Add New Task*\n\n"
        "Please send the task name:",
        parse_mode="Markdown"
    )

async def add_visit_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_chat.id):
        return
    
    context.user_data['admin_action'] = 'add_visit_task'
    context.user_data['task_step'] = 1
    await update.message.reply_text(
        "🔗 *Add New Visit Task*\n\n"
        "Please send the task name:",
        parse_mode="Markdown"
    )

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_chat.id):
        return
    
    context.user_data['admin_action'] = 'broadcast'
    await update.message.reply_text(
        "📢 *Send Broadcast Message*\n\n"
        "Please send the message you want to broadcast to all users:",
        parse_mode="Markdown"
    )

async def task_analytics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_chat.id):
        return
    
    tasks = list(tasks_collection.find())
    visit_tasks = list(visit_tasks_collection.find())
    
    message = "📊 *Task Analytics*\n\n"
    
    if tasks:
        message += "*📝 Regular Tasks:*\n"
        for task in tasks[:10]:
            message += f"📌 {task['name']}\n"
            message += f"   💰 Reward: {task['amount']} INR\n"
            message += f"   ✅ Completions: {task.get('total_completions', 0)}\n"
            message += f"   💵 Total Spent: {task.get('total_spent', 0)} INR\n\n"
    
    if visit_tasks:
        message += "*🔗 Visit Tasks:*\n"
        for task in visit_tasks[:10]:
            message += f"📌 {task['name']}\n"
            message += f"   💰 Reward: {task['amount']} INR\n"
            message += f"   ⏱️ Time: {task['visit_time']}s\n"
            message += f"   ✅ Completions: {task.get('total_completions', 0)}\n\n"
    
    await update.message.reply_text(message, parse_mode="Markdown")

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
            f"🆔 ID: `{wd['user_id']}`\n"
            f"💰 Amount: {wd['amount']} INR\n"
            f"💳 Method: {wd['method']}\n"
            f"📝 Details: {wd['details']}\n"
            f"🕐 Requested: {wd['requested_at'].strftime('%Y-%m-%d %H:%M')}"
        )
        
        await update.message.reply_text(message, reply_markup=reply_markup, parse_mode="Markdown")

async def handle_withdrawal_decision(update: Update, context: ContextTypes.DEFAULT_TYPE, withdrawal_id, decision):
    query = update.callback_query
    await query.answer()
    
    withdrawal = withdrawals_collection.find_one({"withdrawal_id": withdrawal_id})
    if not withdrawal:
        await query.edit_message_text("❌ Withdrawal not found!")
        return
    
    if decision == "approve":
        withdrawals_collection.update_one(
            {"withdrawal_id": withdrawal_id},
            {
                "$set": {
                    "status": "approved",
                    "processed_at": datetime.now(),
                    "processed_by": query.from_user.id
                }
            }
        )
        
        users_collection.update_one(
            {"user_id": withdrawal['user_id']},
            {"$inc": {"total_withdrawn": withdrawal['amount']}}
        )
        
        await query.edit_message_text(f"✅ Withdrawal approved! Amount: {withdrawal['amount']} INR")
        
        try:
            await context.bot.send_message(
                withdrawal['user_id'],
                f"✅ *Withdrawal Approved!*\n\n"
                f"Amount: {withdrawal['amount']} INR\n"
                f"Method: {withdrawal['method']}\n\n"
                f"Amount will be sent to your provided details shortly.",
                parse_mode="Markdown"
            )
        except:
            pass
            
    else:
        withdrawals_collection.update_one(
            {"withdrawal_id": withdrawal_id},
            {
                "$set": {
                    "status": "rejected",
                    "processed_at": datetime.now(),
                    "processed_by": query.from_user.id
                }
            }
        )
        
        update_user_balance(withdrawal['user_id'], withdrawal['amount'])
        
        await query.edit_message_text("❌ Withdrawal rejected! Amount refunded to user.")
        
        try:
            await context.bot.send_message(
                withdrawal['user_id'],
                f"❌ *Withdrawal Rejected*\n\n"
                f"Amount: {withdrawal['amount']} INR\n"
                f"Reason: Invalid details provided.\n\n"
                f"Amount has been refunded to your balance.",
                parse_mode="Markdown"
            )
        except:
            pass

# Handle Admin Input
async def handle_admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text
    
    if not context.user_data.get('admin_action'):
        return
    
    action = context.user_data['admin_action']
    
    if action == 'add_task':
        step = context.user_data.get('task_step', 1)
        
        if step == 1:
            context.user_data['task_name'] = text
            context.user_data['task_step'] = 2
            await update.message.reply_text("📝 Send task description:")
            
        elif step == 2:
            context.user_data['task_description'] = text
            context.user_data['task_step'] = 3
            await update.message.reply_text("💰 Send task reward amount (in INR):")
            
        elif step == 3:
            try:
                amount = float(text)
                context.user_data['task_amount'] = amount
                context.user_data['task_step'] = 4
                await update.message.reply_text("🔗 Send task link:")
            except:
                await update.message.reply_text("❌ Please send a valid number!")
                
        elif step == 4:
            context.user_data['task_link'] = text
            context.user_data['task_step'] = 5
            await update.message.reply_text("📸 Send task image (optional) - Send 'skip' to skip:")
            
        elif step == 5:
            image_id = None
            if text.lower() != 'skip' and update.message.photo:
                image_id = update.message.photo[-1].file_id
            elif text.lower() != 'skip':
                await update.message.reply_text("Please send a photo or type 'skip'")
                return
            
            task_id = f"task_{datetime.now().timestamp()}"
            task = {
                "task_id": task_id,
                "name": context.user_data['task_name'],
                "description": context.user_data['task_description'],
                "amount": context.user_data['task_amount'],
                "link": context.user_data['task_link'],
                "image_id": image_id,
                "status": "active",
                "expires_at": datetime.now() + timedelta(days=30),
                "total_completions": 0,
                "total_spent": 0,
                "created_at": datetime.now()
            }
            
            tasks_collection.insert_one(task)
            
            await update.message.reply_text(
                f"✅ *Task Created!*\n\n"
                f"Name: {task['name']}\n"
                f"Reward: {task['amount']} INR",
                parse_mode="Markdown"
            )
            
            context.user_data.pop('admin_action', None)
            context.user_data.pop('task_step', None)
            
    elif action == 'add_visit_task':
        step = context.user_data.get('task_step', 1)
        
        if step == 1:
            context.user_data['task_name'] = text
            context.user_data['task_step'] = 2
            await update.message.reply_text("💰 Send task reward amount (in INR):")
            
        elif step == 2:
            try:
                amount = float(text)
                context.user_data['task_amount'] = amount
                context.user_data['task_step'] = 3
                await update.message.reply_text("⏱️ Send visit time required (in seconds):")
            except:
                await update.message.reply_text("❌ Please send a valid number!")
                
        elif step == 3:
            try:
                visit_time = int(text)
                context.user_data['visit_time'] = visit_time
                context.user_data['task_step'] = 4
                await update.message.reply_text("🔗 Send website link:")
            except:
                await update.message.reply_text("❌ Please send a valid number!")
                
        elif step == 4:
            context.user_data['task_link'] = text
            context.user_data['task_step'] = 5
            await update.message.reply_text("📸 Send task image (optional) - Send 'skip' to skip:")
            
        elif step == 5:
            image_id = None
            if text.lower() != 'skip' and update.message.photo:
                image_id = update.message.photo[-1].file_id
            elif text.lower() != 'skip':
                await update.message.reply_text("Please send a photo or type 'skip'")
                return
            
            task_id = f"visit_{datetime.now().timestamp()}"
            task = {
                "task_id": task_id,
                "name": context.user_data['task_name'],
                "amount": context.user_data['task_amount'],
                "visit_time": context.user_data['visit_time'],
                "link": context.user_data['task_link'],
                "image_id": image_id,
                "status": "active",
                "expires_at": datetime.now() + timedelta(days=30),
                "total_completions": 0,
                "total_spent": 0,
                "created_at": datetime.now()
            }
            
            visit_tasks_collection.insert_one(task)
            
            await update.message.reply_text(
                f"✅ *Visit Task Created!*\n\n"
                f"Name: {task['name']}\n"
                f"Reward: {task['amount']} INR\n"
                f"Time: {task['visit_time']} seconds",
                parse_mode="Markdown"
            )
            
            context.user_data.pop('admin_action', None)
            context.user_data.pop('task_step', None)
            
    elif action == 'broadcast':
        users = users_collection.find()
        sent = 0
        failed = 0
        
        await update.message.reply_text("📢 Broadcasting message...")
        
        for user in users:
            try:
                await context.bot.send_message(user['user_id'], text, parse_mode="Markdown")
                sent += 1
                await asyncio.sleep(0.05)
            except:
                failed += 1
        
        await update.message.reply_text(
            f"✅ *Broadcast Complete!*\n\n"
            f"Sent: {sent} users\n"
            f"Failed: {failed} users",
            parse_mode="Markdown"
        )
        
        context.user_data.pop('admin_action', None)

# --- Callback Query Handler ---
async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    
    if data.startswith("start_task_"):
        task_id = data.replace("start_task_", "")
        await start_task(update, context, task_id)
    elif data.startswith("submit_screenshot_"):
        task_id = data.replace("submit_screenshot_", "")
        await submit_screenshot(update, context, task_id)
    elif data.startswith("start_visit_"):
        task_id = data.replace("start_visit_", "")
        await start_visit_task(update, context, task_id)
    elif data == "complete_visit":
        await complete_visit_task(update, context)
    elif data.startswith("approve_sub_"):
        submission_id = data.replace("approve_sub_", "")
        await handle_submission_decision(update, context, submission_id, "approve")
    elif data.startswith("reject_sub_"):
        submission_id = data.replace("reject_sub_", "")
        await handle_submission_decision(update, context, submission_id, "reject")
    elif data.startswith("approve_wd_"):
        withdrawal_id = data.replace("approve_wd_", "")
        await handle_withdrawal_decision(update, context, withdrawal_id, "approve")
    elif data.startswith("reject_wd_"):
        withdrawal_id = data.replace("reject_wd_", "")
        await handle_withdrawal_decision(update, context, withdrawal_id, "reject")
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
    print("✅ All features active with security enhancements!")
    print("   - Task System with Screenshot Verification")
    print("   - Visit & Earn with Timer Validation")
    print("   - Referral Program (2 INR per referral)")
    print("   - Multiple Withdrawal Methods with Custom Amounts")
    print("   - Admin Panel with Screenshot Display")
    print("   - Task & Withdrawal History")
    print("   - Advanced Fraud Detection")
    print("   - Rate Limiting & Security Features")
    
    app.run_polling()