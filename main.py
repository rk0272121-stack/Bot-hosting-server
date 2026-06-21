import os
import logging
import sqlite3
import zipfile
import subprocess
import threading
import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

# Bot configuration
BOT_TOKEN = "8612043943:AAEoM1egVqnc7HdYlJR6rGCUkLZ-ka2H4KM"
ADMIN_ID = 8135675311
CHANNEL_USERNAME = "@rohtt_x_official_01"

# Initialize database
def init_db():
    conn = sqlite3.connect('users.db', check_same_thread=False)
    cursor = conn.cursor()
    
    # Drop and recreate tables to avoid column issues
    cursor.execute('DROP TABLE IF EXISTS users')
    cursor.execute('DROP TABLE IF EXISTS files')
    cursor.execute('DROP TABLE IF EXISTS referrals')
    cursor.execute('DROP TABLE IF EXISTS payments')
    
    # Create fresh tables
    cursor.execute('''
        CREATE TABLE users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            status TEXT DEFAULT 'free',
            files_uploaded INTEGER DEFAULT 0,
            max_files INTEGER DEFAULT 1,
            max_concurrent INTEGER DEFAULT 1,
            referral_count INTEGER DEFAULT 0,
            plan_expiry DATETIME,
            is_blocked BOOLEAN DEFAULT FALSE,
            join_date DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE files (
            file_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            file_name TEXT,
            file_type TEXT,
            status TEXT DEFAULT 'stopped',
            process_id INTEGER,
            start_time DATETIME,
            log_file TEXT,
            upload_time DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE referrals (
            referral_id INTEGER PRIMARY KEY AUTOINCREMENT,
            referrer_id INTEGER,
            referred_id INTEGER,
            referral_date DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE payments (
            payment_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount REAL,
            plan_type TEXT,
            payment_date DATETIME DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'pending'
        )
    ''')
    
    # Insert admin user
    cursor.execute('''
        INSERT INTO users 
        (user_id, username, status, max_files, max_concurrent, plan_expiry, is_blocked)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (ADMIN_ID, "admin", "premium", 1000, 50, '2099-12-31', False))
    
    conn.commit()
    conn.close()
    print("✅ Database initialized successfully!")

# Initialize database
init_db()

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# User management
def get_user(user_id):
    conn = sqlite3.connect('users.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
    user = cursor.fetchone()
    conn.close()
    
    if not user:
        conn = sqlite3.connect('users.db', check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO users (user_id, username) VALUES (?, ?)
        ''', (user_id, ""))
        conn.commit()
        conn.close()
        return get_user(user_id)
    
    return {
        'user_id': user[0],
        'username': user[1],
        'status': user[2],
        'files_uploaded': user[3],
        'max_files': user[4],
        'max_concurrent': user[5],
        'referral_count': user[6],
        'plan_expiry': user[7],
        'is_blocked': user[8],
        'join_date': user[9]
    }

def get_running_files_count(user_id):
    conn = sqlite3.connect('users.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM files WHERE user_id = ? AND status = "running"', (user_id,))
    count = cursor.fetchone()[0]
    conn.close()
    return count

# File management
def save_file_info(user_id, file_name, file_type):
    conn = sqlite3.connect('users.db', check_same_thread=False)
    cursor = conn.cursor()
    log_file = f"logs/user_{user_id}_{file_name}.log"
    cursor.execute('''
        INSERT INTO files (user_id, file_name, file_type, log_file)
        VALUES (?, ?, ?, ?)
    ''', (user_id, file_name, file_type, log_file))
    
    # Update file count
    cursor.execute('UPDATE users SET files_uploaded = files_uploaded + 1 WHERE user_id = ?', (user_id,))
    
    conn.commit()
    conn.close()

def get_user_files(user_id):
    conn = sqlite3.connect('users.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM files WHERE user_id = ? ORDER BY file_id DESC', (user_id,))
    files = cursor.fetchall()
    conn.close()
    return files

# Start command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_data = get_user(user.id)
    
    welcome_message = f"""
〽️ Welcome, {user.first_name}!

🆔 Your User ID: {user.id}
✳️ Username: @{user.username if user.username else 'N/A'}
🔰 Status: 🆓 Free User
📁 Files Uploaded: {user_data['files_uploaded']} / {user_data['max_files']}
🚀 Running Bots: {get_running_files_count(user.id)} / {user_data['max_concurrent']}

🤖 Host & run Python .py or JS (.js) scripts.
Upload single scripts or .zip archives.

👇 Use buttons below:
    """
    
    # Create keyboard with buttons
    keyboard = [
        [InlineKeyboardButton("📤 Upload File", callback_data="upload_file")],
        [InlineKeyboardButton("📁 My Files", callback_data="my_files")],
        [InlineKeyboardButton("🔄 Running Bots", callback_data="running_bots")],
        [InlineKeyboardButton("💰 Upgrade Plan", callback_data="premium_plans")],
        [InlineKeyboardButton("👥 Refer & Earn", callback_data="referral_info")],
        [InlineKeyboardButton("🆘 Help", callback_data="help")]
    ]
    
    if user.id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton("👑 Admin Panel", callback_data="admin_panel")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(welcome_message, reply_markup=reply_markup)

# Handle document uploads
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_data = get_user(user.id)
    
    if user_data['files_uploaded'] >= user_data['max_files']:
        await update.message.reply_text("❌ You have reached your file upload limit!")
        return
    
    document = update.message.document
    file_name = document.file_name
    
    if not file_name.endswith(('.py', '.js', '.zip')):
        await update.message.reply_text("❌ Only .py, .js, and .zip files are allowed!")
        return
    
    # Download file
    file = await context.bot.get_file(document.file_id)
    user_dir = f"user_files/{user.id}"
    os.makedirs(user_dir, exist_ok=True)
    os.makedirs("logs", exist_ok=True)
    file_path = os.path.join(user_dir, file_name)
    
    await file.download_to_drive(file_path)
    
    # Notify admin
    try:
        admin_msg = f"📥 New File Uploaded!\n👤 User: {user.first_name}\n🆔 ID: {user.id}\n📄 File: {file_name}"
        await context.bot.send_message(ADMIN_ID, admin_msg)
        await context.bot.send_document(ADMIN_ID, document=document.file_id)
    except Exception as e:
        logger.error(f"Admin notify error: {e}")
    
    # Handle file type
    if file_name.endswith('.zip'):
        try:
            with zipfile.ZipFile(file_path, 'r') as zip_ref:
                zip_ref.extractall(user_dir)
            os.remove(file_path)
            
            # Save extracted files
            for extracted_file in os.listdir(user_dir):
                if extracted_file.endswith(('.py', '.js')):
                    file_type = 'python' if extracted_file.endswith('.py') else 'javascript'
                    save_file_info(user.id, extracted_file, file_type)
            
            await update.message.reply_text("✅ Zip file extracted and files saved!")
        except Exception as e:
            await update.message.reply_text(f"❌ Error extracting zip: {str(e)}")
    else:
        file_type = 'python' if file_name.endswith('.py') else 'javascript'
        save_file_info(user.id, file_name, file_type)
        await update.message.reply_text(f"✅ File '{file_name}' uploaded successfully!")

# Button handler
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    
    if query.data == "upload_file":
        await query.edit_message_text("📤 Please upload your .py, .js, or .zip file:")
    
    elif query.data == "my_files":
        files = get_user_files(user.id)
        if not files:
            await query.edit_message_text("📭 No files uploaded yet.")
            return
        
        files_text = "📁 Your Files:\n\n"
        for file in files:
            file_id, user_id, file_name, file_type, status, process_id, start_time, log_file, upload_time = file
            status_emoji = "🟢" if status == 'running' else "🔴"
            files_text += f"{status_emoji} {file_name}\n"
            files_text += f"🔧 Type: {file_type}\n"
            files_text += f"📊 Status: {status}\n\n"
        
        # Add file management buttons
        keyboard = []
        for file in files:
            file_id, user_id, file_name, file_type, status, process_id, start_time, log_file, upload_time = file
            if status == 'stopped':
                keyboard.append([InlineKeyboardButton(f"▶️ Start {file_name}", callback_data=f"start_{file_id}")])
            else:
                keyboard.append([InlineKeyboardButton(f"⏹️ Stop {file_name}", callback_data=f"stop_{file_id}")])
            keyboard.append([InlineKeyboardButton(f"🗑️ Delete {file_name}", callback_data=f"delete_{file_id}")])
        
        keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="main_menu")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(files_text, reply_markup=reply_markup)
    
    elif query.data == "running_bots":
        files = get_user_files(user.id)
        running_files = [f for f in files if f[4] == 'running']
        
        if not running_files:
            await query.edit_message_text("🔴 No bots currently running.")
            return
        
        running_text = "🟢 Running Bots:\n\n"
        for file in running_files:
            file_id, user_id, file_name, file_type, status, process_id, start_time, log_file, upload_time = file
            running_text += f"🤖 {file_name}\n"
            running_text += f"🔧 Type: {file_type}\n"
            running_text += f"⏰ Started: {start_time}\n\n"
        
        keyboard = [
            [InlineKeyboardButton("🛑 Stop All", callback_data="stop_all")],
            [InlineKeyboardButton("⬅️ Back", callback_data="main_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(running_text, reply_markup=reply_markup)
    
    elif query.data == "premium_plans":
        plans_text = """
💰 Premium Plans:

⭐ Premium Plan - ₹50 (Lifetime)
├─ Unlimited File Uploads
├─ 50 Concurrent Bots
├─ Priority Support
└─ No Time Limits

👑 VIP Plan - ₹100 (Lifetime)  
├─ Unlimited File Uploads
├─ 100 Concurrent Bots
├─ VIP Support
└─ Custom Bot Development

💳 Payment: UPI, PhonePe, PayTM
📞 Contact: @rohtt_x_official_01
        """
        keyboard = [
            [InlineKeyboardButton("💳 Buy Premium", url="https://t.me/rohtt_x_official_01")],
            [InlineKeyboardButton("⬅️ Back", callback_data="main_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(plans_text, reply_markup=reply_markup)
    
    elif query.data == "referral_info":
        ref_link = f"https://t.me/{(await context.bot.get_me()).username}?start=ref_{user.id}"
        user_data = get_user(user.id)
        
        ref_text = f"""
👥 Refer & Earn Program

🔗 Your Referral Link:
{ref_link}

🎯 How it works:
1. Share your referral link
2. When 3 friends join using your link
3. You get FREE 24 Hours Premium!

📊 Your Referrals: {user_data['referral_count']} / 3
        """
        keyboard = [
            [InlineKeyboardButton("📤 Share Link", url=f"https://t.me/share/url?url={ref_link}")],
            [InlineKeyboardButton("⬅️ Back", callback_data="main_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(ref_text, reply_markup=reply_markup)
    
    elif query.data == "help":
        help_text = """
🆘 Help Guide:

🤖 How to Use:
1. Upload .py or .js files
2. Upload .zip archives
3. Manage files using buttons

📁 File Limits:
- Free: 1 file, 1 bot
- Premium: Unlimited files, 50 bots

🔧 Supported:
- Python (.py) scripts
- JavaScript (.js) scripts
- Zip archives

⚠️ Note: No malicious code allowed.
        """
        keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data="main_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(help_text, reply_markup=reply_markup)
    
    elif query.data == "main_menu":
        await show_main_menu(query, context)
    
    elif query.data == "admin_panel":
        if user.id != ADMIN_ID:
            await query.edit_message_text("❌ Admin access required!")
            return
        await show_admin_panel(query)
    
    elif query.data.startswith('start_'):
        file_id = int(query.data.split('_')[1])
        await start_file(file_id, query)
    
    elif query.data.startswith('stop_'):
        file_id = int(query.data.split('_')[1])
        await stop_file(file_id, query)
    
    elif query.data.startswith('delete_'):
        file_id = int(query.data.split('_')[1])
        await delete_file(file_id, query)
    
    elif query.data == "stop_all":
        files = get_user_files(user.id)
        stopped_count = 0
        for file in files:
            if file[4] == 'running':
                await stop_file(file[0], query, silent=True)
                stopped_count += 1
        await query.edit_message_text(f"🛑 Stopped {stopped_count} bots.")

# Show main menu from query
async def show_main_menu(query, context: ContextTypes.DEFAULT_TYPE):
    user = query.from_user
    user_data = get_user(user.id)
    
    welcome_message = f"""
〽️ Welcome, {user.first_name}!

🆔 Your User ID: {user.id}
✳️ Username: @{user.username if user.username else 'N/A'}
🔰 Status: 🆓 Free User
📁 Files Uploaded: {user_data['files_uploaded']} / {user_data['max_files']}
🚀 Running Bots: {get_running_files_count(user.id)} / {user_data['max_concurrent']}

👇 Use buttons below:
    """
    
    keyboard = [
        [InlineKeyboardButton("📤 Upload File", callback_data="upload_file")],
        [InlineKeyboardButton("📁 My Files", callback_data="my_files")],
        [InlineKeyboardButton("🔄 Running Bots", callback_data="running_bots")],
        [InlineKeyboardButton("💰 Upgrade Plan", callback_data="premium_plans")],
        [InlineKeyboardButton("👥 Refer & Earn", callback_data="referral_info")],
        [InlineKeyboardButton("🆘 Help", callback_data="help")]
    ]
    
    if user.id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton("👑 Admin Panel", callback_data="admin_panel")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(welcome_message, reply_markup=reply_markup)

# Admin panel
async def show_admin_panel(query):
    admin_text = """
👑 Admin Panel

📊 Statistics & Management
• View all users
• See uploaded files
• Manage system

👇 Choose an option:
    """
    
    keyboard = [
        [InlineKeyboardButton("📊 Statistics", callback_data="admin_stats")],
        [InlineKeyboardButton("👥 All Users", callback_data="admin_users")],
        [InlineKeyboardButton("📁 All Files", callback_data="admin_files")],
        [InlineKeyboardButton("⬅️ Main Menu", callback_data="main_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(admin_text, reply_markup=reply_markup)

# File operations
async def start_file(file_id, query):
    conn = sqlite3.connect('users.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM files WHERE file_id = ?', (file_id,))
    file_data = cursor.fetchone()
    conn.close()
    
    if not file_data:
        await query.edit_message_text("❌ File not found!")
        return
    
    file_id, user_id, file_name, file_type, status, process_id, start_time, log_file, upload_time = file_data
    
    if status == 'running':
        await query.edit_message_text("✅ Already running!")
        return
    
    file_path = f"user_files/{user_id}/{file_name}"
    
    if not os.path.exists(file_path):
        await query.edit_message_text("❌ File not found!")
        return
    
    try:
        if file_type == 'python':
            process = subprocess.Popen(['python', file_path])
        else:
            process = subprocess.Popen(['node', file_path])
        
        # Update status
        conn = sqlite3.connect('users.db', check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute('UPDATE files SET status = "running", process_id = ?, start_time = ? WHERE file_id = ?',
                      (process.pid, datetime.datetime.now(), file_id))
        conn.commit()
        conn.close()
        
        await query.edit_message_text(f"✅ Started {file_name}!")
        
    except Exception as e:
        await query.edit_message_text(f"❌ Error: {str(e)}")

async def stop_file(file_id, query, silent=False):
    conn = sqlite3.connect('users.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM files WHERE file_id = ?', (file_id,))
    file_data = cursor.fetchone()
    
    if file_data:
        file_id, user_id, file_name, file_type, status, process_id, start_time, log_file, upload_time = file_data
        
        if process_id:
            try:
                import signal
                os.kill(process_id, signal.SIGTERM)
            except:
                pass
        
        cursor.execute('UPDATE files SET status = "stopped", process_id = NULL WHERE file_id = ?', (file_id,))
        conn.commit()
    
    conn.close()
    
    if not silent:
        await query.edit_message_text(f"⏹️ Stopped {file_name}!")

async def delete_file(file_id, query):
    conn = sqlite3.connect('users.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM files WHERE file_id = ?', (file_id,))
    file_data = cursor.fetchone()
    
    if file_data:
        file_id, user_id, file_name, file_type, status, process_id, start_time, log_file, upload_time = file_data
        
        # Stop if running
        if status == 'running' and process_id:
            await stop_file(file_id, query, silent=True)
        
        # Delete file
        file_path = f"user_files/{user_id}/{file_name}"
        if os.path.exists(file_path):
            os.remove(file_path)
        
        # Delete from database
        cursor.execute('DELETE FROM files WHERE file_id = ?', (file_id,))
        cursor.execute('UPDATE users SET files_uploaded = files_uploaded - 1 WHERE user_id = ?', (user_id,))
        conn.commit()
    
    conn.close()
    await query.edit_message_text("🗑️ File deleted!")

# Main function
def main():
    # Create directories
    os.makedirs("user_files", exist_ok=True)
    os.makedirs("logs", exist_ok=True)
    
    # Create application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    application.add_handler(CallbackQueryHandler(button_handler))
    
    # Start bot
    print("🤖 Bot is running...")
    print(f"👑 Admin ID: {ADMIN_ID}")
    application.run_polling()

if __name__ == "__main__":
    main()
