# main.py - Complete Professional Telegram Earning Bot with MongoDB
import telebot
from telebot import types
from pymongo import MongoClient
from datetime import datetime, timedelta
import time
import random
import string
import os
from bson import ObjectId

# --- CONFIGURATION ---
API_TOKEN = '8384600981:AAFOkWJEw0zPqouHrwFUYw9LI7m-eLBp1KE'
ADMIN_PASSWORD = 'Vansh@000'
ADMIN_USER_ID = None  # Will be set when admin logs in

# MongoDB Connection
# IMPORTANT: Fix your MongoDB connection string - remove < > and use proper password
# Current string has 'Vansh<password>' which is invalid
# Replace with your actual password without angle brackets
MONGODB_URI = 'mongodb+srv://Vansh:Vansh000@cluster0.xjwyih4.mongodb.net/?appName=Cluster0'
DB_NAME = 'telegram_earning_bot'  # Changed from 'Cluster0' to a proper database name

print(f"Connecting to MongoDB...")
print(f"URI: {MONGODB_URI.replace('Vansh000', '******')}")  # Hide password in logs

try:
    client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=10000)
    db = client[DB_NAME]
    # Test connection
    client.admin.command('ping')
    print("✅ Connected to MongoDB successfully!")
except Exception as e:
    print(f"❌ MongoDB Connection Error: {e}")
    print("Please check your MongoDB Atlas credentials!")
    print("Make sure:")
    print("1. Username and password are correct")
    print("2. Network access is configured (allow all IPs or your IP)")
    print("3. Database user has proper permissions")
    exit(1)

# Collections
users_collection = db['users']
tasks_collection = db['tasks']
visit_tasks_collection = db['visit_tasks']
withdrawal_requests_collection = db['withdrawal_requests']
completed_tasks_collection = db['completed_tasks']
completed_visits_collection = db['completed_visits']
task_submissions_collection = db['task_submissions']
referrals_collection = db['referrals']

# Create indexes for better performance
try:
    users_collection.create_index('user_id', unique=True)
    users_collection.create_index('referral_code')
    tasks_collection.create_index('type')
    withdrawal_requests_collection.create_index([('user_id', 1), ('status', 1)])
    task_submissions_collection.create_index([('user_id', 1), ('status', 1)])
    print("✅ Database indexes created")
except Exception as e:
    print(f"⚠️ Index creation warning: {e}")

# Initialize bot
bot = telebot.TeleBot(API_TOKEN)

# --- Helper Functions ---
def generate_referral_code():
    """Generate unique referral code"""
    while True:
        code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
        if not users_collection.find_one({'referral_code': code}):
            return code

def get_user(user_id):
    """Get user from database"""
    try:
        user = users_collection.find_one({'user_id': str(user_id)})
        if user and '_id' in user:
            user['_id'] = str(user['_id'])
        return user
    except Exception as e:
        print(f"Error getting user: {e}")
        return None

def update_user_balance(user_id, amount, operation='add'):
    """Update user balance"""
    user = get_user(user_id)
    if not user:
        return False
    
    current_balance = user.get('balance', 0)
    if operation == 'add':
        new_balance = current_balance + amount
    elif operation == 'subtract':
        if current_balance < amount:
            return False
        new_balance = current_balance - amount
    else:
        return False
    
    try:
        users_collection.update_one(
            {'user_id': str(user_id)},
            {'$set': {'balance': new_balance}}
        )
        return True
    except Exception as e:
        print(f"Error updating balance: {e}")
        return False

def add_transaction(user_id, amount, trans_type, description):
    """Add transaction record"""
    try:
        users_collection.update_one(
            {'user_id': str(user_id)},
            {'$push': {
                'transactions': {
                    'amount': amount,
                    'type': trans_type,
                    'description': description,
                    'date': datetime.now(),
                    'status': 'completed'
                }
            }}
        )
    except Exception as e:
        print(f"Error adding transaction: {e}")

def format_balance(balance):
    """Format balance with ₹ symbol"""
    return f"₹{balance:.2f}"

# --- Keyboards ---
def main_keyboard():
    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    btn1 = types.KeyboardButton('📝 Tasks')
    btn2 = types.KeyboardButton('🔗 Visit Tasks')
    btn3 = types.KeyboardButton('💰 Balance')
    btn4 = types.KeyboardButton('💸 Withdraw')
    btn5 = types.KeyboardButton('👥 Refer & Earn')
    btn6 = types.KeyboardButton('📢 Advertisement')
    btn7 = types.KeyboardButton('📊 My Stats')
    btn8 = types.KeyboardButton('ℹ️ About')
    markup.add(btn1, btn2, btn3, btn4, btn5, btn6, btn7, btn8)
    return markup

def admin_keyboard():
    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.add('📊 Total Users', '💰 Total Balance')
    markup.add('📝 Manage Tasks', '🔗 Manage Visit Tasks')
    markup.add('💸 Withdrawal Requests', '📋 Task Submissions')
    markup.add('👥 Referral Stats', '📢 Broadcast Message')
    markup.add('🔙 Back to Menu')
    return markup

def withdrawal_methods_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=1)
    btn1 = types.InlineKeyboardButton('UPI (₹50 Min)', callback_data='withdraw_upi')
    btn2 = types.InlineKeyboardButton('Bank Transfer (₹100 Min)', callback_data='withdraw_bank')
    btn3 = types.InlineKeyboardButton('Bitcoin (₹200 Min)', callback_data='withdraw_crypto')
    markup.add(btn1, btn2, btn3)
    return markup

# --- Start Command ---
@bot.message_handler(commands=['start'])
def start(message):
    user_id = str(message.from_user.id)
    username = message.from_user.username or "No username"
    first_name = message.from_user.first_name
    
    # Check if user exists
    user = get_user(user_id)
    
    # Handle referral
    referral_code = None
    if len(message.text.split()) > 1:
        referral_code = message.text.split()[1]
    
    if not user:
        # Create new user
        new_referral_code = generate_referral_code()
        user_data = {
            'user_id': user_id,
            'username': username,
            'first_name': first_name,
            'balance': 0.0,
            'total_earned': 0.0,
            'total_withdrawn': 0.0,
            'referral_code': new_referral_code,
            'referred_by': None,
            'referral_earnings': 0.0,
            'total_referrals': 0,
            'joined_date': datetime.now(),
            'transactions': [],
            'withdrawal_requests': [],
            'completed_tasks': [],
            'completed_visits': []
        }
        
        # Process referral
        if referral_code:
            referrer = users_collection.find_one({'referral_code': referral_code})
            if referrer and referrer['user_id'] != user_id:
                user_data['referred_by'] = referrer['user_id']
                # Add ₹2 to referrer
                update_user_balance(referrer['user_id'], 2, 'add')
                users_collection.update_one(
                    {'user_id': referrer['user_id']},
                    {'$inc': {'total_referrals': 1, 'referral_earnings': 2}}
                )
                add_transaction(referrer['user_id'], 2, 'referral', f'New user joined via your referral')
                bot.send_message(int(referrer['user_id']), f"🎉 New user joined via your referral! You earned ₹2!")
        
        users_collection.insert_one(user_data)
        
        welcome_msg = f"""👋 Welcome {first_name} to Earning Bot!

✨ Start earning money by completing simple tasks!

📌 Your Features:
✅ Complete Tasks - Earn money
✅ Visit Websites - Earn per visit
✅ Refer Friends - Earn ₹2 per referral
✅ Instant Withdrawals

🔑 Your Referral Code: `{new_referral_code}`

Share this code with friends to earn ₹2 each!

Start earning now by clicking the buttons below!"""
        
        bot.send_message(message.chat.id, welcome_msg, reply_markup=main_keyboard(), parse_mode='Markdown')
    else:
        bot.send_message(message.chat.id, f"👋 Welcome back {first_name}!", reply_markup=main_keyboard())

# --- Balance Check ---
@bot.message_handler(func=lambda message: message.text == '💰 Balance')
def check_balance(message):
    user = get_user(message.from_user.id)
    if user:
        balance = user.get('balance', 0)
        total_earned = user.get('total_earned', 0)
        total_withdrawn = user.get('total_withdrawn', 0)
        
        msg = f"""💰 *Your Wallet Balance*

💵 Available Balance: {format_balance(balance)}
📈 Total Earned: {format_balance(total_earned)}
💸 Total Withdrawn: {format_balance(total_withdrawn)}

📊 *Referral Stats*
👥 Total Referrals: {user.get('total_referrals', 0)}
🎁 Referral Earnings: {format_balance(user.get('referral_earnings', 0))}

Continue completing tasks to earn more!"""
        
        bot.send_message(message.chat.id, msg, parse_mode='Markdown')

# --- My Stats ---
@bot.message_handler(func=lambda message: message.text == '📊 My Stats')
def my_stats(message):
    user = get_user(message.from_user.id)
    if user:
        joined_date = user.get('joined_date')
        if isinstance(joined_date, str):
            joined_date = datetime.fromisoformat(joined_date)
        
        msg = f"""📊 *Your Statistics*

👤 Username: @{user.get('username', 'N/A')}
🎯 User ID: {user['user_id']}
📅 Joined: {joined_date.strftime('%d/%m/%Y') if joined_date else 'Unknown'}

💰 *Financial Stats*
Balance: {format_balance(user.get('balance', 0))}
Total Earned: {format_balance(user.get('total_earned', 0))}
Total Withdrawn: {format_balance(user.get('total_withdrawn', 0))}

👥 *Referral Stats*
Your Code: `{user.get('referral_code')}`
Total Referrals: {user.get('total_referrals', 0)}
Referral Earnings: {format_balance(user.get('referral_earnings', 0))}

📝 Tasks Completed: {len(user.get('completed_tasks', []))}
🔗 Visits Completed: {len(user.get('completed_visits', []))}

Share your referral code to earn more!"""
        
        bot.send_message(message.chat.id, msg, parse_mode='Markdown')

# --- Refer & Earn ---
@bot.message_handler(func=lambda message: message.text == '👥 Refer & Earn')
def refer_earn(message):
    user = get_user(message.from_user.id)
    if user:
        bot_username = bot.get_me().username
        bot_link = f"https://t.me/{bot_username}?start={user['referral_code']}"
        
        msg = f"""👥 *Refer & Earn Program*

🎁 Earn ₹2 for every friend who joins using your referral!

📌 *How it works:*
1️⃣ Share your unique referral link
2️⃣ Friend joins through your link
3️⃣ You get ₹2 instantly in your wallet
4️⃣ Unlimited referrals, unlimited earnings!

🔑 *Your Referral Code:* `{user['referral_code']}`

🔗 *Your Referral Link:*
`{bot_link}`

📊 *Your Stats:*
Total Referrals: {user.get('total_referrals', 0)}
Total Earned: {format_balance(user.get('referral_earnings', 0))}

Share now and start earning! 🚀"""
        
        bot.send_message(message.chat.id, msg, parse_mode='Markdown')

# --- Tasks Menu ---
@bot.message_handler(func=lambda message: message.text == '📝 Tasks')
def show_tasks(message):
    tasks = list(tasks_collection.find({'active': True}))
    if not tasks:
        bot.send_message(message.chat.id, "📝 No tasks available at the moment. Please check back later!")
        return
    
    markup = types.InlineKeyboardMarkup(row_width=1)
    for task in tasks:
        btn = types.InlineKeyboardButton(f"{task['title']} - {format_balance(task['amount'])}", 
                                        callback_data=f"task_{task['_id']}")
        markup.add(btn)
    
    bot.send_message(message.chat.id, "📝 *Available Tasks*\n\nComplete these tasks to earn money:", 
                    parse_mode='Markdown', reply_markup=markup)

# --- Handle Task Callback ---
@bot.callback_query_handler(func=lambda call: call.data.startswith('task_'))
def handle_task(call):
    task_id = call.data.split('_')[1]
    task = tasks_collection.find_one({'_id': ObjectId(task_id)})
    
    if not task:
        bot.answer_callback_query(call.id, "Task not found!")
        return
    
    # Check if user already completed this task
    user = get_user(call.from_user.id)
    if task_id in user.get('completed_tasks', []):
        bot.answer_callback_query(call.id, "You have already completed this task!")
        return
    
    # Check task limit
    completed_count = len(list(completed_tasks_collection.find({'task_id': task_id})))
    if completed_count >= task.get('limit', 999999):
        bot.answer_callback_query(call.id, "This task has reached its limit!")
        return
    
    task_msg = f"""📝 *{task['title']}*

💰 Reward: {format_balance(task['amount'])}

📋 *Instructions:*
{task['description']}

🔗 *Task Link:*
{task['link']}

✅ *How to complete:*
1. Click the link above
2. Complete the required action
3. Take a screenshot as proof
4. Submit using the button below

⚠️ Note: Fake submissions will result in account ban!"""
    
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("📸 Submit Screenshot", callback_data=f"submit_task_{task_id}"))
    markup.add(types.InlineKeyboardButton("🔙 Back to Tasks", callback_data="back_to_tasks"))
    
    bot.edit_message_text(task_msg, call.message.chat.id, call.message.message_id, 
                         parse_mode='Markdown', reply_markup=markup)

# --- Submit Task Screenshot ---
@bot.callback_query_handler(func=lambda call: call.data.startswith('submit_task_'))
def submit_task_screenshot(call):
    task_id = call.data.split('_')[2]
    task = tasks_collection.find_one({'_id': ObjectId(task_id)})
    
    if not task:
        bot.answer_callback_query(call.id, "Task not found!")
        return
    
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, 
                          f"📸 Please send the screenshot proof for task: *{task['title']}*\n\n"
                          "Make sure the screenshot clearly shows the completed action.",
                          parse_mode='Markdown')
    
    bot.register_next_step_handler(msg, process_task_screenshot, task_id, call.message)

def process_task_screenshot(message, task_id, original_msg):
    if message.photo:
        # Get photo file ID
        photo_file_id = message.photo[-1].file_id
        
        # Save submission
        submission = {
            'user_id': str(message.from_user.id),
            'task_id': task_id,
            'screenshot': photo_file_id,
            'status': 'pending',
            'submitted_at': datetime.now(),
            'username': message.from_user.username,
            'first_name': message.from_user.first_name
        }
        
        task_submissions_collection.insert_one(submission)
        
        # Notify admin if admin is logged in
        global ADMIN_USER_ID
        if ADMIN_USER_ID:
            task = tasks_collection.find_one({'_id': ObjectId(task_id)})
            admin_msg = f"📋 *New Task Submission*\n\n"
            admin_msg += f"User: @{message.from_user.username}\n"
            admin_msg += f"User ID: {message.from_user.id}\n"
            admin_msg += f"Task: {task['title']}\n"
            admin_msg += f"Reward: {format_balance(task['amount'])}\n\n"
            admin_msg += f"Use admin panel to review submissions!"
            
            bot.send_message(ADMIN_USER_ID, admin_msg, parse_mode='Markdown')
        
        bot.send_message(message.chat.id, 
                        f"✅ Task submitted successfully!\n\n"
                        f"Your submission is pending admin approval.\n"
                        f"You will receive the reward once approved.\n\n"
                        f"⚠️ This may take up to 24 hours.")
    else:
        bot.send_message(message.chat.id, 
                        "❌ Please send a valid screenshot photo!\n"
                        "Please try submitting the task again.")

# --- Visit Tasks ---
@bot.message_handler(func=lambda message: message.text == '🔗 Visit Tasks')
def show_visit_tasks(message):
    tasks = list(visit_tasks_collection.find({'active': True}))
    if not tasks:
        bot.send_message(message.chat.id, "🔗 No visit tasks available at the moment!")
        return
    
    markup = types.InlineKeyboardMarkup(row_width=1)
    for task in tasks:
        btn = types.InlineKeyboardButton(f"{task['title']} - {format_balance(task['amount'])}", 
                                        callback_data=f"visit_{task['_id']}")
        markup.add(btn)
    
    bot.send_message(message.chat.id, "🔗 *Available Visit Tasks*\n\nVisit these websites and stay for the required time:", 
                    parse_mode='Markdown', reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('visit_'))
def handle_visit_task(call):
    task_id = call.data.split('_')[1]
    task = visit_tasks_collection.find_one({'_id': ObjectId(task_id)})
    
    if not task:
        bot.answer_callback_query(call.id, "Task not found!")
        return
    
    user_id = str(call.from_user.id)
    
    # Check if user completed this task in last 24 hours
    recent_completion = completed_visits_collection.find_one({
        'user_id': user_id,
        'task_id': task_id,
        'completed_at': {'$gte': datetime.now() - timedelta(hours=24)}
    })
    
    if recent_completion:
        time_remaining = datetime.now() - recent_completion['completed_at']
        hours_left = 24 - (time_remaining.seconds // 3600)
        bot.answer_callback_query(call.id, f"You can do this task again after {hours_left} hours!")
        return
    
    # Check task limit
    completed_count = len(list(completed_visits_collection.find({'task_id': task_id})))
    if completed_count >= task.get('limit', 999999):
        bot.answer_callback_query(call.id, "This task has reached its limit!")
        return
    
    # Start visit timer
    visit_msg = f"""🔗 *{task['title']}*

💰 Reward: {format_balance(task['amount'])}
⏱️ Time Required: {task['time_required']} seconds

🔗 *Website Link:*
{task['link']}

⚠️ *Important:*
1. Click the link and stay on the website
2. You must stay for {task['time_required']} seconds
3. Don't close the browser early
4. Complete the captcha if required

✅ Click the button below after staying for the required time!"""
    
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("✅ Complete Visit", callback_data=f"complete_visit_{task_id}"))
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="back_to_visits"))
    
    bot.edit_message_text(visit_msg, call.message.chat.id, call.message.message_id, 
                         parse_mode='Markdown', reply_markup=markup)
    
    # Store visit start time
    users_collection.update_one(
        {'user_id': user_id},
        {'$set': {f'visit_start_{task_id}': datetime.now()}}
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith('complete_visit_'))
def complete_visit(call):
    task_id = call.data.split('_')[2]
    task = visit_tasks_collection.find_one({'_id': ObjectId(task_id)})
    user_id = str(call.from_user.id)
    
    if not task:
        bot.answer_callback_query(call.id, "Task not found!")
        return
    
    # Check if time requirement is met
    user = get_user(user_id)
    start_time = user.get(f'visit_start_{task_id}')
    
    if not start_time:
        bot.answer_callback_query(call.id, "Please start the visit task first!")
        return
    
    if isinstance(start_time, str):
        start_time = datetime.fromisoformat(start_time)
    
    time_elapsed = (datetime.now() - start_time).seconds
    
    if time_elapsed >= task['time_required']:
        # Reward user
        amount = task['amount']
        update_user_balance(user_id, amount, 'add')
        add_transaction(user_id, amount, 'visit_task', f'Completed: {task["title"]}')
        
        # Update user stats
        users_collection.update_one(
            {'user_id': user_id},
            {'$inc': {'total_earned': amount}, '$push': {'completed_visits': task_id}}
        )
        
        # Record completion
        completed_visits_collection.insert_one({
            'user_id': user_id,
            'task_id': task_id,
            'completed_at': datetime.now(),
            'amount': amount
        })
        
        bot.answer_callback_query(call.id, f"✅ Task completed! You earned {format_balance(amount)}!")
        bot.edit_message_text(f"✅ *Visit Completed!*\n\nYou earned {format_balance(amount)}!\n\n"
                             f"You can do this task again after 24 hours.",
                             call.message.chat.id, call.message.message_id, parse_mode='Markdown')
        
        # Clean up
        users_collection.update_one(
            {'user_id': user_id},
            {'$unset': {f'visit_start_{task_id}': ''}}
        )
    else:
        remaining = task['time_required'] - time_elapsed
        bot.answer_callback_query(call.id, f"❌ Please stay on the website for {remaining} more seconds!")

# --- Withdrawal System ---
@bot.message_handler(func=lambda message: message.text == '💸 Withdraw')
def withdrawal_menu(message):
    user = get_user(message.from_user.id)
    balance = user.get('balance', 0)
    
    msg = f"""💸 *Withdrawal Request*

💰 Available Balance: {format_balance(balance)}

📋 *Minimum Withdrawal Amounts:*
• UPI: ₹50 minimum
• Bank Transfer: ₹100 minimum  
• Bitcoin: ₹200 minimum

⚠️ *Processing Time:* 24-48 hours
📝 *Fee:* No processing fees

Select your withdrawal method below:"""

    bot.send_message(message.chat.id, msg, parse_mode='Markdown', 
                    reply_markup=withdrawal_methods_keyboard())

@bot.callback_query_handler(func=lambda call: call.data.startswith('withdraw_'))
def process_withdrawal_method(call):
    method = call.data.split('_')[1]
    user = get_user(call.from_user.id)
    balance = user.get('balance', 0)
    
    min_amounts = {
        'upi': 50,
        'bank': 100,
        'crypto': 200
    }
    
    min_amount = min_amounts.get(method, 50)
    
    if balance < min_amount:
        bot.answer_callback_query(call.id, f"Insufficient balance! Minimum withdrawal for this method is ₹{min_amount}")
        return
    
    msg = bot.send_message(call.message.chat.id, 
                          f"💸 *Withdrawal Request - {'UPI' if method == 'upi' else 'Bank Transfer' if method == 'bank' else 'Bitcoin'}*\n\n"
                          f"💰 Available Balance: {format_balance(balance)}\n"
                          f"📊 Minimum Amount: ₹{min_amount}\n\n"
                          f"Please enter the amount you want to withdraw (in ₹):\n\n"
                          f"Type 'cancel' to cancel the request.")
    
    bot.register_next_step_handler(msg, process_withdrawal_amount, method, min_amount)

def process_withdrawal_amount(message, method, min_amount):
    if message.text.lower() == 'cancel':
        bot.send_message(message.chat.id, "❌ Withdrawal request cancelled.", reply_markup=main_keyboard())
        return
    
    try:
        amount = float(message.text)
        user = get_user(message.from_user.id)
        
        if amount < min_amount:
            bot.send_message(message.chat.id, f"❌ Minimum withdrawal amount is ₹{min_amount}!")
            return
        
        if amount > user.get('balance', 0):
            bot.send_message(message.chat.id, f"❌ Insufficient balance! Your balance is {format_balance(user['balance'])}")
            return
        
        # Get account details
        if method == 'upi':
            msg = bot.send_message(message.chat.id, "Please enter your UPI ID (e.g., example@okhdfcbank):")
            bot.register_next_step_handler(msg, save_withdrawal_request, method, amount)
        elif method == 'bank':
            msg = bot.send_message(message.chat.id, 
                                 "Please enter your bank details in this format:\n\n"
                                 "Account Holder Name\n"
                                 "Account Number\n"
                                 "IFSC Code\n"
                                 "Bank Name")
            bot.register_next_step_handler(msg, save_withdrawal_request, method, amount)
        else:  # crypto
            msg = bot.send_message(message.chat.id, "Please enter your Bitcoin wallet address:")
            bot.register_next_step_handler(msg, save_withdrawal_request, method, amount)
            
    except ValueError:
        bot.send_message(message.chat.id, "❌ Please enter a valid number!")
        return

def save_withdrawal_request(message, method, amount):
    if message.text.lower() == 'cancel':
        bot.send_message(message.chat.id, "❌ Withdrawal request cancelled.")
        return
    
    account_details = message.text
    
    # Create withdrawal request
    request = {
        'user_id': str(message.from_user.id),
        'username': message.from_user.username,
        'amount': amount,
        'method': method,
        'account_details': account_details,
        'status': 'pending',
        'requested_at': datetime.now(),
        'processed_at': None,
        'remarks': ''
    }
    
    result = withdrawal_requests_collection.insert_one(request)
    
    # Deduct balance
    update_user_balance(message.from_user.id, amount, 'subtract')
    users_collection.update_one(
        {'user_id': str(message.from_user.id)},
        {'$inc': {'total_withdrawn': amount}}
    )
    
    bot.send_message(message.chat.id, 
                    f"✅ *Withdrawal Request Submitted!*\n\n"
                    f"Amount: {format_balance(amount)}\n"
                    f"Method: {method.upper()}\n"
                    f"Request ID: {result.inserted_id}\n\n"
                    f"Your request will be processed within 24-48 hours.\n"
                    f"Use /check_withdrawal to check status.",
                    parse_mode='Markdown')
    
    # Notify admin
    global ADMIN_USER_ID
    if ADMIN_USER_ID:
        admin_msg = f"💸 *New Withdrawal Request*\n\n"
        admin_msg += f"User: @{message.from_user.username}\n"
        admin_msg += f"Amount: {format_balance(amount)}\n"
        admin_msg += f"Method: {method.upper()}\n"
        admin_msg += f"Details: {account_details}\n"
        admin_msg += f"Go to admin panel to process!"
        
        bot.send_message(ADMIN_USER_ID, admin_msg, parse_mode='Markdown')

@bot.message_handler(commands=['check_withdrawal'])
def check_withdrawal_status(message):
    requests = list(withdrawal_requests_collection.find(
        {'user_id': str(message.from_user.id)}
    ).sort('requested_at', -1))
    
    if not requests:
        bot.send_message(message.chat.id, "📝 No withdrawal requests found!")
        return
    
    msg = "💸 *Your Withdrawal Requests*\n\n"
    for req in requests[:5]:  # Show last 5 requests
        status_emoji = {
            'pending': '⏳',
            'approved': '✅',
            'rejected': '❌'
        }.get(req['status'], '❓')
        
        msg += f"{status_emoji} *{req['method'].upper()}* - {format_balance(req['amount'])}\n"
        msg += f"Status: {req['status'].upper()}\n"
        msg += f"Date: {req['requested_at'].strftime('%d/%m/%Y')}\n\n"
    
    bot.send_message(message.chat.id, msg, parse_mode='Markdown')

# --- Admin Panel ---
@bot.message_handler(func=lambda message: message.text == ADMIN_PASSWORD)
def activate_admin(message):
    global ADMIN_USER_ID
    ADMIN_USER_ID = message.chat.id
    bot.send_message(message.chat.id, 
                    "✅ *Admin Panel Activated!*\n\nWelcome Vansh! Use the buttons below to manage the bot.",
                    parse_mode='Markdown', reply_markup=admin_keyboard())

@bot.message_handler(func=lambda message: message.text == '📊 Total Users')
def total_users(message):
    if message.chat.id != ADMIN_USER_ID:
        return
    
    total = users_collection.count_documents({})
    active_today = users_collection.count_documents({
        'joined_date': {'$gte': datetime.now().replace(hour=0, minute=0, second=0)}
    })
    
    msg = f"📊 *Bot Statistics*\n\n"
    msg += f"👥 Total Users: {total}\n"
    msg += f"📅 Joined Today: {active_today}\n"
    
    # Calculate total balance safely
    all_users = list(users_collection.find())
    total_balance = sum([u.get('balance', 0) for u in all_users])
    msg += f"💰 Total Balance Across Users: {format_balance(total_balance)}\n"
    
    bot.send_message(message.chat.id, msg, parse_mode='Markdown')

@bot.message_handler(func=lambda message: message.text == '💰 Total Balance')
def total_balance(message):
    if message.chat.id != ADMIN_USER_ID:
        return
    
    all_users = list(users_collection.find())
    total_balance = sum([u.get('balance', 0) for u in all_users])
    total_earned = sum([u.get('total_earned', 0) for u in all_users])
    total_withdrawn = sum([u.get('total_withdrawn', 0) for u in all_users])
    
    msg = f"💰 *Financial Statistics*\n\n"
    msg += f"💵 Total User Balance: {format_balance(total_balance)}\n"
    msg += f"📈 Total Earnings: {format_balance(total_earned)}\n"
    msg += f"💸 Total Withdrawn: {format_balance(total_withdrawn)}\n"
    msg += f"📊 System Profit: {format_balance(total_earned - total_withdrawn)}"
    
    bot.send_message(message.chat.id, msg, parse_mode='Markdown')

@bot.message_handler(func=lambda message: message.text == '💸 Withdrawal Requests')
def view_withdrawal_requests(message):
    if message.chat.id != ADMIN_USER_ID:
        return
    
    pending_requests = list(withdrawal_requests_collection.find({'status': 'pending'}))
    
    if not pending_requests:
        bot.send_message(message.chat.id, "📝 No pending withdrawal requests!")
        return
    
    for req in pending_requests[:5]:  # Show 5 at a time
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton("✅ Approve", callback_data=f"approve_wd_{req['_id']}"),
            types.InlineKeyboardButton("❌ Reject", callback_data=f"reject_wd_{req['_id']}")
        )
        
        req_date = req['requested_at']
        if isinstance(req_date, str):
            req_date = datetime.fromisoformat(req_date)
        
        msg = f"💸 *Withdrawal Request*\n\n"
        msg += f"User: @{req['username']}\n"
        msg += f"User ID: {req['user_id']}\n"
        msg += f"Amount: {format_balance(req['amount'])}\n"
        msg += f"Method: {req['method'].upper()}\n"
        msg += f"Details: {req['account_details']}\n"
        msg += f"Requested: {req_date.strftime('%d/%m/%Y %H:%M')}"
        
        bot.send_message(message.chat.id, msg, parse_mode='Markdown', reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('approve_wd_'))
def approve_withdrawal(call):
    if call.message.chat.id != ADMIN_USER_ID:
        bot.answer_callback_query(call.id, "Unauthorized!")
        return
    
    request_id = call.data.split('_')[2]
    withdrawal_requests_collection.update_one(
        {'_id': ObjectId(request_id)},
        {'$set': {'status': 'approved', 'processed_at': datetime.now()}}
    )
    
    req = withdrawal_requests_collection.find_one({'_id': ObjectId(request_id)})
    
    if req:
        # Notify user
        bot.send_message(int(req['user_id']), 
                        f"✅ *Withdrawal Request Approved!*\n\n"
                        f"Amount: {format_balance(req['amount'])}\n"
                        f"Amount has been sent to your {req['method'].upper()} account.\n\n"
                        f"Thank you for using our service!",
                        parse_mode='Markdown')
    
    bot.answer_callback_query(call.id, "Withdrawal approved!")
    bot.edit_message_text("✅ *Approved*", call.message.chat.id, call.message.message_id, parse_mode='Markdown')

@bot.callback_query_handler(func=lambda call: call.data.startswith('reject_wd_'))
def reject_withdrawal(call):
    if call.message.chat.id != ADMIN_USER_ID:
        bot.answer_callback_query(call.id, "Unauthorized!")
        return
    
    request_id = call.data.split('_')[2]
    
    # Refund balance
    req = withdrawal_requests_collection.find_one({'_id': ObjectId(request_id)})
    if req:
        update_user_balance(req['user_id'], req['amount'], 'add')
        
        withdrawal_requests_collection.update_one(
            {'_id': ObjectId(request_id)},
            {'$set': {'status': 'rejected', 'processed_at': datetime.now()}}
        )
        
        # Notify user
        bot.send_message(int(req['user_id']), 
                        f"❌ *Withdrawal Request Rejected*\n\n"
                        f"Amount: {format_balance(req['amount'])}\n"
                        f"Reason: Please check your account details and try again.\n\n"
                        f"Amount has been refunded to your wallet.",
                        parse_mode='Markdown')
    
    bot.answer_callback_query(call.id, "Withdrawal rejected!")
    bot.edit_message_text("❌ *Rejected*", call.message.chat.id, call.message.message_id, parse_mode='Markdown')

@bot.message_handler(func=lambda message: message.text == '📝 Manage Tasks')
def manage_tasks(message):
    if message.chat.id != ADMIN_USER_ID:
        return
    
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("➕ Add New Task", callback_data="add_task"),
        types.InlineKeyboardButton("📋 View Tasks", callback_data="view_tasks"),
        types.InlineKeyboardButton("📊 Task Submissions", callback_data="view_submissions")
    )
    
    bot.send_message(message.chat.id, "📝 *Task Management Panel*\n\nManage your tasks here:", 
                    parse_mode='Markdown', reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == 'add_task')
def add_task_form(call):
    if call.message.chat.id != ADMIN_USER_ID:
        bot.answer_callback_query(call.id, "Unauthorized!")
        return
    
    msg = bot.send_message(call.message.chat.id, 
                          "📝 *Add New Task*\n\n"
                          "Please send task details in this format:\n\n"
                          "Title | Amount | Description | Link | Limit\n\n"
                          "Example:\n"
                          "Subscribe to Channel | 5 | Subscribe to our YouTube channel | https://youtube.com/... | 100")
    
    bot.register_next_step_handler(msg, process_add_task)

def process_add_task(message):
    if message.chat.id != ADMIN_USER_ID:
        return
    
    try:
        parts = message.text.split('|')
        if len(parts) != 5:
            bot.send_message(message.chat.id, "❌ Invalid format! Please use: Title | Amount | Description | Link | Limit")
            return
        
        task = {
            'title': parts[0].strip(),
            'amount': float(parts[1].strip()),
            'description': parts[2].strip(),
            'link': parts[3].strip(),
            'limit': int(parts[4].strip()),
            'active': True,
            'type': 'task',
            'created_at': datetime.now()
        }
        
        tasks_collection.insert_one(task)
        bot.send_message(message.chat.id, f"✅ Task '{task['title']}' added successfully!")
        
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Error: {str(e)}")

@bot.callback_query_handler(func=lambda call: call.data == 'view_submissions')
def view_submissions(call):
    if call.message.chat.id != ADMIN_USER_ID:
        bot.answer_callback_query(call.id, "Unauthorized!")
        return
    
    submissions = list(task_submissions_collection.find({'status': 'pending'}))
    
    if not submissions:
        bot.send_message(call.message.chat.id, "📋 No pending task submissions!")
        return
    
    for submission in submissions[:5]:
        task = tasks_collection.find_one({'_id': ObjectId(submission['task_id'])})
        
        if not task:
            continue
            
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton("✅ Approve", callback_data=f"approve_sub_{submission['_id']}"),
            types.InlineKeyboardButton("❌ Reject", callback_data=f"reject_sub_{submission['_id']}")
        )
        
        sub_date = submission['submitted_at']
        if isinstance(sub_date, str):
            sub_date = datetime.fromisoformat(sub_date)
        
        msg = f"📋 *Task Submission*\n\n"
        msg += f"User: @{submission['username']}\n"
        msg += f"User ID: {submission['user_id']}\n"
        msg += f"Task: {task['title']}\n"
        msg += f"Reward: {format_balance(task['amount'])}\n\n"
        msg += f"Submitted: {sub_date.strftime('%d/%m/%Y %H:%M')}"
        
        bot.send_photo(call.message.chat.id, submission['screenshot'], caption=msg, 
                      parse_mode='Markdown', reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('approve_sub_'))
def approve_submission(call):
    if call.message.chat.id != ADMIN_USER_ID:
        bot.answer_callback_query(call.id, "Unauthorized!")
        return
    
    submission_id = call.data.split('_')[2]
    submission = task_submissions_collection.find_one({'_id': ObjectId(submission_id)})
    
    if submission:
        task = tasks_collection.find_one({'_id': ObjectId(submission['task_id'])})
        
        if task:
            # Add reward to user
            update_user_balance(submission['user_id'], task['amount'], 'add')
            add_transaction(submission['user_id'], task['amount'], 'task', f'Completed: {task["title"]}')
            
            # Update user stats
            users_collection.update_one(
                {'user_id': submission['user_id']},
                {'$inc': {'total_earned': task['amount']}, '$push': {'completed_tasks': submission['task_id']}}
            )
            
            # Record completion
            completed_tasks_collection.insert_one({
                'user_id': submission['user_id'],
                'task_id': submission['task_id'],
                'completed_at': datetime.now(),
                'amount': task['amount']
            })
            
            # Notify user
            bot.send_message(int(submission['user_id']), 
                            f"✅ *Task Approved!*\n\n"
                            f"Your submission for task '{task['title']}' has been approved!\n"
                            f"You earned {format_balance(task['amount'])}!\n\n"
                            f"Thank you for completing the task!",
                            parse_mode='Markdown')
        
        # Update submission status
        task_submissions_collection.update_one(
            {'_id': ObjectId(submission_id)},
            {'$set': {'status': 'approved', 'processed_at': datetime.now()}}
        )
        
        bot.answer_callback_query(call.id, "Task approved!")
        bot.edit_message_caption(call.message.chat.id, call.message.message_id, 
                               caption="✅ *Approved*", parse_mode='Markdown')

@bot.callback_query_handler(func=lambda call: call.data.startswith('reject_sub_'))
def reject_submission(call):
    if call.message.chat.id != ADMIN_USER_ID:
        bot.answer_callback_query(call.id, "Unauthorized!")
        return
    
    submission_id = call.data.split('_')[2]
    submission = task_submissions_collection.find_one({'_id': ObjectId(submission_id)})
    
    if submission:
        task = tasks_collection.find_one({'_id': ObjectId(submission['task_id'])})
        
        task_submissions_collection.update_one(
            {'_id': ObjectId(submission_id)},
            {'$set': {'status': 'rejected', 'processed_at': datetime.now()}}
        )
        
        # Notify user
        bot.send_message(int(submission['user_id']), 
                        f"❌ *Task Rejected*\n\n"
                        f"Your submission for task '{task['title'] if task else 'Unknown'}' has been rejected.\n"
                        f"Please make sure to follow the instructions carefully and resubmit.\n\n"
                        f"Contact support for more information.",
                        parse_mode='Markdown')
        
        bot.answer_callback_query(call.id, "Task rejected!")
        bot.edit_message_caption(call.message.chat.id, call.message.message_id, 
                               caption="❌ *Rejected*", parse_mode='Markdown')

@bot.message_handler(func=lambda message: message.text == '🔗 Manage Visit Tasks')
def manage_visit_tasks(message):
    if message.chat.id != ADMIN_USER_ID:
        return
    
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("➕ Add Visit Task", callback_data="add_visit_task"),
        types.InlineKeyboardButton("📋 View Visit Tasks", callback_data="view_visit_tasks")
    )
    
    bot.send_message(message.chat.id, "🔗 *Visit Task Management*", 
                    parse_mode='Markdown', reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == 'add_visit_task')
def add_visit_task_form(call):
    if call.message.chat.id != ADMIN_USER_ID:
        bot.answer_callback_query(call.id, "Unauthorized!")
        return
    
    msg = bot.send_message(call.message.chat.id, 
                          "🔗 *Add New Visit Task*\n\n"
                          "Please send task details in this format:\n\n"
                          "Title | Amount | Description | Link | Time (seconds) | Limit\n\n"
                          "Example:\n"
                          "Visit Website | 2 | Visit our website | https://example.com | 30 | 500")
    
    bot.register_next_step_handler(msg, process_add_visit_task)

def process_add_visit_task(message):
    if message.chat.id != ADMIN_USER_ID:
        return
    
    try:
        parts = message.text.split('|')
        if len(parts) != 6:
            bot.send_message(message.chat.id, "❌ Invalid format! Please use: Title | Amount | Description | Link | Time | Limit")
            return
        
        task = {
            'title': parts[0].strip(),
            'amount': float(parts[1].strip()),
            'description': parts[2].strip(),
            'link': parts[3].strip(),
            'time_required': int(parts[4].strip()),
            'limit': int(parts[5].strip()),
            'active': True,
            'type': 'visit',
            'created_at': datetime.now()
        }
        
        visit_tasks_collection.insert_one(task)
        bot.send_message(message.chat.id, f"✅ Visit task '{task['title']}' added successfully!")
        
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Error: {str(e)}")

@bot.message_handler(func=lambda message: message.text == '🔙 Back to Menu')
def back_to_menu(message):
    global ADMIN_USER_ID
    if message.chat.id == ADMIN_USER_ID:
        ADMIN_USER_ID = None
        bot.send_message(message.chat.id, "Returning to main menu...", reply_markup=main_keyboard())
    else:
        bot.send_message(message.chat.id, "Returning to main menu...", reply_markup=main_keyboard())

@bot.message_handler(func=lambda message: message.text == '📢 Advertisement')
def advertisement(message):
    total_users = users_collection.count_documents({})
    bot.send_message(message.chat.id, 
                    "📢 *Advertise With Us*\n\n"
                    "Promote your product/service to our growing community!\n\n"
                    "📊 *Statistics:*\n"
                    f"👥 Total Users: {total_users}\n\n"
                    "💰 *Advertisement Rates:*\n"
                    "• Broadcast Message: ₹500\n"
                    "• Pin Message for 24h: ₹1000\n"
                    "• Featured Task: ₹2000\n\n"
                    "📞 Contact: @Admin for bookings and inquiries!",
                    parse_mode='Markdown')

@bot.message_handler(func=lambda message: message.text == 'ℹ️ About')
def about(message):
    msg = """ℹ️ *About This Bot*

💰 *Earning Bot v2.0*

✨ *Features:*
• Complete tasks to earn money
• Visit websites for instant earning
• Referral program (₹2 per referral)
• Multiple withdrawal methods
• 24/7 automated system

📋 *Terms & Conditions:*
1. One account per user
2. Fake submissions = Instant ban
3. Withdrawals processed within 48h
4. Minimum withdrawal: ₹50 (UPI), ₹100 (Bank), ₹200 (Crypto)
5. Support response within 24h

🔗 *Privacy Policy:*
We value your privacy. Your data is secure and never shared with third parties.

📞 *Support:* @Admin
🌐 *Website:* Coming Soon

Thank you for being part of our community! 🚀"""
    
    bot.send_message(message.chat.id, msg, parse_mode='Markdown')

# --- Start Bot ---
if __name__ == '__main__':
    print("🤖 Bot is starting...")
    print("✅ Bot is running!")
    
    # Remove webhook if exists (for polling mode)
    try:
        bot.remove_webhook()
        print("✅ Webhook removed")
    except:
        pass
    
    try:
        bot.infinity_polling(timeout=10, long_polling_timeout=5)
    except Exception as e:
        print(f"❌ Bot Error: {e}")
        time.sleep(5)