# main.py - Complete Earning Bot for Railway
import telebot
from telebot import types
from pymongo import MongoClient
from pongo.errors import ConnectionFailure
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

# ==================== LOGGING SETUP ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ==================== CONFIGURATION ====================
API_TOKEN = os.environ.get('API_TOKEN', '')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'admin123')
MONGODB_URI = os.environ.get('MONGODB_URI', '')
DB_NAME = os.environ.get('DB_NAME', 'earning_bot')
PORT = int(os.environ.get('PORT', 8080))

# Check required environment variables
if not API_TOKEN:
    logger.error("❌ API_TOKEN environment variable is required!")
    logger.error("Please add it in Railway: Variables -> Add Variable -> API_TOKEN")
    sys.exit(1)

if not MONGODB_URI:
    logger.error("❌ MONGODB_URI environment variable is required!")
    logger.error("Please add it in Railway: Variables -> Add Variable -> MONGODB_URI")
    sys.exit(1)

# Bot Settings
REFERRAL_BONUS = 3.0
DAILY_BONUS = 2.0
VISIT_COOLDOWN_HOURS = 24

# Global Variables
ADMIN_USER_ID = None
mongo_client = None
db = None

# ==================== INITIALIZE BOT FIRST ====================
logger.info("Initializing Telegram Bot...")
bot = telebot.TeleBot(API_TOKEN, threaded=False)
logger.info("✅ Bot initialized successfully!")

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
        logger.info("✅ MongoDB connected successfully!")
        return True
    except Exception as e:
        logger.error(f"❌ MongoDB connection failed: {e}")
        return False

# ==================== COLLECTIONS ====================
users = None
tasks = None
visit_tasks = None
withdrawals = None
submissions = None
transactions = None

def init_collections():
    global users, tasks, visit_tasks, withdrawals, submissions, transactions
    users = db['users']
    tasks = db['tasks']
    visit_tasks = db['visit_tasks']
    withdrawals = db['withdrawals']
    submissions = db['submissions']
    transactions = db['transactions']
    
    # Create indexes
    try:
        users.create_index('user_id', unique=True)
        users.create_index('referral_code', unique=True, sparse=True)
        tasks.create_index('active')
        visit_tasks.create_index('active')
        logger.info("✅ Collections initialized")
    except Exception as e:
        logger.warning(f"Index creation warning: {e}")

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
    try:
        server = HTTPServer(('0.0.0.0', PORT), HealthHandler)
        logger.info(f"🏥 Health server running on port {PORT}")
        server.serve_forever()
    except Exception as e:
        logger.error(f"Health server error: {e}")

# ==================== HELPER FUNCTIONS ====================
def generate_referral_code():
    chars = string.ascii_uppercase + string.digits
    for _ in range(10):
        code = ''.join(random.choices(chars, k=8))
        if not users.find_one({'referral_code': code}):
            return code
    return f"USER{random.randint(10000, 99999)}"

def get_user(user_id):
    try:
        return users.find_one({'user_id': str(user_id)})
    except Exception as e:
        logger.error(f"Get user error: {e}")
        return None

def update_balance(user_id, amount, operation='add'):
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
    except Exception as e:
        logger.error(f"Update balance error: {e}")
        return False

def add_transaction(user_id, amount, tx_type, description):
    try:
        transactions.insert_one({
            'user_id': str(user_id),
            'amount': amount,
            'type': tx_type,
            'description': description,
            'date': datetime.now(),
            'status': 'completed'
        })
        if amount > 0:
            users.update_one({'user_id': str(user_id)}, {'$inc': {'total_earned': amount}})
    except Exception as e:
        logger.error(f"Add transaction error: {e}")

def format_money(amount):
    try:
        return f"₹{float(amount):.2f}"
    except:
        return "₹0.00"

# ==================== KEYBOARDS ====================
def main_keyboard():
    keyboard = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    buttons = [
        types.KeyboardButton('📝 Tasks'),
        types.KeyboardButton('🔗 Visit & Earn'),
        types.KeyboardButton('💰 My Balance'),
        types.KeyboardButton('💸 Withdraw'),
        types.KeyboardButton('👥 Referral Program'),
        types.KeyboardButton('🎁 Daily Bonus'),
        types.KeyboardButton('📊 My Stats'),
        types.KeyboardButton('❓ Help'),
        types.KeyboardButton('ℹ️ About')
    ]
    keyboard.add(*buttons)
    return keyboard

def admin_keyboard():
    keyboard = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    buttons = [
        types.KeyboardButton('📊 Dashboard'),
        types.KeyboardButton('👥 User Stats'),
        types.KeyboardButton('💰 Financial Stats'),
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
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        types.InlineKeyboardButton('💳 UPI (Min ₹50)', callback_data='wd_upi'),
        types.InlineKeyboardButton('🏦 Bank (Min ₹100)', callback_data='wd_bank'),
        types.InlineKeyboardButton('₿ Crypto (Min ₹200)', callback_data='wd_crypto')
    )
    return keyboard

# ==================== COMMAND HANDLERS ====================
@bot.message_handler(commands=['start'])
def cmd_start(message):
    user_id = str(message.from_user.id)
    username = message.from_user.username or "no_username"
    first_name = message.from_user.first_name or "User"
    
    user = get_user(user_id)
    
    ref_code = None
    if len(message.text.split()) > 1:
        ref_code = message.text.split()[1]
    
    if not user:
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
        
        if ref_code:
            referrer = users.find_one({'referral_code': ref_code})
            if referrer and referrer['user_id'] != user_id:
                user_data['referred_by'] = referrer['user_id']
                update_balance(referrer['user_id'], REFERRAL_BONUS, 'add')
                add_transaction(referrer['user_id'], REFERRAL_BONUS, 'referral', f'New user: {first_name}')
                users.update_one({'user_id': referrer['user_id']}, {
                    '$inc': {'total_referrals': 1, 'referral_earnings': REFERRAL_BONUS}
                })
                try:
                    bot.send_message(int(referrer['user_id']), 
                        f"🎉 *New Referral!*\n\n{first_name} joined!\n💰 You earned {format_money(REFERRAL_BONUS)}",
                        parse_mode='Markdown')
                except:
                    pass
        
        users.insert_one(user_data)
        
        welcome_text = f"""
🎉 *Welcome to Earning Pro, {first_name}!*

💰 *Earn Money Easily:*
✅ Complete Tasks - Earn up to ₹100/task
🔗 Visit Websites - Earn by visiting links
👥 Refer Friends - Earn {format_money(REFERRAL_BONUS)} per referral
🎁 Daily Bonus - Claim daily rewards
💸 Fast Withdrawals - Get paid instantly

🔑 *Your Referral Code:* `{new_code}`

⚠️ *Rules:* One account per person | No fake submissions

🚀 *Start earning now!*
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

@bot.message_handler(func=lambda m: m.text == '💰 My Balance')
def show_balance(message):
    user = get_user(message.from_user.id)
    if not user or user.get('is_banned', False):
        bot.send_message(message.chat.id, "❌ Please use /start first!")
        return
    
    text = f"""
💰 *YOUR WALLET*

💵 Balance: {format_money(user.get('balance', 0))}
📈 Total Earned: {format_money(user.get('total_earned', 0))}
💸 Total Withdrawn: {format_money(user.get('total_withdrawn', 0))}

👥 *Referral Stats*
├ 👤 Referrals: {user.get('total_referrals', 0)}
├ 🎁 Referral Earnings: {format_money(user.get('referral_earnings', 0))}
└ 🔑 Code: `{user.get('referral_code')}`

📊 *Activity*
├ ✅ Tasks Done: {len(user.get('completed_tasks', []))}
└ 🔗 Visits Done: {len(user.get('completed_visits', []))}
"""
    bot.send_message(message.chat.id, text, parse_mode='Markdown')

@bot.message_handler(func=lambda m: m.text == '📊 My Stats')
def show_stats(message):
    user = get_user(message.from_user.id)
    if not user:
        bot.send_message(message.chat.id, "❌ Please use /start first!")
        return
    
    joined = user.get('joined_date')
    if isinstance(joined, str):
        joined = datetime.fromisoformat(joined)
    
    text = f"""
📊 *YOUR STATISTICS*

👤 *Profile*
├ ID: `{user['user_id']}`
├ Username: @{user.get('username', 'N/A')}
└ Joined: {joined.strftime('%d %b %Y') if joined else 'N/A'}

💰 *Financial*
├ Balance: {format_money(user.get('balance', 0))}
├ Earned: {format_money(user.get('total_earned', 0))}
└ Withdrawn: {format_money(user.get('total_withdrawn', 0))}

👥 *Referrals*
├ Code: `{user.get('referral_code')}`
├ Total: {user.get('total_referrals', 0)}
└ Earnings: {format_money(user.get('referral_earnings', 0))}

📈 *Completed*
├ Tasks: {len(user.get('completed_tasks', []))}
└ Visits: {len(user.get('completed_visits', []))}
"""
    bot.send_message(message.chat.id, text, parse_mode='Markdown')

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
"""
    bot.send_message(message.chat.id, text, parse_mode='Markdown', disable_web_page_preview=True)

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
    
    update_balance(message.from_user.id, DAILY_BONUS, 'add')
    add_transaction(message.from_user.id, DAILY_BONUS, 'bonus', 'Daily Bonus')
    users.update_one({'user_id': str(message.from_user.id)}, {'$set': {'daily_bonus_last': datetime.now()}})
    
    text = f"""
🎁 *DAILY BONUS CLAIMED!*

💰 You received: {format_money(DAILY_BONUS)}

📈 *New Balance:* {format_money(user.get('balance', 0) + DAILY_BONUS)}

⏰ Come back tomorrow for more!
"""
    bot.send_message(message.chat.id, text, parse_mode='Markdown')

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
        bot.send_message(message.chat.id, "✅ You've completed all available tasks!")
        return
    
    bot.send_message(message.chat.id, "📝 *AVAILABLE TASKS*\n\nClick a task below:", parse_mode='Markdown', reply_markup=keyboard)

@bot.callback_query_handler(func=lambda call: call.data.startswith('view_task_'))
def view_task(call):
    task_id = call.data.replace('view_task_', '')
    task = tasks.find_one({'_id': ObjectId(task_id)})
    
    if not task:
        bot.answer_callback_query(call.id, "Task not found!")
        return
    
    text = f"""
📝 *{task['title']}*

💰 *Reward:* {format_money(task['amount'])}
📋 *Description:* {task.get('description', 'Complete the task')}

🔗 *Link:* {task.get('link', 'No link')}

✅ *Instructions:*
1. Click the link above
2. Complete the required action
3. Take a clear screenshot
4. Submit using the button below

⚠️ *Fake submissions = Permanent Ban!*
"""
    
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("📸 Submit Proof", callback_data=f"submit_task_{task_id}"))
    keyboard.add(types.InlineKeyboardButton("🔙 Back", callback_data="back_to_tasks"))
    
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
        f"📸 *Submit Proof for:* {task['title']}\n\nPlease send a screenshot.\nType 'cancel' to cancel.",
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
        f"✅ *Submission Received!*\n\nTask: {task['title']}\nReward: {format_money(task['amount'])}\n\n⏳ Waiting for admin approval.",
        parse_mode='Markdown', reply_markup=main_keyboard())
    
    if ADMIN_USER_ID:
        try:
            bot.send_message(ADMIN_USER_ID,
                f"📋 *New Submission*\n👤 @{message.from_user.username}\n📝 {task['title']}",
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

@bot.message_handler(func=lambda m: m.text == '🔗 Visit & Earn')
def show_visit_tasks(message):
    user = get_user(message.from_user.id)
    if not user:
        bot.send_message(message.chat.id, "❌ Please use /start first!")
        return
    
    available_tasks = list(visit_tasks.find({'active': True}))
    
    if not available_tasks:
        bot.send_message(message.chat.id, "🔗 No visit tasks available!")
        return
    
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    for task in available_tasks:
        task_id = str(task['_id'])
        keyboard.add(types.InlineKeyboardButton(
            f"{task['title']} - {format_money(task['amount'])} ({task['time_required']}s)",
            callback_data=f"start_visit_{task_id}"
        ))
    
    bot.send_message(message.chat.id, "🔗 *VISIT & EARN*\n\nVisit websites and earn instantly!", parse_mode='Markdown', reply_markup=keyboard)

@bot.callback_query_handler(func=lambda call: call.data.startswith('start_visit_'))
def start_visit(call):
    task_id = call.data.replace('start_visit_', '')
    task = visit_tasks.find_one({'_id': ObjectId(task_id)})
    
    if not task:
        bot.answer_callback_query(call.id, "Task not found!")
        return
    
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
"""
    
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("✅ Complete Visit", callback_data=f"complete_visit_{task_id}"))
    keyboard.add(types.InlineKeyboardButton("🔙 Back", callback_data="back_to_visit_tasks"))
    
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
        amount = task['amount']
        update_balance(call.from_user.id, amount, 'add')
        add_transaction(call.from_user.id, amount, 'visit', f'Visit: {task["title"]}')
        
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
            bot.edit_message_text(f"✅ *COMPLETED!*\n\n💰 You earned {format_money(amount)}",
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

@bot.message_handler(func=lambda m: m.text == '💸 Withdraw')
def withdraw_menu(message):
    user = get_user(message.from_user.id)
    if not user:
        bot.send_message(message.chat.id, "❌ Please use /start first!")
        return
    
    bal = user.get('balance', 0)
    
    pending = withdrawals.find_one({'user_id': str(message.from_user.id), 'status': 'pending'})
    if pending:
        bot.send_message(message.chat.id,
            f"⚠️ *Pending Withdrawal*\nAmount: {format_money(pending['amount'])}",
            parse_mode='Markdown')
        return
    
    if bal < 50:
        bot.send_message(message.chat.id,
            f"❌ *Insufficient Balance*\nBalance: {format_money(bal)}\nMinimum: ₹50",
            parse_mode='Markdown')
        return
    
    text = f"""
💸 *WITHDRAWAL*

💰 *Available Balance:* {format_money(bal)}

📋 *Methods:*
• 💳 UPI - Min ₹50
• 🏦 Bank - Min ₹100  
• ₿ Crypto - Min ₹200

Select method below:
"""
    bot.send_message(message.chat.id, text, parse_mode='Markdown', reply_markup=withdrawal_keyboard())

@bot.callback_query_handler(func=lambda call: call.data.startswith('wd_'))
def withdrawal_method(call):
    method = call.data.replace('wd_', '')
    methods = {'upi': 'UPI', 'bank': 'Bank Transfer', 'crypto': 'Crypto'}
    min_amounts = {'upi': 50, 'bank': 100, 'crypto': 200}
    
    user = get_user(call.from_user.id)
    bal = user.get('balance', 0)
    min_amt = min_amounts.get(method, 50)
    
    if bal < min_amt:
        bot.answer_callback_query(call.id, f"Minimum is ₹{min_amt} for {methods.get(method)}!", show_alert=True)
        return
    
    bot.answer_callback_query(call.id)
    
    msg = bot.send_message(call.message.chat.id,
        f"💸 *{methods.get(method)} Withdrawal*\n\nBalance: {format_money(bal)}\nMinimum: ₹{min_amt}\n\nEnter amount:",
        parse_mode='Markdown')
    
    bot.register_next_step_handler(msg, process_withdrawal_amount, method, min_amt)

def process_withdrawal_amount(message, method, min_amt):
    if message.text and message.text.lower() == 'cancel':
        bot.send_message(message.chat.id, "❌ Cancelled.", reply_markup=main_keyboard())
        return
    
    try:
        amount = float(message.text)
    except:
        bot.send_message(message.chat.id, "❌ Enter a valid number!")
        return
    
    if amount < min_amt:
        bot.send_message(message.chat.id, f"❌ Minimum amount is ₹{min_amt}!")
        return
    
    user = get_user(message.from_user.id)
    if amount > user.get('balance', 0):
        bot.send_message(message.chat.id, f"❌ Insufficient balance!")
        return
    
    prompts = {
        'upi': "📱 *Enter your UPI ID:*\nExample: name@okhdfcbank",
        'bank': "🏦 *Enter bank details:*\nName\nAccount No\nIFSC\nBank Name",
        'crypto': "₿ *Enter wallet address:"
    }
    
    msg = bot.send_message(message.chat.id, prompts.get(method), parse_mode='Markdown')
    bot.register_next_step_handler(msg, save_withdrawal_request, method, amount)

def save_withdrawal_request(message, method, amount):
    if message.text and message.text.lower() == 'cancel':
        bot.send_message(message.chat.id, "❌ Cancelled.", reply_markup=main_keyboard())
        return
    
    details = message.text
    
    update_balance(message.from_user.id, amount, 'subtract')
    
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
    add_transaction(message.from_user.id, -amount, 'withdrawal', f'Withdrawal via {method.upper()}')
    
    bot.send_message(message.chat.id,
        f"✅ *Withdrawal Request Submitted!*\n\nAmount: {format_money(amount)}\nMethod: {method.upper()}\n\nProcessing: 24-48 hours",
        parse_mode='Markdown', reply_markup=main_keyboard())
    
    if ADMIN_USER_ID:
        try:
            bot.send_message(ADMIN_USER_ID, f"💸 *New Withdrawal*\n@{message.from_user.username}\n{format_money(amount)}", parse_mode='Markdown')
        except:
            pass

@bot.message_handler(commands=['check'])
def check_withdrawal(message):
    requests = list(withdrawals.find({'user_id': str(message.from_user.id)}).sort('requested_at', -1).limit(5))
    
    if not requests:
        bot.send_message(message.chat.id, "📭 No withdrawal requests found.")
        return
    
    text = "💸 *YOUR WITHDRAWALS*\n\n"
    for req in requests:
        emoji = {'pending': '⏳', 'approved': '✅', 'rejected': '❌'}.get(req['status'], '❓')
        req_date = req['requested_at']
        if isinstance(req_date, str):
            req_date = datetime.fromisoformat(req_date)
        text += f"{emoji} {req['method'].upper()} - {format_money(req['amount'])}\n   Status: {req['status'].upper()}\n   Date: {req_date.strftime('%d/%m/%Y')}\n\n"
    
    bot.send_message(message.chat.id, text, parse_mode='Markdown')

@bot.message_handler(func=lambda m: m.text == '❓ Help')
def show_help(message):
    text = """
❓ *HELP & SUPPORT*

📝 *How to Earn:*
1. Click "📝 Tasks" - Complete tasks
2. Click "🔗 Visit & Earn" - Visit websites
3. Click "👥 Referral Program" - Share your link
4. Click "🎁 Daily Bonus" - Claim daily reward

💸 *Withdrawal:*
• Minimum: ₹50 (UPI), ₹100 (Bank), ₹200 (Crypto)
• Processing: 24-48 hours

⚠️ *Rules: No fake submissions = Permanent Ban*

📞 Support: @Admin
"""
    bot.send_message(message.chat.id, text, parse_mode='Markdown')

@bot.message_handler(func=lambda m: m.text == 'ℹ️ About')
def show_about(message):
    total_users = users.count_documents({})
    
    text = f"""
ℹ️ *ABOUT EARNING PRO*

🤖 *Version:* 3.0

✨ *Features:*
• ✅ Task Completion
• 🔗 Website Visits
• 👥 Referral Program (₹{REFERRAL_BONUS}/referral)
• 🎁 Daily Bonus (₹{DAILY_BONUS}/day)
• 💸 Multiple Withdrawals

📊 *Statistics:*
👥 Total Users: {total_users}+

📞 *Support:* @Admin
"""
    bot.send_message(message.chat.id, text, parse_mode='Markdown')

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
        bot.send_message(message.chat.id, "Main menu.", reply_markup=main_keyboard())

@bot.message_handler(func=lambda m: m.text == '📊 Dashboard')
def admin_dashboard(message):
    if message.chat.id != ADMIN_USER_ID:
        return
    
    total_users = users.count_documents({})
    active_24h = users.count_documents({'last_active': {'$gte': datetime.now() - timedelta(hours=24)}})
    pending_withdrawals = withdrawals.count_documents({'status': 'pending'})
    pending_submissions = submissions.count_documents({'status': 'pending'})
    
    text = f"""
📊 *ADMIN DASHBOARD*

👥 *Users:*
├ Total: {total_users}
├ Active (24h): {active_24h}

💰 *Pending:*
├ Withdrawals: {pending_withdrawals}
└ Submissions: {pending_submissions}

📝 *Tasks:*
├ Active Tasks: {tasks.count_documents({'active': True})}
└ Active Visit Tasks: {visit_tasks.count_documents({'active': True})}
"""
    bot.send_message(message.chat.id, text, parse_mode='Markdown')

@bot.message_handler(func=lambda m: m.text == '👥 User Stats')
def admin_user_stats(message):
    if message.chat.id != ADMIN_USER_ID:
        return
    
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
        'total_withdrawn': {'$sum': '$total_withdrawn'}
    }}]
    
    result = list(users.aggregate(pipeline))
    stats = result[0] if result else {}
    
    pending_amount = sum([w['amount'] for w in withdrawals.find({'status': 'pending'})])
    
    text = f"""
💰 *FINANCIAL STATISTICS*

💵 User Balances: {format_money(stats.get('total_balance', 0))}
📈 Total Earned: {format_money(stats.get('total_earned', 0))}
💸 Total Withdrawn: {format_money(stats.get('total_withdrawn', 0))}

⏳ Pending Withdrawals: {format_money(pending_amount)}
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

👤 @{req.get('username', 'Unknown')}
💰 {format_money(req['amount'])}
💳 {req['method'].upper()}
📅 {req_date.strftime('%d/%m/%Y %H:%M')}

📝 Details: `{req['account_details'][:100]}`
"""
        bot.send_message(message.chat.id, text, parse_mode='Markdown', reply_markup=keyboard)

@bot.message_handler(func=lambda m: m.text == '📋 Pending Submissions')
def admin_submissions(message):
    if message.chat.id != ADMIN_USER_ID:
        return
    
    pending = list(submissions.find({'status': 'pending'}).limit(20))
    
    if not pending:
        bot.send_message(message.chat.id, "✅ No pending submissions!")
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
📋 *SUBMISSION*

👤 @{sub.get('username', 'Unknown')}
📝 {sub['task_title']}
💰 {format_money(sub['task_amount'])}
📅 {sub_date.strftime('%d/%m/%Y %H:%M')}
"""
        
        try:
            bot.send_photo(message.chat.id, sub['screenshot'], caption=caption,
                          parse_mode='Markdown', reply_markup=keyboard)
        except:
            bot.send_message(message.chat.id, caption + "\n⚠️ Screenshot unavailable",
                           parse_mode='Markdown', reply_markup=keyboard)

@bot.message_handler(func=lambda m: m.text == '📢 Broadcast')
def broadcast_prompt(message):
    if message.chat.id != ADMIN_USER_ID:
        return
    
    msg = bot.send_message(message.chat.id,
        "📢 *Broadcast Message*\n\nSend the message to broadcast to all users.\nSend 'cancel' to cancel.",
        parse_mode='Markdown')
    bot.register_next_step_handler(msg, send_broadcast)

def send_broadcast(message):
    if message.text and message.text.lower() == 'cancel':
        bot.send_message(message.chat.id, "❌ Broadcast cancelled.")
        return
    
    broadcast_text = message.text
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
            time.sleep(0.05)
        except:
            pass
    
    bot.edit_message_text(f"✅ Broadcast Complete!\n\nSent to: {success}/{total} users", 
                         message.chat.id, status_msg.message_id)

@bot.message_handler(func=lambda m: m.text == '➕ Add Task')
def add_task_prompt(message):
    if message.chat.id != ADMIN_USER_ID:
        return
    
    msg = bot.send_message(message.chat.id, 
        "📝 *Add New Task*\n\nSend: `Title | Amount | Link | Description`\n\nExample:\n`Subscribe | 5 | https://t.me/channel | Subscribe and screenshot`",
        parse_mode='Markdown')
    bot.register_next_step_handler(msg, save_new_task)

def save_new_task(message):
    if message.text and message.text.lower() == 'cancel':
        bot.send_message(message.chat.id, "❌ Cancelled.")
        return
    
    parts = message.text.split('|')
    if len(parts) < 3:
        bot.send_message(message.chat.id, "❌ Invalid format! Use: Title | Amount | Link | Description")
        return
    
    title = parts[0].strip()
    try:
        amount = float(parts[1].strip())
    except:
        bot.send_message(message.chat.id, "❌ Invalid amount!")
        return
    link = parts[2].strip() if len(parts) > 2 else ""
    description = parts[3].strip() if len(parts) > 3 else "Complete the task"
    
    tasks.insert_one({
        'title': title, 'amount': amount, 'link': link,
        'description': description, 'active': True, 'created_at': datetime.now()
    })
    bot.send_message(message.chat.id, f"✅ Task '{title}' added!", reply_markup=admin_keyboard())

@bot.message_handler(func=lambda m: m.text == '➕ Add Visit Task')
def add_visit_task_prompt(message):
    if message.chat.id != ADMIN_USER_ID:
        return
    
    msg = bot.send_message(message.chat.id,
        "🔗 *Add Visit Task*\n\nSend: `Title | Amount | Time(seconds) | Link`\n\nExample:\n`Visit Site | 2 | 10 | https://example.com`",
        parse_mode='Markdown')
    bot.register_next_step_handler(msg, save_new_visit_task)

def save_new_visit_task(message):
    if message.text and message.text.lower() == 'cancel':
        bot.send_message(message.chat.id, "❌ Cancelled.")
        return
    
    parts = message.text.split('|')
    if len(parts) < 4:
        bot.send_message(message.chat.id, "❌ Invalid format!")
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
        bot.send_message(message.chat.id, "❌ Invalid time!")
        return
    link = parts[3].strip()
    
    visit_tasks.insert_one({
        'title': title, 'amount': amount, 'time_required': time_req,
        'link': link, 'active': True, 'created_at': datetime.now()
    })
    bot.send_message(message.chat.id, f"✅ Visit task '{title}' added!", reply_markup=admin_keyboard())

# ==================== CALLBACK HANDLERS FOR ADMIN ====================
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
                f"✅ *Withdrawal Approved!*\n\n💰 {format_money(wd['amount'])} will be sent soon.",
                parse_mode='Markdown')
        except:
            pass
    
    bot.answer_callback_query(call.id, "Approved!")
    bot.edit_message_text("✅ APPROVED", call.message.chat.id, call.message.message_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith('reject_wd_'))
def reject_withdrawal(call):
    if call.message.chat.id != ADMIN_USER_ID:
        bot.answer_callback_query(call.id, "Unauthorized!")
        return
    
    wd_id = call.data.replace('reject_wd_', '')
    wd = withdrawals.find_one({'_id': ObjectId(wd_id)})
    
    if wd:
        update_balance(wd['user_id'], wd['amount'], 'add')
        withdrawals.update_one({'_id': ObjectId(wd_id)}, {'$set': {'status': 'rejected', 'processed_at': datetime.now()}})
        try:
            bot.send_message(int(wd['user_id']),
                f"❌ *Withdrawal Rejected*\n\nAmount {format_money(wd['amount'])} refunded.",
                parse_mode='Markdown')
        except:
            pass
    
    bot.answer_callback_query(call.id, "Rejected!")
    bot.edit_message_text("❌ REJECTED", call.message.chat.id, call.message.message_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith('approve_sub_'))
def approve_submission(call):
    if call.message.chat.id != ADMIN_USER_ID:
        bot.answer_callback_query(call.id, "Unauthorized!")
        return
    
    sub_id = call.data.replace('approve_sub_', '')
    sub = submissions.find_one({'_id': ObjectId(sub_id)})
    
    if sub:
        update_balance(sub['user_id'], sub['task_amount'], 'add')
        add_transaction(sub['user_id'], sub['task_amount'], 'task', f'Task: {sub["task_title"]}')
        users.update_one({'user_id': sub['user_id']}, {'$push': {'completed_tasks': sub['task_id']}})
        submissions.update_one({'_id': ObjectId(sub_id)}, {'$set': {'status': 'approved'}})
        
        try:
            bot.send_message(int(sub['user_id']),
                f"✅ *Task Approved!*\n\n{sub['task_title']}\n💰 {format_money(sub['task_amount'])}",
                parse_mode='Markdown')
        except:
            pass
    
    bot.answer_callback_query(call.id, "Approved!")
    try:
        bot.edit_message_caption("✅ APPROVED", call.message.chat.id, call.message.message_id)
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
                f"❌ *Task Rejected*\n\n{sub['task_title']}\nPlease resubmit correctly.",
                parse_mode='Markdown')
        except:
            pass
    
    bot.answer_callback_query(call.id, "Rejected!")
    try:
        bot.edit_message_caption("❌ REJECTED", call.message.chat.id, call.message.message_id)
    except:
        pass

# ==================== CATCH ALL ====================
@bot.message_handler(func=lambda m: True)
def catch_all(message):
    if not message.text.startswith('/') and message.text not in ['📝 Tasks', '🔗 Visit & Earn', '💰 My Balance', '💸 Withdraw', '👥 Referral Program', '🎁 Daily Bonus', '📊 My Stats', '❓ Help', 'ℹ️ About', ADMIN_PASSWORD, '📊 Dashboard', '👥 User Stats', '💰 Financial Stats', '💸 Withdrawal Requests', '📋 Pending Submissions', '📢 Broadcast', '➕ Add Task', '➕ Add Visit Task', '🔙 Exit Admin']:
        bot.send_message(message.chat.id, "❓ Use the buttons below.\nType /start to see all options.", reply_markup=main_keyboard())

# ==================== MAIN ====================
if __name__ == '__main__':
    print("=" * 60)
    print("🤖 PREMIUM EARNING BOT v3.0")
    print("📡 Deploying on Railway...")
    print("=" * 60)
    
    # Connect to MongoDB
    if not connect_mongodb():
        print("❌ MongoDB connection failed!")
        print("💡 Make sure MONGODB_URI is correct in Railway variables")
        sys.exit(1)
    
    # Initialize collections
    init_collections()
    
    # Start health server
    health_thread = threading.Thread(target=start_health_server, daemon=True)
    health_thread.start()
    print(f"🏥 Health server: http://0.0.0.0:{PORT}")
    
    # Remove webhook
    try:
        bot.remove_webhook()
        print("✅ Webhook removed")
    except Exception as e:
        print(f"⚠️ Webhook removal: {e}")
    
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