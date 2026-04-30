# main.py - Premium Earning Bot (Railway Optimized)
import telebot
from telebot import types
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError
from datetime import datetime, timedelta
import time
import random
import string
import os
import sys
import logging
import re
from bson import ObjectId
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import json
from urllib.parse import urlparse

# ==================== LOGGING SETUP ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ==================== CONFIGURATION ====================
API_TOKEN = os.environ.get('API_TOKEN', '8384600981:AAFOkWJEw0zPqouHrwFUYw9LI7m-eLBp1KE')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'Vansh@000')
MONGODB_URI = os.environ.get('MONGODB_URI', 'mongodb+srv://Vansh:Vansh000@cluster0.tqmuzxc.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0')
DB_NAME = os.environ.get('DB_NAME', 'earning_bot')
PORT = int(os.environ.get('PORT', 8080))

# Bot Settings
WITHDRAWAL_MIN = {
    'upi': 50,
    'bank': 100,
    'crypto': 200
}
REFERRAL_BONUS = 3.0
DAILY_BONUS = 2.0
VISIT_COOLDOWN_HOURS = 24

# Global Variables
ADMIN_USER_ID = None
bot = None
db = None
mongo_client = None

# ==================== DATABASE CONNECTION ====================
def connect_mongodb():
    global mongo_client, db
    try:
        logger.info("Connecting to MongoDB...")
        mongo_client = MongoClient(
            MONGODB_URI,
            serverSelectionTimeoutMS=10000,
            connectTimeoutMS=10000,
            retryWrites=True,
            w='majority'
        )
        mongo_client.admin.command('ping')
        db = mongo_client[DB_NAME]
        logger.info("MongoDB connected successfully!")
        return True
    except Exception as e:
        logger.error(f"MongoDB connection failed: {e}")
        return False

# ==================== COLLECTIONS ====================
users = None
tasks = None
visit_tasks = None
withdrawals = None
submissions = None
transactions = None
announcements = None

def init_collections():
    global users, tasks, visit_tasks, withdrawals, submissions, transactions, announcements
    users = db['users']
    tasks = db['tasks']
    visit_tasks = db['visit_tasks']
    withdrawals = db['withdrawals']
    submissions = db['submissions']
    transactions = db['transactions']
    announcements = db['announcements']
    
    # Create indexes
    users.create_index('user_id', unique=True)
    users.create_index('referral_code', unique=True, sparse=True)
    tasks.create_index('active')
    visit_tasks.create_index('active')
    withdrawals.create_index([('user_id', 1), ('status', 1)])
    logger.info("Collections initialized")

# ==================== HEALTH SERVER ====================
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ['/', '/health', '/healthz']:
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            status = {
                "status": "healthy",
                "bot": "running",
                "database": "connected" if mongo_client else "disconnected",
                "timestamp": datetime.now().isoformat()
            }
            self.wfile.write(json.dumps(status).encode())
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        pass

def start_health_server():
    server = HTTPServer(('0.0.0.0', PORT), HealthHandler)
    logger.info(f"Health server running on port {PORT}")
    server.serve_forever()

# ==================== HELPER FUNCTIONS ====================
def generate_referral_code():
    """Generate unique referral code"""
    chars = string.ascii_uppercase + string.digits
    for _ in range(10):
        code = ''.join(random.choices(chars, k=8))
        if not users.find_one({'referral_code': code}):
            return code
    return f"USER{random.randint(10000, 99999)}"

def get_user(user_id):
    """Get user by Telegram ID"""
    try:
        return users.find_one({'user_id': str(user_id)})
    except:
        return None

def update_balance(user_id, amount, operation='add'):
    """Update user balance"""
    try:
        user = get_user(user_id)
        if not user:
            return False
        current = float(user.get('balance', 0))
        new_balance = round(current + amount if operation == 'add' else current - amount, 2)
        if new_balance < 0:
            return False
        users.update_one({'user_id': str(user_id)}, {'$set': {'balance': new_balance}})
        return True
    except:
        return False

def add_transaction(user_id, amount, tx_type, description):
    """Record transaction"""
    try:
        transactions.insert_one({
            'user_id': str(user_id),
            'amount': amount,
            'type': tx_type,
            'description': description,
            'date': datetime.now(),
            'status': 'completed'
        })
        users.update_one({'user_id': str(user_id)}, {'$inc': {'total_earned': amount if amount > 0 else 0}})
    except:
        pass

def format_money(amount):
    """Format currency"""
    try:
        return f"₹{float(amount):.2f}"
    except:
        return "₹0.00"

def is_valid_upi(upi_id):
    """Validate UPI ID"""
    pattern = r'^[a-zA-Z0-9.\-_]{2,256}@[a-zA-Z]{3,}$'
    return re.match(pattern, upi_id) is not None

# ==================== KEYBOARDS ====================
def main_keyboard():
    """Main user keyboard"""
    keyboard = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    buttons = [
        types.KeyboardButton('📝 Tasks'),
        types.KeyboardButton('🔗 Visit & Earn'),
        types.KeyboardButton('💰 My Balance'),
        types.KeyboardButton('💸 Withdraw'),
        types.KeyboardButton('👥 Referral Program'),
        types.KeyboardButton('🎁 Daily Bonus'),
        types.KeyboardButton('📊 My Stats'),
        types.KeyboardButton('📢 Announcements'),
        types.KeyboardButton('❓ Help'),
        types.KeyboardButton('ℹ️ About')
    ]
    keyboard.add(*buttons)
    return keyboard

def admin_keyboard():
    """Admin keyboard"""
    keyboard = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    buttons = [
        types.KeyboardButton('📊 Dashboard'),
        types.KeyboardButton('👥 User Stats'),
        types.KeyboardButton('💰 Financial Stats'),
        types.KeyboardButton('📝 Manage Tasks'),
        types.KeyboardButton('🔗 Manage Visit Tasks'),
        types.KeyboardButton('💸 Withdrawal Requests'),
        types.KeyboardButton('📋 Pending Submissions'),
        types.KeyboardButton('📢 Broadcast'),
        types.KeyboardButton('➕ Add Task'),
        types.KeyboardButton('➕ Add Visit Task'),
        types.KeyboardButton('🔙 Exit Admin')
    ]
    keyboard.add(*buttons)
    return keyboard

def withdrawal_keyboard():
    """Withdrawal method selection"""
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        types.InlineKeyboardButton('💳 UPI (Min ₹50)', callback_data='wd_upi'),
        types.InlineKeyboardButton('🏦 Bank Transfer (Min ₹100)', callback_data='wd_bank'),
        types.InlineKeyboardButton('₿ Crypto (Min ₹200)', callback_data='wd_crypto'),
        types.InlineKeyboardButton('🔙 Back', callback_data='wd_back')
    )
    return keyboard

# ==================== COMMAND HANDLERS ====================
@bot.message_handler(commands=['start'])
def cmd_start(message):
    user_id = str(message.from_user.id)
    username = message.from_user.username or "no_username"
    first_name = message.from_user.first_name or "User"
    
    user = get_user(user_id)
    
    # Parse referral
    ref_code = None
    if len(message.text.split()) > 1:
        ref_code = message.text.split()[1]
    
    if not user:
        # Create new user
        new_code = generate_referral_code()
        
        user_data = {
            'user_id': user_id,
            'username': username,
            'first_name': first_name,
            'balance': 0.0,
            'total_earned': 0.0,
            'total_withdrawn': 0.0,
            'referral_code': new_code,
            'referred_by': None,
            'referral_earnings': 0.0,
            'total_referrals': 0,
            'joined_date': datetime.now(),
            'last_active': datetime.now(),
            'daily_bonus_last': None,
            'completed_tasks': [],
            'completed_visits': [],
            'is_active': True,
            'is_banned': False
        }
        
        # Handle referral
        if ref_code:
            referrer = users.find_one({'referral_code': ref_code})
            if referrer and referrer['user_id'] != user_id:
                user_data['referred_by'] = referrer['user_id']
                # Credit bonus to referrer
                update_balance(referrer['user_id'], REFERRAL_BONUS, 'add')
                add_transaction(referrer['user_id'], REFERRAL_BONUS, 'referral', f'New user: {first_name}')
                users.update_one({'user_id': referrer['user_id']}, {
                    '$inc': {'total_referrals': 1, 'referral_earnings': REFERRAL_BONUS}
                })
                # Notify referrer
                try:
                    bot.send_message(int(referrer['user_id']), 
                        f"🎉 *New Referral!*\n\n{first_name} joined using your link!\n"
                        f"💰 You earned {format_money(REFERRAL_BONUS)}\n"
                        f"📊 Total Referrals: {referrer.get('total_referrals', 0) + 1}",
                        parse_mode='Markdown')
                except:
                    pass
        
        users.insert_one(user_data)
        
        welcome_text = f"""
🎉 *Welcome to Earning Pro, {first_name}!*

💰 *Earn Money Easily:*

✅ *Complete Tasks* - Earn up to ₹100/task
🔗 *Visit Websites* - Earn by visiting links
👥 *Refer Friends* - Earn {format_money(REFERRAL_BONUS)} per referral
🎁 *Daily Bonus* - Claim daily rewards
💸 *Fast Withdrawals* - Get paid instantly

🔑 *Your Referral Code:* `{new_code}`

📤 *Share with friends:* `https://t.me/{bot.get_me().username}?start={new_code}`

⚠️ *Rules:*
• One account per person
• Complete tasks honestly
• No fake submissions = Permanent Ban

🚀 *Start earning now! Click the buttons below*
"""
        bot.send_message(message.chat.id, welcome_text, parse_mode='Markdown', reply_markup=main_keyboard())
    else:
        if user.get('is_banned', False):
            bot.send_message(message.chat.id, "❌ You are banned from this bot!")
            return
        
        users.update_one({'user_id': user_id}, {'$set': {'last_active': datetime.now()}})
        
        welcome_back = f"""
👋 *Welcome Back, {first_name}!*

💰 *Balance:* {format_money(user.get('balance', 0))}
👥 *Referrals:* {user.get('total_referrals', 0)}
📈 *Total Earned:* {format_money(user.get('total_earned', 0))}

Tap a button below to continue earning! 🚀
"""
        bot.send_message(message.chat.id, welcome_back, parse_mode='Markdown', reply_markup=main_keyboard())

# ==================== BALANCE HANDLER ====================
@bot.message_handler(func=lambda m: m.text == '💰 My Balance')
def show_balance(message):
    user = get_user(message.from_user.id)
    if not user:
        bot.send_message(message.chat.id, "❌ Please use /start first!")
        return
    
    if user.get('is_banned', False):
        bot.send_message(message.chat.id, "❌ You are banned!")
        return
    
    bal = user.get('balance', 0)
    earned = user.get('total_earned', 0)
    withdrawn = user.get('total_withdrawn', 0)
    ref_count = user.get('total_referrals', 0)
    ref_earn = user.get('referral_earnings', 0)
    
    text = f"""
💰 *YOUR WALLET*

┌─────────────────┐
│ 💵 Balance: {format_money(bal)}
│ 📈 Total Earned: {format_money(earned)}
│ 💸 Total Withdrawn: {format_money(withdrawn)}
└─────────────────┘

👥 *Referral Stats*
├ 👤 Referrals: {ref_count}
├ 🎁 Referral Earnings: {format_money(ref_earn)}
└ 🔑 Code: `{user.get('referral_code')}`

📊 *Activity*
├ ✅ Tasks Done: {len(user.get('completed_tasks', []))}
└ 🔗 Visits Done: {len(user.get('completed_visits', []))}

💪 *Keep earning to reach withdrawal minimum!*
"""
    bot.send_message(message.chat.id, text, parse_mode='Markdown')

# ==================== STATS HANDLER ====================
@bot.message_handler(func=lambda m: m.text == '📊 My Stats')
def show_stats(message):
    user = get_user(message.from_user.id)
    if not user:
        bot.send_message(message.chat.id, "❌ Please use /start first!")
        return
    
    joined = user.get('joined_date')
    if isinstance(joined, str):
        joined = datetime.fromisoformat(joined)
    
    last_active = user.get('last_active')
    if isinstance(last_active, str):
        last_active = datetime.fromisoformat(last_active)
    
    text = f"""
📊 *YOUR STATISTICS*

👤 *Profile*
├ ID: `{user['user_id']}`
├ Username: @{user.get('username', 'N/A')}
├ Joined: {joined.strftime('%d %b %Y') if joined else 'N/A'}
└ Last Active: {last_active.strftime('%d %b %Y, %H:%M') if last_active else 'N/A'}

💰 *Financial*
├ Balance: {format_money(user.get('balance', 0))}
├ Earned: {format_money(user.get('total_earned', 0))}
└ Withdrawn: {format_money(user.get('total_withdrawn', 0))}

👥 *Referrals*
├ Code: `{user.get('referral_code')}`
├ Total: {user.get('total_referrals', 0)}
├ Earnings: {format_money(user.get('referral_earnings', 0))}
└ Referred By: {user.get('referred_by', 'None')}

📈 *Completed*
├ Tasks: {len(user.get('completed_tasks', []))}
└ Visits: {len(user.get('completed_visits', []))}

🔥 *Keep grinding! Share your referral code*
"""
    bot.send_message(message.chat.id, text, parse_mode='Markdown')

# ==================== REFERRAL HANDLER ====================
@bot.message_handler(func=lambda m: m.text == '👥 Referral Program')
def show_referral(message):
    user = get_user(message.from_user.id)
    if not user:
        bot.send_message(message.chat.id, "❌ Please use /start first!")
        return
    
    bot_info = bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start={user['referral_code']}"
    
    text = f"""
👥 *REFERRAL PROGRAM*

💰 *Earn {format_money(REFERRAL_BONUS)} per referral!*

📌 *How it works:*
1️⃣ Share your unique link
2️⃣ Friend joins using your link
3️⃣ You get {format_money(REFERRAL_BONUS)} instantly!

🔑 *Your Code:* `{user['referral_code']}`

🔗 *Share this link:*
`{ref_link}`

📊 *Your Stats:*
├ ✅ Referrals: {user.get('total_referrals', 0)}
└ 💵 Earned: {format_money(user.get('referral_earnings', 0))}

📢 *Tips to earn more:*
• Share on WhatsApp, Telegram, Instagram
• Post in groups and channels
• Tell friends about earning opportunities

🚀 *Start sharing now!*
"""
    bot.send_message(message.chat.id, text, parse_mode='Markdown', disable_web_page_preview=True)

# ==================== DAILY BONUS ====================
@bot.message_handler(func=lambda m: m.text == '🎁 Daily Bonus')
def daily_bonus(message):
    user = get_user(message.from_user.id)
    if not user:
        bot.send_message(message.chat.id, "❌ Please use /start first!")
        return
    
    last_bonus = user.get('daily_bonus_last')
    if last_bonus:
        if isinstance(last_bonus, str):
            last_bonus = datetime.fromisoformat(last_bonus)
        if datetime.now() - last_bonus < timedelta(hours=24):
            remaining = timedelta(hours=24) - (datetime.now() - last_bonus)
            hours = remaining.seconds // 3600
            minutes = (remaining.seconds % 3600) // 60
            bot.send_message(message.chat.id, 
                f"⏰ *Daily Bonus Already Claimed!*\n\nCome back in {hours}h {minutes}m",
                parse_mode='Markdown')
            return
    
    # Give bonus
    update_balance(message.from_user.id, DAILY_BONUS, 'add')
    add_transaction(message.from_user.id, DAILY_BONUS, 'bonus', 'Daily Bonus')
    users.update_one({'user_id': str(message.from_user.id)}, {'$set': {'daily_bonus_last': datetime.now()}})
    
    text = f"""
🎁 *DAILY BONUS CLAIMED!*

💰 You received: {format_money(DAILY_BONUS)}

📈 *New Balance:* {format_money(user.get('balance', 0) + DAILY_BONUS)}

⏰ Come back tomorrow for more!

💪 *Complete tasks to earn even more!*
"""
    bot.send_message(message.chat.id, text, parse_mode='Markdown')

# ==================== TASKS HANDLER ====================
@bot.message_handler(func=lambda m: m.text == '📝 Tasks')
def show_tasks(message):
    user = get_user(message.from_user.id)
    if not user:
        bot.send_message(message.chat.id, "❌ Please use /start first!")
        return
    
    available_tasks = list(tasks.find({'active': True}))
    
    if not available_tasks:
        bot.send_message(message.chat.id, "📝 No tasks available right now. Check back later!")
        return
    
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    for task in available_tasks:
        task_id = str(task['_id'])
        if task_id not in user.get('completed_tasks', []):
            keyboard.add(types.InlineKeyboardButton(
                f"{task['title']} - {format_money(task['amount'])}",
                callback_data=f"view_task_{task_id}"
            ))
    
    if not keyboard.keyboard:
        bot.send_message(message.chat.id, "✅ You've completed all available tasks! New tasks coming soon.")
        return
    
    bot.send_message(message.chat.id, 
        "📝 *AVAILABLE TASKS*\n\nClick a task below to view details and submit proof:",
        parse_mode='Markdown', reply_markup=keyboard)

@bot.callback_query_handler(func=lambda call: call.data.startswith('view_task_'))
def view_task(call):
    task_id = call.data.replace('view_task_', '')
    task = tasks.find_one({'_id': ObjectId(task_id)})
    
    if not task:
        bot.answer_callback_query(call.id, "Task not found!")
        return
    
    user = get_user(call.from_user.id)
    if task_id in user.get('completed_tasks', []):
        bot.answer_callback_query(call.id, "You've already completed this task!")
        return
    
    text = f"""
📝 *{task['title']}*

💰 *Reward:* {format_money(task['amount'])}
📋 *Description:* {task.get('description', 'Complete the task as instructed')}

🔗 *Link:* {task.get('link', 'No link provided')}

✅ *Instructions:*
1. Click the link above
2. Complete the required action
3. Take a clear screenshot
4. Submit using the button below

⚠️ *Warning:* Fake submissions will result in a permanent ban!
"""
    
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("📸 Submit Proof", callback_data=f"submit_task_{task_id}"))
    keyboard.add(types.InlineKeyboardButton("🔙 Back to Tasks", callback_data="back_to_tasks"))
    
    try:
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                            parse_mode='Markdown', reply_markup=keyboard, disable_web_page_preview=True)
    except:
        bot.send_message(call.message.chat.id, text, parse_mode='Markdown',
                        reply_markup=keyboard, disable_web_page_preview=True)
    
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith('submit_task_'))
def submit_task(call):
    task_id = call.data.replace('submit_task_', '')
    task = tasks.find_one({'_id': ObjectId(task_id)})
    
    if not task:
        bot.answer_callback_query(call.id, "Task not found!")
        return
    
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id,
        f"📸 *Submit Proof for:* {task['title']}\n\nPlease send a screenshot of completed task.\nType 'cancel' to cancel.",
        parse_mode='Markdown')
    
    bot.register_next_step_handler(msg, save_submission, task_id, task)

def save_submission(message, task_id, task):
    if message.text and message.text.lower() == 'cancel':
        bot.send_message(message.chat.id, "❌ Submission cancelled.", reply_markup=main_keyboard())
        return
    
    if not message.photo:
        bot.send_message(message.chat.id, "❌ Please send a photo/screenshot!")
        return
    
    photo_id = message.photo[-1].file_id
    
    submission = {
        'user_id': str(message.from_user.id),
        'username': message.from_user.username or "unknown",
        'first_name': message.from_user.first_name or "User",
        'task_id': task_id,
        'task_title': task['title'],
        'task_amount': task['amount'],
        'screenshot': photo_id,
        'status': 'pending',
        'submitted_at': datetime.now()
    }
    
    submissions.insert_one(submission)
    
    bot.send_message(message.chat.id,
        f"✅ *Submission Received!*\n\nTask: {task['title']}\nReward: {format_money(task['amount'])}\n\n⏳ Waiting for admin approval.\nYou will be notified once approved.",
        parse_mode='Markdown', reply_markup=main_keyboard())
    
    # Notify admin
    if ADMIN_USER_ID:
        try:
            bot.send_message(ADMIN_USER_ID,
                f"📋 *New Task Submission*\n\n👤 @{message.from_user.username}\n📝 {task['title']}\n💰 {format_money(task['amount'])}",
                parse_mode='Markdown')
        except:
            pass

@bot.callback_query_handler(func=lambda call: call.data == 'back_to_tasks')
def back_to_tasks(call):
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except:
        pass
    show_tasks(call.message)

# ==================== VISIT TASKS ====================
@bot.message_handler(func=lambda m: m.text == '🔗 Visit & Earn')
def show_visit_tasks(message):
    user = get_user(message.from_user.id)
    if not user:
        bot.send_message(message.chat.id, "❌ Please use /start first!")
        return
    
    available_tasks = list(visit_tasks.find({'active': True}))
    
    if not available_tasks:
        bot.send_message(message.chat.id, "🔗 No visit tasks available right now. Check back later!")
        return
    
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    for task in available_tasks:
        task_id = str(task['_id'])
        # Check cooldown
        last_completed = None
        for visit in user.get('completed_visits', []):
            if visit.get('task_id') == task_id:
                last_completed = visit.get('completed_at')
                break
        
        if not last_completed or (datetime.now() - last_completed).total_seconds() > VISIT_COOLDOWN_HOURS * 3600:
            keyboard.add(types.InlineKeyboardButton(
                f"{task['title']} - {format_money(task['amount'])} ({task['time_required']}s)",
                callback_data=f"start_visit_{task_id}"
            ))
    
    if not keyboard.keyboard:
        bot.send_message(message.chat.id, f"⏰ All tasks are on cooldown. Come back after {VISIT_COOLDOWN_HOURS} hours!")
        return
    
    bot.send_message(message.chat.id,
        "🔗 *VISIT & EARN*\n\nVisit websites, stay for required time, and earn instantly!",
        parse_mode='Markdown', reply_markup=keyboard)

@bot.callback_query_handler(func=lambda call: call.data.startswith('start_visit_'))
def start_visit(call):
    task_id = call.data.replace('start_visit_', '')
    task = visit_tasks.find_one({'_id': ObjectId(task_id)})
    
    if not task:
        bot.answer_callback_query(call.id, "Task not found!")
        return
    
    # Store start time
    users.update_one({'user_id': str(call.from_user.id)},
        {'$set': {f'visit_start_{task_id}': datetime.now()}})
    
    text = f"""
🔗 *{task['title']}*

💰 *Reward:* {format_money(task['amount'])}
⏱️ *Time Required:* {task['time_required']} seconds
🔗 *Link:* {task['link']}

⚠️ *Instructions:*
1. Click the link above
2. Stay on the page for {task['time_required']} seconds
3. Click "✅ Complete Visit" button below
4. You'll be credited instantly!

💡 *Tip:* Don't close the page too early!
"""
    
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("✅ Complete Visit", callback_data=f"complete_visit_{task_id}"))
    keyboard.add(types.InlineKeyboardButton("🔙 Back to Tasks", callback_data="back_to_visit_tasks"))
    
    try:
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                            parse_mode='Markdown', reply_markup=keyboard, disable_web_page_preview=True)
    except:
        bot.send_message(call.message.chat.id, text, parse_mode='Markdown',
                        reply_markup=keyboard, disable_web_page_preview=True)
    
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith('complete_visit_'))
def complete_visit(call):
    task_id = call.data.replace('complete_visit_', '')
    task = visit_tasks.find_one({'_id': ObjectId(task_id)})
    
    if not task:
        bot.answer_callback_query(call.id, "Task not found!")
        return
    
    user = get_user(call.from_user.id)
    start_time = user.get(f'visit_start_{task_id}')
    
    if not start_time:
        bot.answer_callback_query(call.id, "Please start the task first!")
        return
    
    if isinstance(start_time, str):
        start_time = datetime.fromisoformat(start_time)
    
    elapsed = (datetime.now() - start_time).total_seconds()
    
    if elapsed >= task['time_required']:
        # Credit reward
        amount = task['amount']
        update_balance(call.from_user.id, amount, 'add')
        add_transaction(call.from_user.id, amount, 'visit', f'Visit Task: {task["title"]}')
        
        # Update user record
        users.update_one({'user_id': str(call.from_user.id)}, {
            '$push': {'completed_visits': {
                'task_id': task_id,
                'task_title': task['title'],
                'completed_at': datetime.now()
            }},
            '$unset': {f'visit_start_{task_id}': ''}
        })
        
        bot.answer_callback_query(call.id, f"✅ Earned {format_money(amount)}!")
        
        try:
            bot.edit_message_text(
                f"✅ *VISIT COMPLETED!*\n\n💰 You earned {format_money(amount)}\n\n⏰ Come back after {VISIT_COOLDOWN_HOURS} hours for more!",
                call.message.chat.id, call.message.message_id, parse_mode='Markdown')
        except:
            pass
    else:
        remaining = int(task['time_required'] - elapsed)
        bot.answer_callback_query(call.id, f"⏰ Wait {remaining} more seconds!", show_alert=True)

@bot.callback_query_handler(func=lambda call: call.data == 'back_to_visit_tasks')
def back_to_visit_tasks(call):
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except:
        pass
    show_visit_tasks(call.message)

# ==================== WITHDRAWAL SYSTEM ====================
@bot.message_handler(func=lambda m: m.text == '💸 Withdraw')
def withdraw_menu(message):
    user = get_user(message.from_user.id)
    if not user:
        bot.send_message(message.chat.id, "❌ Please use /start first!")
        return
    
    bal = user.get('balance', 0)
    
    # Check for pending withdrawal
    pending = withdrawals.find_one({'user_id': str(message.from_user.id), 'status': 'pending'})
    if pending:
        bot.send_message(message.chat.id,
            f"⚠️ *Pending Withdrawal Request*\n\nAmount: {format_money(pending['amount'])}\nStatus: Pending Approval\n\nPlease wait for admin to process.",
            parse_mode='Markdown')
        return
    
    if bal < 50:
        bot.send_message(message.chat.id,
            f"❌ *Insufficient Balance*\n\nYour Balance: {format_money(bal)}\nMinimum Withdrawal: ₹50\n\nComplete more tasks to reach the minimum!",
            parse_mode='Markdown')
        return
    
    text = f"""
💸 *WITHDRAWAL*

💰 *Available Balance:* {format_money(bal)}

📋 *Withdrawal Methods:*
• 💳 UPI - Min ₹50
• 🏦 Bank Transfer - Min ₹100  
• ₿ Crypto - Min ₹200

⏱️ *Processing Time:* 24-48 hours

Select your withdrawal method below:
"""
    
    bot.send_message(message.chat.id, text, parse_mode='Markdown', reply_markup=withdrawal_keyboard())

@bot.callback_query_handler(func=lambda call: call.data.startswith('wd_'))
def withdrawal_method(call):
    if call.data == 'wd_back':
        bot.delete_message(call.message.chat.id, call.message.message_id)
        return
    
    method = call.data.replace('wd_', '')
    methods = {'upi': 'UPI', 'bank': 'Bank Transfer', 'crypto': 'Crypto'}
    min_amounts = {'upi': 50, 'bank': 100, 'crypto': 200}
    
    user = get_user(call.from_user.id)
    bal = user.get('balance', 0)
    min_amt = min_amounts.get(method, 50)
    
    if bal < min_amt:
        bot.answer_callback_query(call.id, f"Minimum withdrawal is ₹{min_amt} for {methods.get(method)}!", show_alert=True)
        return
    
    bot.answer_callback_query(call.id)
    
    msg = bot.send_message(call.message.chat.id,
        f"💸 *{methods.get(method)} Withdrawal*\n\n💰 Balance: {format_money(bal)}\n💰 Minimum: ₹{min_amt}\n\nEnter amount to withdraw (or 'cancel'):",
        parse_mode='Markdown')
    
    bot.register_next_step_handler(msg, process_withdrawal_amount, method, min_amt)

def process_withdrawal_amount(message, method, min_amt):
    if message.text and message.text.lower() == 'cancel':
        bot.send_message(message.chat.id, "❌ Withdrawal cancelled.", reply_markup=main_keyboard())
        return
    
    try:
        amount = float(message.text)
    except:
        bot.send_message(message.chat.id, "❌ Please enter a valid number!")
        return
    
    if amount < min_amt:
        bot.send_message(message.chat.id, f"❌ Minimum withdrawal amount is ₹{min_amt}!")
        return
    
    user = get_user(message.from_user.id)
    if amount > user.get('balance', 0):
        bot.send_message(message.chat.id, f"❌ Insufficient balance! Your balance is {format_money(user.get('balance', 0))}")
        return
    
    # Ask for account details
    prompts = {
        'upi': "📱 *Enter your UPI ID:*\n\nExample: name@okhdfcbank\n\nSend 'cancel' to cancel",
        'bank': "🏦 *Enter your bank details:*\n\nSend in this format:\n`Account Holder Name\nAccount Number\nIFSC Code\nBank Name`\n\nSend 'cancel' to cancel",
        'crypto': "₿ *Enter your wallet address:*\n\nSend your BTC/USDT wallet address\nSend 'cancel' to cancel"
    }
    
    msg = bot.send_message(message.chat.id, prompts.get(method), parse_mode='Markdown')
    bot.register_next_step_handler(msg, save_withdrawal_request, method, amount)

def save_withdrawal_request(message, method, amount):
    if message.text and message.text.lower() == 'cancel':
        bot.send_message(message.chat.id, "❌ Withdrawal cancelled.", reply_markup=main_keyboard())
        return
    
    details = message.text
    
    # Deduct balance
    update_balance(message.from_user.id, amount, 'subtract')
    
    # Create withdrawal request
    request = {
        'user_id': str(message.from_user.id),
        'username': message.from_user.username or "unknown",
        'first_name': message.from_user.first_name or "User",
        'amount': amount,
        'method': method,
        'account_details': details,
        'status': 'pending',
        'requested_at': datetime.now()
    }
    
    withdrawals.insert_one(request)
    
    users.update_one({'user_id': str(message.from_user.id)}, {'$inc': {'total_withdrawn': amount}})
    add_transaction(message.from_user.id, -amount, 'withdrawal', f'Withdrawal request via {method.upper()}')
    
    bot.send_message(message.chat.id,
        f"✅ *Withdrawal Request Submitted!*\n\n💰 Amount: {format_money(amount)}\n💳 Method: {method.upper()}\n\n⏱️ Processing Time: 24-48 hours\n\nUse /check_withdrawal to track status",
        parse_mode='Markdown', reply_markup=main_keyboard())
    
    # Notify admin
    if ADMIN_USER_ID:
        try:
            bot.send_message(ADMIN_USER_ID,
                f"💸 *New Withdrawal Request*\n\n👤 @{message.from_user.username}\n💰 {format_money(amount)}\n💳 {method.upper()}\n📝 `{details[:100]}`",
                parse_mode='Markdown')
        except:
            pass

@bot.message_handler(commands=['check_withdrawal'])
def check_withdrawal(message):
    requests = list(withdrawals.find({'user_id': str(message.from_user.id)}).sort('requested_at', -1).limit(5))
    
    if not requests:
        bot.send_message(message.chat.id, "📭 No withdrawal requests found.")
        return
    
    text = "💸 *YOUR WITHDRAWALS*\n\n"
    for req in requests:
        status_emoji = {'pending': '⏳', 'approved': '✅', 'completed': '✅', 'rejected': '❌'}.get(req['status'], '❓')
        req_date = req['requested_at']
        if isinstance(req_date, str):
            req_date = datetime.fromisoformat(req_date)
        
        text += f"{status_emoji} *{req['method'].upper()}* - {format_money(req['amount'])}\n"
        text += f"   Status: {req['status'].upper()}\n"
        text += f"   Date: {req_date.strftime('%d/%m/%Y')}\n\n"
    
    bot.send_message(message.chat.id, text, parse_mode='Markdown')

# ==================== ANNOUNCEMENTS ====================
@bot.message_handler(func=lambda m: m.text == '📢 Announcements')
def show_announcements(message):
    announcement_list = list(announcements.find().sort('created_at', -1).limit(5))
    
    if not announcement_list:
        bot.send_message(message.chat.id, "📢 No announcements yet. Check back later!")
        return
    
    text = "📢 *LATEST ANNOUNCEMENTS*\n\n"
    for ann in announcement_list:
        created = ann['created_at']
        if isinstance(created, str):
            created = datetime.fromisoformat(created)
        text += f"📌 *{ann['title']}*\n{ann['content']}\n📅 {created.strftime('%d %b %Y')}\n\n---\n\n"
    
    bot.send_message(message.chat.id, text, parse_mode='Markdown')

# ==================== HELP & ABOUT ====================
@bot.message_handler(func=lambda m: m.text == '❓ Help')
def show_help(message):
    text = """
❓ *HELP & SUPPORT*

📝 *How to Earn:*
1. Click "📝 Tasks" - Complete tasks and submit proof
2. Click "🔗 Visit & Earn" - Visit websites, stay for required time
3. Click "👥 Referral Program" - Share your link and earn per referral
4. Click "🎁 Daily Bonus" - Claim free bonus every 24 hours

💸 *How to Withdraw:*
1. Earn minimum ₹50
2. Click "💸 Withdraw"
3. Select payment method
4. Enter details and amount
5. Wait 24-48 hours for processing

⚠️ *Rules:*
• One account per person
• Fake submissions = Permanent Ban
• No cheating or automation
• Respect other users

📞 *Support Contact:* @Admin

🔗 *Share Bot:* `https://t.me/{}?start=ref`
"""
    bot.send_message(message.chat.id, text.format(bot.get_me().username), parse_mode='Markdown', disable_web_page_preview=True)

@bot.message_handler(func=lambda m: m.text == 'ℹ️ About')
def show_about(message):
    total_users = users.count_documents({})
    
    text = f"""
ℹ️ *ABOUT EARNING PRO*

🤖 *Version:* 3.0
📅 *Launched:* 2024

✨ *Features:*
• ✅ Task Completion
• 🔗 Website Visits
• 👥 Referral Program (₹{REFERRAL_BONUS}/referral)
• 🎁 Daily Bonus (₹{DAILY_BONUS}/day)
• 💸 Multiple Withdrawal Methods
• 📢 Announcements

📊 *Statistics:*
👥 Total Users: {total_users}+

💪 *Our Mission:*
Provide easy earning opportunities for everyone!

📞 *Support:* @Admin
🔗 *Bot Link:* `https://t.me/{bot.get_me().username}`

⚠️ *Terms apply. No fake submissions.*
"""
    bot.send_message(message.chat.id, text, parse_mode='Markdown', disable_web_page_preview=True)

# ==================== ADMIN PANEL ====================
@bot.message_handler(func=lambda m: m.text == ADMIN_PASSWORD)
def admin_login(message):
    global ADMIN_USER_ID
    ADMIN_USER_ID = message.chat.id
    bot.send_message(message.chat.id, "✅ *Admin Panel Activated!*", parse_mode='Markdown', reply_markup=admin_keyboard())

@bot.message_handler(func=lambda m: m.text == '🔙 Exit Admin')
def admin_exit(message):
    global ADMIN_USER_ID
    if message.chat.id == ADMIN_USER_ID:
        ADMIN_USER_ID = None
        bot.send_message(message.chat.id, "👋 Exited admin panel.", reply_markup=main_keyboard())
    else:
        bot.send_message(message.chat.id, "Use /start for main menu.", reply_markup=main_keyboard())

@bot.message_handler(func=lambda m: m.text == '📊 Dashboard')
def admin_dashboard(message):
    if message.chat.id != ADMIN_USER_ID:
        return
    
    total_users = users.count_documents({})
    active_24h = users.count_documents({'last_active': {'$gte': datetime.now() - timedelta(hours=24)}})
    banned = users.count_documents({'is_banned': True})
    pending_withdrawals = withdrawals.count_documents({'status': 'pending'})
    pending_submissions = submissions.count_documents({'status': 'pending'})
    
    text = f"""
📊 *ADMIN DASHBOARD*

👥 *Users:*
├ Total: {total_users}
├ Active (24h): {active_24h}
└ Banned: {banned}

💰 *Financial:*
├ Pending Withdrawals: {pending_withdrawals}
└ Pending Submissions: {pending_submissions}

📝 *Tasks:*
├ Active Tasks: {tasks.count_documents({'active': True})}
└ Active Visit Tasks: {visit_tasks.count_documents({'active': True})}

📌 Use the buttons below to manage the bot.
"""
    bot.send_message(message.chat.id, text, parse_mode='Markdown')

@bot.message_handler(func=lambda m: m.text == '👥 User Stats')
def admin_user_stats(message):
    if message.chat.id != ADMIN_USER_ID:
        return
    
    # Get top referrers
    top_referrers = list(users.find().sort('total_referrals', -1).limit(5))
    top_earners = list(users.find().sort('total_earned', -1).limit(5))
    
    text = "👥 *USER STATISTICS*\n\n"
    text += "🏆 *Top Referrers:*\n"
    for i, user in enumerate(top_referrers, 1):
        text += f"{i}. @{user.get('username', 'N/A')} - {user.get('total_referrals', 0)} refs\n"
    
    text += "\n💰 *Top Earners:*\n"
    for i, user in enumerate(top_earners, 1):
        text += f"{i}. @{user.get('username', 'N/A')} - {format_money(user.get('total_earned', 0))}\n"
    
    bot.send_message(message.chat.id, text, parse_mode='Markdown')

@bot.message_handler(func=lambda m: m.text == '💰 Financial Stats')
def admin_financial_stats(message):
    if message.chat.id != ADMIN_USER_ID:
        return
    
    pipeline = [{'$group': {
        '_id': None,
        'total_balance': {'$sum': '$balance'},
        'total_earned': {'$sum': '$total_earned'},
        'total_withdrawn': {'$sum': '$total_withdrawn'},
        'total_referral_earnings': {'$sum': '$referral_earnings'}
    }}]
    
    result = list(users.aggregate(pipeline))
    stats = result[0] if result else {}
    
    pending_withdrawals = withdrawals.count_documents({'status': 'pending'})
    pending_amount = sum([w['amount'] for w in withdrawals.find({'status': 'pending'})]) if pending_withdrawals > 0 else 0
    
    text = f"""
💰 *FINANCIAL STATISTICS*

💵 *User Balances:* {format_money(stats.get('total_balance', 0))}
📈 *Total Earned:* {format_money(stats.get('total_earned', 0))}
💸 *Total Withdrawn:* {format_money(stats.get('total_withdrawn', 0))}
🎁 *Referral Payouts:* {format_money(stats.get('total_referral_earnings', 0))}

⏳ *Pending Withdrawals:* {format_money(pending_amount)} ({pending_withdrawals} requests)

📊 *System Health:*
├ Profit Margin: {format_money(stats.get('total_earned', 0) - stats.get('total_withdrawn', 0))}
└ Reserved Balance: {format_money(stats.get('total_balance', 0) - pending_amount)}
"""
    bot.send_message(message.chat.id, text, parse_mode='Markdown')

@bot.message_handler(func=lambda m: m.text == '💸 Withdrawal Requests')
def admin_withdrawals(message):
    if message.chat.id != ADMIN_USER_ID:
        return
    
    pending = list(withdrawals.find({'status': 'pending'}).limit(20))
    
    if not pending:
        bot.send_message(message.chat.id, "✅ No pending withdrawal requests!")
        return
    
    for req in pending:
        keyboard = types.InlineKeyboardMarkup(row_width=2)
        keyboard.add(
            types.InlineKeyboardButton("✅ Approve", callback_data=f"approve_wd_{req['_id']}"),
            types.InlineKeyboardButton("❌ Reject", callback_data=f"reject_wd_{req['_id']}")
        )
        
        req_date = req['requested_at']
        if isinstance(req_date, str):
            req_date = datetime.fromisoformat(req_date)
        
        text = f"""
💸 *WITHDRAWAL REQUEST*

👤 *User:* @{req.get('username', 'Unknown')}
🆔 *ID:* `{req['user_id']}`
💰 *Amount:* {format_money(req['amount'])}
💳 *Method:* {req['method'].upper()}
📅 *Requested:* {req_date.strftime('%d/%m/%Y %H:%M')}

📝 *Account Details:*
`{req['account_details'][:200]}`
"""
        bot.send_message(message.chat.id, text, parse_mode='Markdown', reply_markup=keyboard)

@bot.message_handler(func=lambda m: m.text == '📋 Pending Submissions')
def admin_submissions(message):
    if message.chat.id != ADMIN_USER_ID:
        return
    
    pending = list(submissions.find({'status': 'pending'}).limit(20))
    
    if not pending:
        bot.send_message(message.chat.id, "✅ No pending task submissions!")
        return
    
    for sub in pending:
        keyboard = types.InlineKeyboardMarkup(row_width=2)
        keyboard.add(
            types.InlineKeyboardButton("✅ Approve", callback_data=f"approve_sub_{sub['_id']}"),
            types.InlineKeyboardButton("❌ Reject", callback_data=f"reject_sub_{sub['_id']}")
        )
        
        sub_date = sub['submitted_at']
        if isinstance(sub_date, str):
            sub_date = datetime.fromisoformat(sub_date)
        
        caption = f"""
📋 *TASK SUBMISSION*

👤 *User:* @{sub.get('username', 'Unknown')}
📝 *Task:* {sub['task_title']}
💰 *Reward:* {format_money(sub['task_amount'])}
📅 *Submitted:* {sub_date.strftime('%d/%m/%Y %H:%M')}
"""
        
        try:
            bot.send_photo(message.chat.id, sub['screenshot'], caption=caption,
                          parse_mode='Markdown', reply_markup=keyboard)
        except:
            bot.send_message(message.chat.id, caption + "\n⚠️ Screenshot unavailable",
                           parse_mode='Markdown', reply_markup=keyboard)

@bot.callback_query_handler(func=lambda call: call.data.startswith('approve_wd_'))
def approve_withdrawal(call):
    if call.message.chat.id != ADMIN_USER_ID:
        bot.answer_callback_query(call.id, "Unauthorized!")
        return
    
    wd_id = call.data.replace('approve_wd_', '')
    wd = withdrawals.find_one({'_id': ObjectId(wd_id)})
    
    if wd:
        withdrawals.update_one({'_id': ObjectId(wd_id)}, {'$set': {'status': 'approved', 'processed_at': datetime.now()}})
        
        try:
            bot.send_message(int(wd['user_id']),
                f"✅ *Withdrawal Approved!*\n\n💰 Amount: {format_money(wd['amount'])}\n💳 Method: {wd['method'].upper()}\n\nAmount will be sent to your provided details within 24 hours.",
                parse_mode='Markdown')
        except:
            pass
    
    bot.answer_callback_query(call.id, "Withdrawal Approved!")
    bot.edit_message_text("✅ *APPROVED*", call.message.chat.id, call.message.message_id, parse_mode='Markdown')

@bot.callback_query_handler(func=lambda call: call.data.startswith('reject_wd_'))
def reject_withdrawal(call):
    if call.message.chat.id != ADMIN_USER_ID:
        bot.answer_callback_query(call.id, "Unauthorized!")
        return
    
    wd_id = call.data.replace('reject_wd_', '')
    wd = withdrawals.find_one({'_id': ObjectId(wd_id)})
    
    if wd:
        # Refund balance
        update_balance(wd['user_id'], wd['amount'], 'add')
        withdrawals.update_one({'_id': ObjectId(wd_id)}, {'$set': {'status': 'rejected', 'processed_at': datetime.now()}})
        
        try:
            bot.send_message(int(wd['user_id']),
                f"❌ *Withdrawal Rejected*\n\n💰 Amount: {format_money(wd['amount'])}\n\nReason: Invalid details or verification failed.\nAmount has been refunded to your balance.\nPlease submit a new request with correct details.",
                parse_mode='Markdown')
        except:
            pass
    
    bot.answer_callback_query(call.id, "Withdrawal Rejected!")
    bot.edit_message_text("❌ *REJECTED*", call.message.chat.id, call.message.message_id, parse_mode='Markdown')

@bot.callback_query_handler(func=lambda call: call.data.startswith('approve_sub_'))
def approve_submission(call):
    if call.message.chat.id != ADMIN_USER_ID:
        bot.answer_callback_query(call.id, "Unauthorized!")
        return
    
    sub_id = call.data.replace('approve_sub_', '')
    sub = submissions.find_one({'_id': ObjectId(sub_id)})
    
    if sub:
        # Credit reward
        update_balance(sub['user_id'], sub['task_amount'], 'add')
        add_transaction(sub['user_id'], sub['task_amount'], 'task', f'Task Completed: {sub["task_title"]}')
        
        # Mark task as completed
        users.update_one({'user_id': sub['user_id']}, {
            '$push': {'completed_tasks': sub['task_id']}
        })
        
        submissions.update_one({'_id': ObjectId(sub_id)}, {'$set': {'status': 'approved'}})
        
        try:
            bot.send_message(int(sub['user_id']),
                f"✅ *Task Approved!*\n\n📝 Task: {sub['task_title']}\n💰 Reward: {format_money(sub['task_amount'])}\n\nKeep earning! 🚀",
                parse_mode='Markdown')
        except:
            pass
    
    bot.answer_callback_query(call.id, "Submission Approved!")
    try:
        bot.edit_message_caption("✅ *APPROVED*", call.message.chat.id, call.message.message_id, parse_mode='Markdown')
    except:
        pass

@bot.callback_query_handler(func=lambda call: call.data.startswith('reject_sub_'))
def reject_submission(call):
    if call.message.chat.id != ADMIN_USER_ID:
        bot.answer_callback_query(call.id, "Unauthorized!")
        return
    
    sub_id = call.data.replace('reject_sub_', '')
    sub = submissions.find_one({'_id': ObjectId(sub_id)})
    
    if sub:
        submissions.update_one({'_id': ObjectId(sub_id)}, {'$set': {'status': 'rejected'}})
        
        try:
            bot.send_message(int(sub['user_id']),
                f"❌ *Task Rejected*\n\n📝 Task: {sub['task_title']}\n\nReason: Invalid or fake submission.\nPlease complete the task correctly and resubmit.",
                parse_mode='Markdown')
        except:
            pass
    
    bot.answer_callback_query(call.id, "Submission Rejected!")
    try:
        bot.edit_message_caption("❌ *REJECTED*", call.message.chat.id, call.message.message_id, parse_mode='Markdown')
    except:
        pass

@bot.message_handler(func=lambda m: m.text == '📝 Manage Tasks')
def manage_tasks(message):
    if message.chat.id != ADMIN_USER_ID:
        return
    
    tasks_list = list(tasks.find())
    
    if not tasks_list:
        bot.send_message(message.chat.id, "No tasks found. Use '➕ Add Task' to create one.")
        return
    
    for task in tasks_list:
        status = "✅ Active" if task.get('active', False) else "❌ Inactive"
        keyboard = types.InlineKeyboardMarkup(row_width=2)
        keyboard.add(
            types.InlineKeyboardButton("🔄 Toggle Status", callback_data=f"toggle_task_{task['_id']}"),
            types.InlineKeyboardButton("🗑 Delete", callback_data=f"delete_task_{task['_id']}")
        )
        
        text = f"""
📝 *Task: {task['title']}*
💰 Reward: {format_money(task['amount'])}
📋 Status: {status}
🔗 Link: {task.get('link', 'No link')}
📝 Desc: {task.get('description', 'No description')[:100]}
"""
        bot.send_message(message.chat.id, text, parse_mode='Markdown', reply_markup=keyboard)

@bot.message_handler(func=lambda m: m.text == '🔗 Manage Visit Tasks')
def manage_visit_tasks(message):
    if message.chat.id != ADMIN_USER_ID:
        return
    
    v_tasks = list(visit_tasks.find())
    
    if not v_tasks:
        bot.send_message(message.chat.id, "No visit tasks found. Use '➕ Add Visit Task' to create one.")
        return
    
    for task in v_tasks:
        status = "✅ Active" if task.get('active', False) else "❌ Inactive"
        keyboard = types.InlineKeyboardMarkup(row_width=2)
        keyboard.add(
            types.InlineKeyboardButton("🔄 Toggle Status", callback_data=f"toggle_vtask_{task['_id']}"),
            types.InlineKeyboardButton("🗑 Delete", callback_data=f"delete_vtask_{task['_id']}")
        )
        
        text = f"""
🔗 *Visit Task: {task['title']}*
💰 Reward: {format_money(task['amount'])}
⏱️ Time: {task['time_required']}s
📋 Status: {status}
🔗 Link: {task.get('link', 'No link')}
"""
        bot.send_message(message.chat.id, text, parse_mode='Markdown', reply_markup=keyboard)

@bot.callback_query_handler(func=lambda call: call.data.startswith('toggle_task_'))
def toggle_task(call):
    if call.message.chat.id != ADMIN_USER_ID:
        bot.answer_callback_query(call.id, "Unauthorized!")
        return
    
    task_id = call.data.replace('toggle_task_', '')
    task = tasks.find_one({'_id': ObjectId(task_id)})
    
    if task:
        new_status = not task.get('active', False)
        tasks.update_one({'_id': ObjectId(task_id)}, {'$set': {'active': new_status}})
        bot.answer_callback_query(call.id, f"Task {'activated' if new_status else 'deactivated'}!")
        bot.edit_message_text(f"✅ Task {'Activated' if new_status else 'Deactivated'}", 
                            call.message.chat.id, call.message.message_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith('delete_task_'))
def delete_task(call):
    if call.message.chat.id != ADMIN_USER_ID:
        bot.answer_callback_query(call.id, "Unauthorized!")
        return
    
    task_id = call.data.replace('delete_task_', '')
    tasks.delete_one({'_id': ObjectId(task_id)})
    bot.answer_callback_query(call.id, "Task deleted!")
    bot.delete_message(call.message.chat.id, call.message.message_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith('toggle_vtask_'))
def toggle_vtask(call):
    if call.message.chat.id != ADMIN_USER_ID:
        bot.answer_callback_query(call.id, "Unauthorized!")
        return
    
    task_id = call.data.replace('toggle_vtask_', '')
    task = visit_tasks.find_one({'_id': ObjectId(task_id)})
    
    if task:
        new_status = not task.get('active', False)
        visit_tasks.update_one({'_id': ObjectId(task_id)}, {'$set': {'active': new_status}})
        bot.answer_callback_query(call.id, f"Visit task {'activated' if new_status else 'deactivated'}!")
        bot.edit_message_text(f"✅ Visit Task {'Activated' if new_status else 'Deactivated'}", 
                            call.message.chat.id, call.message.message_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith('delete_vtask_'))
def delete_vtask(call):
    if call.message.chat.id != ADMIN_USER_ID:
        bot.answer_callback_query(call.id, "Unauthorized!")
        return
    
    task_id = call.data.replace('delete_vtask_', '')
    visit_tasks.delete_one({'_id': ObjectId(task_id)})
    bot.answer_callback_query(call.id, "Visit task deleted!")
    bot.delete_message(call.message.chat.id, call.message.message_id)

@bot.message_handler(func=lambda m: m.text == '➕ Add Task')
def add_task_prompt(message):
    if message.chat.id != ADMIN_USER_ID:
        return
    
    msg = bot.send_message(message.chat.id, 
        "📝 *Add New Task*\n\nSend task details in this format:\n\n`Title | Amount | Link | Description`\n\nExample:\n`Subscribe to Channel | 5 | https://t.me/channel | Subscribe and send screenshot`\n\nSend 'cancel' to cancel.",
        parse_mode='Markdown')
    bot.register_next_step_handler(msg, save_new_task)

def save_new_task(message):
    if message.text and message.text.lower() == 'cancel':
        bot.send_message(message.chat.id, "❌ Task creation cancelled.")
        return
    
    parts = message.text.split('|')
    if len(parts) < 3:
        bot.send_message(message.chat.id, "❌ Invalid format! Use: `Title | Amount | Link | Description`", parse_mode='Markdown')
        return
    
    title = parts[0].strip()
    try:
        amount = float(parts[1].strip())
    except:
        bot.send_message(message.chat.id, "❌ Invalid amount! Use a number.")
        return
    link = parts[2].strip() if len(parts) > 2 else ""
    description = parts[3].strip() if len(parts) > 3 else "Complete the task"
    
    task = {
        'title': title,
        'amount': amount,
        'link': link,
        'description': description,
        'active': True,
        'created_at': datetime.now()
    }
    
    tasks.insert_one(task)
    bot.send_message(message.chat.id, f"✅ Task '{title}' added successfully!", reply_markup=admin_keyboard())

@bot.message_handler(func=lambda m: m.text == '➕ Add Visit Task')
def add_visit_task_prompt(message):
    if message.chat.id != ADMIN_USER_ID:
        return
    
    msg = bot.send_message(message.chat.id,
        "🔗 *Add New Visit Task*\n\nSend task details in this format:\n\n`Title | Amount | Time(seconds) | Link`\n\nExample:\n`Visit Website | 2 | 10 | https://example.com`\n\nSend 'cancel' to cancel.",
        parse_mode='Markdown')
    bot.register_next_step_handler(msg, save_new_visit_task)

def save_new_visit_task(message):
    if message.text and message.text.lower() == 'cancel':
        bot.send_message(message.chat.id, "❌ Visit task creation cancelled.")
        return
    
    parts = message.text.split('|')
    if len(parts) < 4:
        bot.send_message(message.chat.id, "❌ Invalid format! Use: `Title | Amount | Time(seconds) | Link`", parse_mode='Markdown')
        return
    
    title = parts[0].strip()
    try:
        amount = float(parts[1].strip())
    except:
        bot.send_message(message.chat.id, "❌ Invalid amount!")
        return
    try:
        time_req = int(parts[2].strip())
    except:
        bot.send_message(message.chat.id, "❌ Invalid time! Use seconds.")
        return
    link = parts[3].strip()
    
    task = {
        'title': title,
        'amount': amount,
        'time_required': time_req,
        'link': link,
        'active': True,
        'created_at': datetime.now()
    }
    
    visit_tasks.insert_one(task)
    bot.send_message(message.chat.id, f"✅ Visit task '{title}' added successfully!", reply_markup=admin_keyboard())

@bot.message_handler(func=lambda m: m.text == '📢 Broadcast')
def broadcast_prompt(message):
    if message.chat.id != ADMIN_USER_ID:
        return
    
    msg = bot.send_message(message.chat.id,
        "📢 *Broadcast Message*\n\nSend the message you want to broadcast to all users.\n\nSend 'cancel' to cancel.",
        parse_mode='Markdown')
    bot.register_next_step_handler(msg, send_broadcast)

def send_broadcast(message):
    if message.text and message.text.lower() == 'cancel':
        bot.send_message(message.chat.id, "❌ Broadcast cancelled.")
        return
    
    broadcast_text = message.text
    
    # Get all users
    all_users = list(users.find({}, {'user_id': 1}))
    total = len(all_users)
    success = 0
    
    status_msg = bot.send_message(message.chat.id, f"📤 Broadcasting to {total} users...")
    
    for user in all_users:
        try:
            bot.send_message(int(user['user_id']), 
                f"📢 *ANNOUNCEMENT*\n\n{broadcast_text}\n\n— Earning Pro Team",
                parse_mode='Markdown')
            success += 1
            time.sleep(0.05)  # Rate limit protection
        except:
            pass
    
    bot.edit_message_text(f"✅ Broadcast Complete!\n\nSent to: {success}/{total} users", 
                         message.chat.id, status_msg.message_id)

# ==================== CATCH ALL ====================
@bot.message_handler(func=lambda m: True)
def catch_all(message):
    if not message.text.startswith('/') and message.text not in ['📝 Tasks', '🔗 Visit & Earn', '💰 My Balance', '💸 Withdraw', '👥 Referral Program', '🎁 Daily Bonus', '📊 My Stats', '📢 Announcements', '❓ Help', 'ℹ️ About']:
        bot.send_message(message.chat.id, 
            "❓ Please use the buttons below to navigate.\n\nType /start to see all options.",
            reply_markup=main_keyboard())

# ==================== MAIN ====================
if __name__ == '__main__':
    print("=" * 60)
    print("🤖 PREMIUM EARNING BOT v3.0")
    print("📡 Deploying on Railway...")
    print("=" * 60)
    
    # Connect to MongoDB
    if not connect_mongodb():
        print("❌ MongoDB connection failed!")
        sys.exit(1)
    
    # Initialize collections
    init_collections()
    
    # Initialize bot
    bot = telebot.TeleBot(API_TOKEN, threaded=False)
    print("✅ Bot initialized")
    
    # Start health server
    health_thread = threading.Thread(target=start_health_server, daemon=True)
    health_thread.start()
    print(f"🏥 Health server: http://0.0.0.0:{PORT}")
    
    # Remove webhook
    try:
        bot.remove_webhook()
        print("✅ Webhook removed")
    except:
        pass
    
    print("=" * 60)
    print("🚀 BOT IS RUNNING!")
    print("=" * 60)
    
    # Start polling with retry
    retry_count = 0
    while retry_count < 10:
        try:
            bot.infinity_polling(timeout=30, long_polling_timeout=15)
        except Exception as e:
            retry_count += 1
            print(f"⚠️ Polling error (attempt {retry_count}): {e}")
            time.sleep(min(retry_count * 5, 30))