import telebot
import os
import zipfile
import subprocess
import shutil
import time
import threading
import signal
import sys
from datetime import datetime
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
import requests
import json
import tempfile

BOT_TOKEN = "8657903558:AAFjDz8wlX-wgaHHiDvdQgVFmnmUhThbPIE"

# Admin Configuration
ADMIN_ID = 8446135201  # REPLACE WITH YOUR ACTUAL CHAT ID

bot = telebot.TeleBot(BOT_TOKEN)

BASE_DIR = "projects"
os.makedirs(BASE_DIR, exist_ok=True)

# Store running processes per user
running_projects = {}  # {user_id: {project_name: {"process": process, "cwd": path}}}
project_errors = {}  # {user_id: {project_name: error}}

# Admin statistics
admin_stats = {
    "total_users": 0,
    "total_projects": 0,
    "total_running": 0,
    "bot_start_time": datetime.now()
}

# Store environment variables
project_env_vars = {}

# ============== SIMPLE UI ==============

def get_main_keyboard(user_id):
    """Get main keyboard based on user role"""
    is_admin = (user_id == ADMIN_ID)
    
    if is_admin:
        markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        buttons = [
            "📦 Upload Project", "🐙 GitHub Deploy",
            "📁 My Projects", "▶️ Start", "⏹️ Stop",
            "🔄 Restart", "🗑️ Delete", "🗑️ Delete All",
            "📊 Stats", "🔄 Refresh", "📝 Errors",
            "⚙️ Env Vars", "👑 Admin Panel", "❓ Help"
        ]
        markup.add(*buttons)
        return markup
    else:
        markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        buttons = [
            "📦 Upload", "🐙 GitHub Deploy", "📁 Projects",
            "▶️ Start", "⏹️ Stop", "🔄 Restart", "🗑️ Delete",
            "🗑️ Delete All", "📊 Stats", "🔄 Refresh",
            "📝 Errors", "⚙️ Env Vars", "❓ Help"
        ]
        markup.add(*buttons)
        return markup

def get_admin_keyboard():
    """Admin panel inline keyboard"""
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("👥 All Users", callback_data="admin_users"),
        InlineKeyboardButton("📦 All Projects", callback_data="admin_projects"),
        InlineKeyboardButton("🟢 Running Projects", callback_data="admin_running"),
        InlineKeyboardButton("⚠️ Error Logs", callback_data="admin_errors"),
        InlineKeyboardButton("💾 Server Stats", callback_data="admin_server"),
        InlineKeyboardButton("📊 Bot Stats", callback_data="admin_botstats"),
        InlineKeyboardButton("🗑️ Clean Orphaned", callback_data="admin_clean"),
        InlineKeyboardButton("🔄 Broadcast", callback_data="admin_broadcast"),
        InlineKeyboardButton("❌ Close", callback_data="admin_close")
    )
    return markup

# ============== GITHUB INTEGRATION (No git module required) ==============

@bot.message_handler(func=lambda m: m.text == "🐙 GitHub Deploy")
def github_deploy_menu(msg):
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("📦 Deploy from GitHub URL", callback_data="github_url"),
        InlineKeyboardButton("📋 My GitHub Projects", callback_data="github_my")
    )
    bot.send_message(msg.chat.id, "🐙 *GitHub Deployment*\n\nChoose deployment method:", 
                     parse_mode="Markdown", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "github_url")
def handle_github_url(call):
    bot.edit_message_text("🔗 *Enter GitHub Repository URL*\n\nExample: `https://github.com/username/repo`\n\nOr /cancel to cancel",
                        call.message.chat.id, call.message.message_id, parse_mode="Markdown")
    bot.register_next_step_handler(call.message, process_github_download)

@bot.callback_query_handler(func=lambda call: call.data == "github_my")
def show_github_projects(call):
    user_id = call.message.chat.id
    user_dir = get_user_dir(user_id)
    github_projects = []
    
    for project in os.listdir(user_dir):
        if project.startswith("github_"):
            github_projects.append(project)
    
    if not github_projects:
        bot.edit_message_text("📭 *No GitHub projects found*\n\nUse '🐙 GitHub Deploy' to add one.",
                            call.message.chat.id, call.message.message_id, parse_mode="Markdown")
        return
    
    text = "🐙 *YOUR GITHUB PROJECTS*\n━━━━━━━━━━━━━━━━━━━━━━\n"
    for proj in github_projects:
        is_running = proj in get_user_running_projects(user_id)
        status = "🟢 Running" if is_running else "⚪ Stopped"
        text += f"\n📁 `{proj}`\n└─ Status: {status}\n"
    
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode="Markdown")

def process_github_download(msg):
    if msg.text == "/cancel":
        bot.send_message(msg.chat.id, "❌ Cancelled.", reply_markup=get_main_keyboard(msg.chat.id))
        return
    
    url = msg.text.strip()
    
    # Extract username and repo name
    if "github.com" in url:
        parts = url.replace("https://", "").replace("http://", "").replace("github.com/", "").split('/')
        if len(parts) >= 2:
            username = parts[0]
            repo = parts[1].replace(".git", "")
            download_url = f"https://github.com/{username}/{repo}/archive/refs/heads/main.zip"
            
            status_msg = bot.send_message(msg.chat.id, f"⏳ *Downloading {repo} from GitHub...*", parse_mode="Markdown")
            
            try:
                # Download the zip file
                response = requests.get(download_url)
                
                if response.status_code == 404:
                    # Try master branch instead
                    download_url = f"https://github.com/{username}/{repo}/archive/refs/heads/master.zip"
                    response = requests.get(download_url)
                
                if response.status_code == 200:
                    # Save and extract
                    user_id = msg.chat.id
                    project_name = f"github_{repo}_{int(time.time())}"
                    user_dir = get_user_dir(user_id)
                    zip_path = os.path.join(user_dir, f"{project_name}.zip")
                    
                    with open(zip_path, 'wb') as f:
                        f.write(response.content)
                    
                    # Extract
                    extract_path = os.path.join(user_dir, project_name)
                    os.makedirs(extract_path, exist_ok=True)
                    
                    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                        zip_ref.extractall(extract_path)
                    
                    # Handle nested folder structure
                    extracted_items = os.listdir(extract_path)
                    if len(extracted_items) == 1 and os.path.isdir(os.path.join(extract_path, extracted_items[0])):
                        subfolder = os.path.join(extract_path, extracted_items[0])
                        for item in os.listdir(subfolder):
                            shutil.move(os.path.join(subfolder, item), extract_path)
                        os.rmdir(subfolder)
                    
                    os.remove(zip_path)
                    
                    bot.edit_message_text("📦 *Extracted!*", msg.chat.id, status_msg.message_id, parse_mode="Markdown")
                    
                    # Install requirements
                    req_file = os.path.join(extract_path, "requirements.txt")
                    if os.path.exists(req_file):
                        subprocess.run(["pip", "install", "-r", req_file], cwd=extract_path, capture_output=True)
                        bot.edit_message_text("📥 *Requirements Installed*", msg.chat.id, status_msg.message_id, parse_mode="Markdown")
                    
                    size = get_folder_size(extract_path)
                    
                    # Check for main.py
                    main_file = os.path.join(extract_path, "main.py")
                    if not os.path.exists(main_file):
                        # Look for any .py file
                        py_files = [f for f in os.listdir(extract_path) if f.endswith('.py')]
                        if py_files:
                            shutil.move(os.path.join(extract_path, py_files[0]), main_file)
                    
                    markup = InlineKeyboardMarkup()
                    markup.add(
                        InlineKeyboardButton("▶️ Start Now", callback_data=f"start_{project_name}"),
                        InlineKeyboardButton("⚙️ Set Env Vars", callback_data=f"env_{project_name}")
                    )
                    
                    bot.edit_message_text(f"✅ *GitHub Project Deployed!*\n\n"
                                     f"📁 Name: `{project_name}`\n"
                                     f"📦 Size: {size}\n"
                                     f"📄 main.py: {'✅' if os.path.exists(main_file) else '❌'}\n"
                                     f"🐙 Repo: {url}\n\n"
                                     f"Click below to start:",
                                     msg.chat.id, status_msg.message_id, parse_mode="Markdown", reply_markup=markup)
                else:
                    bot.edit_message_text(f"❌ *Failed to download repository*\nStatus: {response.status_code}\nMake sure the repository exists and is public.", 
                                        msg.chat.id, status_msg.message_id, parse_mode="Markdown")
            except Exception as e:
                bot.edit_message_text(f"❌ *Error:* `{str(e)[:200]}`", msg.chat.id, status_msg.message_id, parse_mode="Markdown")
        else:
            bot.send_message(msg.chat.id, "❌ *Invalid GitHub URL*", parse_mode="Markdown")
    else:
        bot.send_message(msg.chat.id, "❌ *Please provide a valid GitHub URL*", parse_mode="Markdown")

# ============== ENVIRONMENT VARIABLES ==============

@bot.message_handler(func=lambda m: m.text == "⚙️ Env Vars")
def env_vars_menu(msg):
    user_id = msg.chat.id
    projects = get_user_projects(user_id)
    
    if not projects:
        bot.send_message(msg.chat.id, "📂 *No projects found*", parse_mode="Markdown")
        return
    
    markup = InlineKeyboardMarkup(row_width=2)
    for project in projects:
        markup.add(InlineKeyboardButton(f"⚙️ {project}", callback_data=f"env_{project}"))
    
    bot.send_message(msg.chat.id, "⚙️ *Select project to configure environment variables:*",
                     parse_mode="Markdown", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("env_"))
def handle_env_vars(call):
    user_id = call.message.chat.id
    project_name = call.data.replace("env_", "")
    
    # Get current env vars
    if user_id not in project_env_vars:
        project_env_vars[user_id] = {}
    if project_name not in project_env_vars[user_id]:
        project_env_vars[user_id][project_name] = {}
    
    current_vars = project_env_vars[user_id][project_name]
    
    text = f"⚙️ *Environment Variables for `{project_name}`*\n\n"
    if current_vars:
        text += "📋 *Current Variables:*\n"
        for key, value in current_vars.items():
            text += f"├─ {key} = `{value[:20]}`\n"
    else:
        text += "📭 *No environment variables set*\n"
    
    text += "\n🔧 *Actions:*"
    
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("➕ Add Variable", callback_data=f"env_add_{project_name}"),
        InlineKeyboardButton("🗑️ Remove Variable", callback_data=f"env_remove_{project_name}"),
        InlineKeyboardButton("📋 List All", callback_data=f"env_list_{project_name}"),
        InlineKeyboardButton("🔙 Back", callback_data="env_back")
    )
    
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                         parse_mode="Markdown", reply_markup=markup)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("env_add_"))
def env_add_var(call):
    project_name = call.data.replace("env_add_", "")
    bot.edit_message_text(f"📝 *Add Environment Variable for `{project_name}`*\n\n"
                         f"Send in format: `KEY=value`\n\nExample: `PORT=8080`\n`DATABASE_URL=postgresql://localhost/db`\n\nType /cancel to cancel",
                         call.message.chat.id, call.message.message_id, parse_mode="Markdown")
    bot.register_next_step_handler(call.message, process_env_add, project_name)

def process_env_add(msg, project_name):
    if msg.text == "/cancel":
        bot.send_message(msg.chat.id, "❌ Cancelled.", reply_markup=get_main_keyboard(msg.chat.id))
        return
    
    user_id = msg.chat.id
    
    try:
        key, value = msg.text.split('=', 1)
        key = key.strip().upper()
        value = value.strip()
        
        if user_id not in project_env_vars:
            project_env_vars[user_id] = {}
        if project_name not in project_env_vars[user_id]:
            project_env_vars[user_id][project_name] = {}
        
        project_env_vars[user_id][project_name][key] = value
        
        # Save to .env file in project directory
        project_path = os.path.join(get_user_dir(user_id), project_name)
        env_file = os.path.join(project_path, ".env")
        
        with open(env_file, 'w') as f:
            for k, v in project_env_vars[user_id][project_name].items():
                f.write(f"{k}={v}\n")
        
        bot.send_message(msg.chat.id, f"✅ *Variable added:* `{key}={value[:30]}`",
                        parse_mode="Markdown")
        
        # Restart project if running to apply new env vars
        if project_name in get_user_running_projects(user_id):
            stop_project(user_id, project_name)
            time.sleep(1)
            start_project(user_id, project_name)
            bot.send_message(msg.chat.id, f"🔄 *Project '{project_name}' restarted to apply changes*",
                           parse_mode="Markdown")
        
    except ValueError:
        bot.send_message(msg.chat.id, "❌ *Invalid format!* Use `KEY=value`", parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("env_remove_"))
def env_remove_var(call):
    project_name = call.data.replace("env_remove_", "")
    user_id = call.message.chat.id
    
    if user_id in project_env_vars and project_name in project_env_vars[user_id]:
        vars_list = project_env_vars[user_id][project_name]
        
        if not vars_list:
            bot.answer_callback_query(call.id, "No variables to remove")
            return
        
        markup = InlineKeyboardMarkup(row_width=1)
        for key in vars_list.keys():
            markup.add(InlineKeyboardButton(f"🗑️ {key}", callback_data=f"env_del_{project_name}_{key}"))
        markup.add(InlineKeyboardButton("🔙 Back", callback_data=f"env_{project_name}"))
        
        bot.edit_message_text(f"🗑️ *Select variable to remove from `{project_name}`:*",
                            call.message.chat.id, call.message.message_id,
                            parse_mode="Markdown", reply_markup=markup)
    
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("env_del_"))
def env_delete_var(call):
    parts = call.data.replace("env_del_", "").split('_', 1)
    project_name = parts[0]
    key = parts[1] if len(parts) > 1 else ""
    
    user_id = call.message.chat.id
    
    if user_id in project_env_vars and project_name in project_env_vars[user_id]:
        if key in project_env_vars[user_id][project_name]:
            del project_env_vars[user_id][project_name][key]
            
            # Update .env file
            project_path = os.path.join(get_user_dir(user_id), project_name)
            env_file = os.path.join(project_path, ".env")
            
            if os.path.exists(env_file):
                with open(env_file, 'w') as f:
                    for k, v in project_env_vars[user_id][project_name].items():
                        f.write(f"{k}={v}\n")
            
            bot.answer_callback_query(call.id, f"Removed {key}")
            bot.edit_message_text(f"✅ *Removed variable:* `{key}`",
                                call.message.chat.id, call.message.message_id,
                                parse_mode="Markdown")
            
            # Restart project if running
            if project_name in get_user_running_projects(user_id):
                stop_project(user_id, project_name)
                time.sleep(1)
                start_project(user_id, project_name)

@bot.callback_query_handler(func=lambda call: call.data.startswith("env_list_"))
def env_list_vars(call):
    project_name = call.data.replace("env_list_", "")
    user_id = call.message.chat.id
    
    if user_id in project_env_vars and project_name in project_env_vars[user_id]:
        vars_dict = project_env_vars[user_id][project_name]
        
        if vars_dict:
            text = f"📋 *Environment Variables for `{project_name}`*\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
            for key, value in vars_dict.items():
                text += f"🔑 **{key}**\n└─ `{value[:50]}`\n\n"
            
            if len(text) > 4000:
                text = text[:4000] + "\n... (truncated)"
            
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("🔙 Back", callback_data=f"env_{project_name}"))
            
            bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                                parse_mode="Markdown", reply_markup=markup)
        else:
            bot.answer_callback_query(call.id, "No variables set")

@bot.callback_query_handler(func=lambda call: call.data == "env_back")
def env_back(call):
    env_vars_menu(call.message)

# ============== START COMMAND ==============

@bot.message_handler(commands=['start'])
def start(msg):
    user_id = msg.chat.id
    is_admin = (user_id == ADMIN_ID)
    total_projects = len(get_user_projects(user_id))
    
    welcome_text = f"""
🔥 *WELCOME TO PYTHON HOSTING*

✨ *Hex Python Hosting Panel v2.0*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ User-Specific Workspaces
✅ No File Conflicts Between Users
✅ Upload & Deploy Python Projects
✅ **NEW: GitHub Repository Deploy**
✅ **NEW: Environment Variables**
✅ Auto-Install Requirements
✅ 24/7 Project Hosting

👤 *Your Workspace:*
├─ User ID: `{user_id}`
├─ Projects: {total_projects}
├─ Running: {len(get_user_running_projects(user_id))}
└─ Role: {'👑 ADMIN' if is_admin else '👤 USER'}

💡 *Need Help?* @Hexh4ckerOFC

💻 *Powered by @Hexh4ckerOFC*
    """
    
    bot.send_message(msg.chat.id, welcome_text, parse_mode="Markdown", reply_markup=get_main_keyboard(user_id))
    
    if is_admin:
        update_admin_stats()
        uptime = datetime.now() - admin_stats["bot_start_time"]
        hours, remainder = divmod(int(uptime.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)
        
        admin_notice = f"""
👑 *ADMIN PANEL LOADED*

📊 *Quick Stats:*
├─ Total Users: {admin_stats['total_users']}
├─ Total Projects: {admin_stats['total_projects']}
├─ Running: {admin_stats['total_running']}
└─ Uptime: {hours}h {minutes}m

Use the *Admin Panel* button for full control.

💻 *Powered by @Hexh4ckerOFC*
        """
        bot.send_message(msg.chat.id, admin_notice, parse_mode="Markdown")

# ============== ADMIN PANEL HANDLER ==============

@bot.message_handler(func=lambda m: m.text == "👑 Admin Panel")
def admin_panel(msg):
    if msg.chat.id != ADMIN_ID:
        bot.send_message(msg.chat.id, "⛔ *Access Denied!* You are not authorized to use the Admin Panel.", parse_mode="Markdown")
        return
    
    update_admin_stats()
    uptime = datetime.now() - admin_stats["bot_start_time"]
    hours, remainder = divmod(int(uptime.total_seconds()), 3600)
    minutes, seconds = divmod(remainder, 60)
    
    admin_text = f"""
👑 *ADMIN CONTROL PANEL*

📈 *SYSTEM STATISTICS*
├─ 👥 Total Users: {admin_stats['total_users']}
├─ 📦 Total Projects: {admin_stats['total_projects']}
├─ 🟢 Running: {admin_stats['total_running']}
├─ ⚪ Stopped: {admin_stats['total_projects'] - admin_stats['total_running']}
└─ ⏱️ Uptime: {hours}h {minutes}m

🎛️ *CONTROLS*
└─ Use the buttons below to manage the system

💡 *Admin Actions:*
• View all users and their projects
• Stop any running project
• Broadcast messages to all users
• Clean orphaned processes
• View server statistics

💻 *Powered by @Hexh4ckerOFC*
    """
    
    bot.send_message(msg.chat.id, admin_text, parse_mode="Markdown", reply_markup=get_admin_keyboard())

# ============== ADMIN CALLBACK HANDLERS ==============

@bot.callback_query_handler(func=lambda call: call.data.startswith("admin_"))
def handle_admin_callbacks(call):
    if call.message.chat.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "⛔ Admin access only!")
        return
    
    action = call.data.replace("admin_", "")
    
    if action == "users":
        users = get_all_users()
        if not users:
            bot.edit_message_text("📭 *No users found*", call.message.chat.id, call.message.message_id, parse_mode="Markdown")
        else:
            user_list = f"👥 *ALL USERS LIST*\n━━━━━━━━━━━━━━━━━━━━━━\n"
            for uid in users:
                project_count = get_user_project_count(uid)
                running_count = len(running_projects.get(uid, {}))
                user_list += f"\n👤 `{uid}`\n├─ 📦 {project_count} projects\n└─ 🟢 {running_count} running\n"
            user_list += f"\n💻 *Powered by @Hexh4ckerOFC*"
            
            if len(user_list) > 4000:
                user_list = user_list[:4000] + "\n... (truncated)"
            
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_back"))
            bot.edit_message_text(user_list, call.message.chat.id, call.message.message_id, parse_mode="Markdown", reply_markup=markup)
    
    elif action == "projects":
        update_admin_stats()
        text = f"📦 *ALL PROJECTS SUMMARY*\n━━━━━━━━━━━━━━━━━━━━━━\n"
        text += f"├─ Total Projects: {admin_stats['total_projects']}\n"
        text += f"├─ Running: {admin_stats['total_running']} 🟢\n"
        text += f"└─ Stopped: {admin_stats['total_projects'] - admin_stats['total_running']} ⚪\n"
        
        users = get_all_users()
        for uid in users[:10]:
            user_projects = get_user_projects(uid)
            if user_projects:
                text += f"\n👤 User `{uid}`:\n"
                for proj in user_projects[:5]:
                    is_running = proj in running_projects.get(uid, {})
                    icon = "🟢" if is_running else "⚪"
                    text += f"  {icon} {proj}\n"
                if len(user_projects) > 5:
                    text += f"  ... and {len(user_projects)-5} more\n"
        
        text += f"\n💻 *Powered by @Hexh4ckerOFC*"
        
        if len(text) > 4000:
            text = text[:4000] + "\n... (truncated)"
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_back"))
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode="Markdown", reply_markup=markup)
    
    elif action == "running":
        running_list = f"🟢 *RUNNING PROJECTS*\n━━━━━━━━━━━━━━━━━━━━━━\n"
        has_running = False
        for user_id, user_projects in running_projects.items():
            if user_projects:
                has_running = True
                running_list += f"\n👤 User `{user_id}`:\n"
                for proj in user_projects.keys():
                    running_list += f"  🟢 {proj}\n"
        
        if not has_running:
            running_list += "\n📭 *No projects currently running*"
        
        running_list += f"\n💻 *Powered by @Hexh4ckerOFC*"
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_back"))
        bot.edit_message_text(running_list, call.message.chat.id, call.message.message_id, parse_mode="Markdown", reply_markup=markup)
    
    elif action == "errors":
        error_list = f"⚠️ *ERROR LOGS*\n━━━━━━━━━━━━━━━━━━━━━━\n"
        has_errors = False
        for user_id, errors in project_errors.items():
            if errors:
                has_errors = True
                error_list += f"\n👤 User `{user_id}`:\n"
                for proj, err in errors.items():
                    error_list += f"  📁 {proj}: {err[:50]}...\n"
        
        if not has_errors:
            error_list += "\n✅ *No errors logged*"
        
        error_list += f"\n💻 *Powered by @Hexh4ckerOFC*"
        
        if len(error_list) > 4000:
            error_list = error_list[:4000] + "\n... (truncated)"
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_back"))
        bot.edit_message_text(error_list, call.message.chat.id, call.message.message_id, parse_mode="Markdown", reply_markup=markup)
    
    elif action == "server":
        try:
            disk_usage = shutil.disk_usage("/")
            disk_total = disk_usage.total // (1024**3)
            disk_used = disk_usage.used // (1024**3)
            disk_free = disk_usage.free // (1024**3)
            disk_percent = (disk_usage.used / disk_usage.total) * 100
            
            server_text = f"""
💾 *SERVER STATISTICS*
━━━━━━━━━━━━━━━━━━━━━━

💿 *DISK USAGE*
├─ Total: {disk_total} GB
├─ Used: {disk_used} GB ({disk_percent:.1f}%)
├─ Free: {disk_free} GB
└─ Status: {'⚠️ Low Space' if disk_free < 5 else '✅ Healthy'}

📂 *STORAGE PATHS*
├─ Base Dir: {BASE_DIR}
└─ Projects Path: {os.path.abspath(BASE_DIR)}

💻 *Powered by @Hexh4ckerOFC*
            """
        except:
            server_text = f"💾 *SERVER STATISTICS*\n━━━━━━━━━━━━━━━━━━━━━━\n❌ Unable to fetch disk statistics\n\n💻 *Powered by @Hexh4ckerOFC*"
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_back"))
        bot.edit_message_text(server_text, call.message.chat.id, call.message.message_id, parse_mode="Markdown", reply_markup=markup)
    
    elif action == "botstats":
        update_admin_stats()
        uptime = datetime.now() - admin_stats["bot_start_time"]
        hours, remainder = divmod(int(uptime.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)
        
        stats_text = f"""
📊 *BOT STATISTICS*
━━━━━━━━━━━━━━━━━━━━━━

📈 *USAGE STATS*
├─ 👥 Total Users: {admin_stats['total_users']}
├─ 📦 Total Projects: {admin_stats['total_projects']}
├─ 🟢 Running Projects: {admin_stats['total_running']}
└─ 📁 Projects/User: {admin_stats['total_projects']/max(admin_stats['total_users'],1):.1f}

⏱️ *BOT INFO*
├─ Uptime: {hours}h {minutes}m {seconds}s
├─ Started: {admin_stats['bot_start_time'].strftime('%Y-%m-%d %H:%M:%S')}
└─ Admin ID: `{ADMIN_ID}`

🔧 *SYSTEM*
├─ Python: {sys.version.split()[0]}
└─ Platform: {sys.platform}

💻 *Powered by @Hexh4ckerOFC*
        """
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_back"))
        bot.edit_message_text(stats_text, call.message.chat.id, call.message.message_id, parse_mode="Markdown", reply_markup=markup)
    
    elif action == "clean":
        cleaned = clean_orphaned_processes()
        bot.edit_message_text(f"✅ *Cleanup Complete*\n\n🗑️ Removed {cleaned} orphaned process entries", 
                            call.message.chat.id, call.message.message_id, parse_mode="Markdown")
        time.sleep(2)
        admin_panel(call.message)
    
    elif action == "broadcast":
        bot.edit_message_text("📢 *Broadcast Mode*\n\nSend the message you want to broadcast to all users.\n\nType /cancel to cancel.", 
                            call.message.chat.id, call.message.message_id, parse_mode="Markdown")
        bot.register_next_step_handler(call.message, process_broadcast)
    
    elif action == "back":
        admin_panel(call.message)
    
    elif action == "close":
        bot.delete_message(call.message.chat.id, call.message.message_id)
    
    bot.answer_callback_query(call.id)

def process_broadcast(msg):
    if msg.text == "/cancel":
        bot.send_message(msg.chat.id, "❌ Broadcast cancelled.", reply_markup=get_main_keyboard(msg.chat.id))
        return
    
    status_msg = bot.send_message(msg.chat.id, "📡 *Broadcasting message...*", parse_mode="Markdown")
    
    success, failed = broadcast_message(msg.text)
    
    bot.edit_message_text(f"✅ *Broadcast Complete*\n\n📨 Sent: {success}\n❌ Failed: {failed}", 
                        msg.chat.id, status_msg.message_id, parse_mode="Markdown")
    
    bot.send_message(msg.chat.id, "👑 Admin Panel", reply_markup=get_admin_keyboard())

# ============== USER COMMANDS ==============

@bot.message_handler(func=lambda m: m.text == "📦 Upload" or m.text == "📦 Upload Project")
def upload_btn(msg):
    bot.send_message(msg.chat.id, "📦 *Send your .zip file with:*\n• `main.py`\n• `requirements.txt`\n\n📝 *Max size: 50MB*\n🔒 *Your files are private to you*\n\n🐙 *Or use GitHub Deploy for repositories!*", parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "📁 Projects" or m.text == "📁 My Projects")
def file_manager(msg):
    user_id = msg.chat.id
    projects = get_user_projects(user_id)
    user_running = get_user_running_projects(user_id)
    
    if not projects:
        bot.send_message(msg.chat.id, "📂 *No projects found*\nUse 📦 Upload or 🐙 GitHub Deploy to add one.", parse_mode="Markdown")
        return
    
    project_list = f"📁 *YOUR PROJECTS*\n━━━━━━━━━━━━━━━━━━━━━━\n"
    for project in projects:
        is_running = project in user_running
        has_main = os.path.exists(os.path.join(get_user_dir(user_id), project, "main.py"))
        user_errors = get_user_errors(user_id)
        has_error = project in user_errors
        is_github = project.startswith("github_")
        
        status_icon = "🟢" if is_running else "⚪"
        main_icon = "✅" if has_main else "❌"
        error_icon = "⚠️" if has_error else "✓"
        github_icon = "🐙" if is_github else "📦"
        
        project_list += f"\n{status_icon} `{project}`\n   ├─ Type: {github_icon}\n   ├─ main.py: {main_icon}\n   └─ Status: {error_icon}\n"
    
    project_list += f"\n💻 *Powered by @Hexh4ckerOFC*"
    bot.send_message(msg.chat.id, project_list, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "▶️ Start")
def start_project_menu(msg):
    user_id = msg.chat.id
    projects = get_user_projects(user_id)
    user_running = get_user_running_projects(user_id)
    
    if not projects:
        bot.send_message(msg.chat.id, "❌ *No projects found to start*", parse_mode="Markdown")
        return
    
    markup = InlineKeyboardMarkup(row_width=2)
    for project in projects:
        if project not in user_running:
            markup.add(InlineKeyboardButton(f"▶️ {project}", callback_data=f"start_{project}"))
    
    if not markup.keyboard:
        bot.send_message(msg.chat.id, f"✅ *All your projects are already running!*", parse_mode="Markdown")
    else:
        bot.send_message(msg.chat.id, "🚀 *Select project to start:*", parse_mode="Markdown", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "⏹️ Stop")
def stop_project_menu(msg):
    user_id = msg.chat.id
    user_running = get_user_running_projects(user_id)
    
    if not user_running:
        bot.send_message(msg.chat.id, "⚪ *No projects are currently running*", parse_mode="Markdown")
        return
    
    markup = InlineKeyboardMarkup(row_width=2)
    for project in user_running.keys():
        markup.add(InlineKeyboardButton(f"⏹️ {project}", callback_data=f"stop_{project}"))
    
    bot.send_message(msg.chat.id, "🛑 *Select project to stop:*", parse_mode="Markdown", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "🔄 Restart")
def restart_project_menu(msg):
    user_id = msg.chat.id
    projects = get_user_projects(user_id)
    
    if not projects:
        bot.send_message(msg.chat.id, "❌ *No projects found to restart*", parse_mode="Markdown")
        return
    
    markup = InlineKeyboardMarkup(row_width=2)
    for project in projects:
        markup.add(InlineKeyboardButton(f"🔄 {project}", callback_data=f"restart_{project}"))
    
    bot.send_message(msg.chat.id, "🔄 *Select project to restart:*", parse_mode="Markdown", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "🗑️ Delete")
def delete_project_menu(msg):
    user_id = msg.chat.id
    projects = get_user_projects(user_id)
    
    if not projects:
        bot.send_message(msg.chat.id, "📂 *No projects to delete*", parse_mode="Markdown")
        return
    
    markup = InlineKeyboardMarkup(row_width=2)
    for project in projects:
        markup.add(InlineKeyboardButton(f"🗑️ {project}", callback_data=f"delete_{project}"))
    markup.add(InlineKeyboardButton("❌ Cancel", callback_data="cancel_delete"))
    
    bot.send_message(msg.chat.id, "⚠️ *Select project to delete (IRREVERSIBLE):*", 
                     parse_mode="Markdown", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "🗑️ Delete All")
def delete_all(msg):
    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("✅ YES, Delete All", callback_data="confirm_delete_all"),
        InlineKeyboardButton("❌ NO, Cancel", callback_data="cancel_delete")
    )
    bot.send_message(msg.chat.id, "⚠️ *WARNING: This will delete ALL your projects!*\nAre you sure?", 
                     parse_mode="Markdown", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "📊 Stats")
def server_info(msg):
    user_id = msg.chat.id
    total_projects = len(get_user_projects(user_id))
    running_count = len(get_user_running_projects(user_id))
    user_errors = get_user_errors(user_id)
    error_count = len(user_errors)
    github_count = len([p for p in get_user_projects(user_id) if p.startswith("github_")])
    
    try:
        user_dir = get_user_dir(user_id)
        disk_usage = shutil.disk_usage(user_dir)
        disk_total = disk_usage.total // (1024**3)
        disk_used = disk_usage.used // (1024**3)
        disk_percent = (disk_usage.used / disk_usage.total) * 100
        disk_text = f"{disk_used}GB / {disk_total}GB ({disk_percent:.1f}%)"
    except:
        disk_text = "N/A"
    
    stats_text = f"""
📊 *YOUR STATISTICS*
━━━━━━━━━━━━━━━━━━━━━━

📦 *PROJECTS*
├─ Total: {total_projects}
├─ Running: {running_count} 🟢
├─ Stopped: {total_projects - running_count} ⚪
├─ GitHub: {github_count} 🐙
└─ Errors: {error_count} ⚠️

💾 *STORAGE*
└─ Disk Usage: {disk_text}

👤 *USER INFO*
├─ User ID: `{user_id}`
└─ Workspace: Private

🕐 *Server Time*
└─ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

💻 *Powered by @Hexh4ckerOFC*
    """
    bot.send_message(msg.chat.id, stats_text, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "🔄 Refresh")
def refresh_status(msg):
    user_id = msg.chat.id
    user_running = get_user_running_projects(user_id)
    dead_projects = []
    
    for project, info in list(user_running.items()):
        process = info["process"] if isinstance(info, dict) else info
        if process.poll() is not None:
            dead_projects.append(project)
            del user_running[project]
            user_errors = get_user_errors(user_id)
            user_errors[project] = f"⚠️ Process died at {datetime.now().strftime('%H:%M:%S')}"
    
    if dead_projects:
        bot.send_message(msg.chat.id, f"⚠️ *Detected dead processes:*\n{', '.join(dead_projects)}\n\nUse ▶️ Start to restart them.", 
                         parse_mode="Markdown")
    else:
        running_count = len(user_running)
        bot.send_message(msg.chat.id, f"✅ *Status Refreshed* | 🟢 Running: {running_count}", 
                         parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "📝 Errors")
def view_errors(msg):
    user_id = msg.chat.id
    user_errors = get_user_errors(user_id)
    
    if not user_errors:
        bot.send_message(msg.chat.id, "✅ *No errors logged! All projects running smoothly.*", parse_mode="Markdown")
        return
    
    error_text = f"⚠️ *ERROR LOG*\n━━━━━━━━━━━━━━━━━━━━━━\n"
    for project, error in user_errors.items():
        error_text += f"\n📁 `{project}`\n└─ {error[:100]}\n"
    
    if len(error_text) > 4000:
        error_text = error_text[:4000] + "\n... (truncated)"
    
    error_text += f"\n💻 *Powered by @Hexh4ckerOFC*"
    bot.send_message(msg.chat.id, error_text, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "❓ Help")
def help_command(msg):
    help_text = f"""
📚 *HELP MENU v2.0*
━━━━━━━━━━━━━━━━━━━━━━

🎯 *PROJECT MANAGEMENT*
📦 Upload - Deploy new .zip project
🐙 GitHub Deploy - Clone from GitHub
📁 Projects - View all projects with status
▶️ Start - Run a specific project
⏹️ Stop - Stop running project
🔄 Restart - Restart a project
🗑️ Delete - Delete specific project
🗑️ Delete All - Delete ALL your projects

⚙️ *ENVIRONMENT VARIABLES*
⚙️ Env Vars - Set environment variables for projects
• KEY=value format
• Saved to .env file
• Auto-applied on restart

📊 *MONITORING*
📊 Stats - Your storage & project counts
🔄 Refresh - Check if projects are running
📝 Errors - See error logs

🔒 *PRIVACY*
• Each user has their own private workspace
• No file conflicts between users
• Your projects are completely isolated

💡 *GITHUB DEPLOYMENT*
• Supports public repositories
• Downloads as ZIP (no git required)
• Auto-installs requirements.txt
• Auto-detects main.py

🆘 *SUPPORT*
Contact: @Hexh4ckerOFC

🟢 *Bot Status: ONLINE & FULLY WORKING*

💻 *Powered by @Hexh4ckerOFC*
    """
    bot.send_message(msg.chat.id, help_text, parse_mode="Markdown")

# ============== CALLBACK HANDLERS ==============

@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    user_id = call.message.chat.id
    
    # Handle Start Project
    if call.data.startswith("start_"):
        project_name = call.data.replace("start_", "")
        result = start_project(user_id, project_name)
        if result:
            bot.edit_message_text(f"✅ *'{project_name}' started successfully!*", 
                                call.message.chat.id, call.message.message_id, parse_mode="Markdown")
        else:
            bot.edit_message_text(f"❌ *Failed to start '{project_name}'*\nCheck if main.py exists!", 
                                call.message.chat.id, call.message.message_id, parse_mode="Markdown")
    
    # Handle Stop Project
    elif call.data.startswith("stop_"):
        project_name = call.data.replace("stop_", "")
        if project_name in get_user_running_projects(user_id):
            stop_project(user_id, project_name)
            bot.edit_message_text(f"⏹️ *'{project_name}' stopped successfully*", 
                                call.message.chat.id, call.message.message_id, parse_mode="Markdown")
        else:
            bot.edit_message_text(f"⚠️ *'{project_name}' is not running*", 
                                call.message.chat.id, call.message.message_id, parse_mode="Markdown")
    
    # Handle Restart Project
    elif call.data.startswith("restart_"):
        project_name = call.data.replace("restart_", "")
        bot.edit_message_text(f"🔄 *Restarting '{project_name}'...*", 
                            call.message.chat.id, call.message.message_id, parse_mode="Markdown")
        
        if project_name in get_user_running_projects(user_id):
            stop_project(user_id, project_name)
            time.sleep(2)
        
        result = start_project(user_id, project_name)
        if result:
            bot.send_message(call.message.chat.id, f"✅ *'{project_name}' restarted successfully!*", parse_mode="Markdown")
        else:
            bot.send_message(call.message.chat.id, f"❌ *Failed to restart '{project_name}'*", parse_mode="Markdown")
    
    # Handle Delete Project
    elif call.data.startswith("delete_"):
        project_name = call.data.replace("delete_", "")
        delete_project(user_id, project_name)
        bot.edit_message_text(f"🗑️ *Project '{project_name}' deleted*", 
                            call.message.chat.id, call.message.message_id, parse_mode="Markdown")
    
    # Handle Delete All
    elif call.data == "confirm_delete_all":
        user_dir = get_user_dir(user_id)
        user_running = get_user_running_projects(user_id)
        
        for project in list(user_running.keys()):
            stop_project(user_id, project)
        
        user_running.clear()
        
        if user_id in project_errors:
            project_errors[user_id].clear()
        
        for item in os.listdir(user_dir):
            item_path = os.path.join(user_dir, item)
            if os.path.isdir(item_path):
                shutil.rmtree(item_path)
        
        bot.edit_message_text("🗑️ *ALL your projects deleted successfully!*", 
                            call.message.chat.id, call.message.message_id, parse_mode="Markdown")
    
    # Handle Cancel
    elif call.data == "cancel_delete":
        bot.edit_message_text("❌ *Action cancelled*", 
                            call.message.chat.id, call.message.message_id, parse_mode="Markdown")
    
    bot.answer_callback_query(call.id)

# ============== CORE FUNCTIONS ==============

def get_user_dir(user_id):
    user_dir = os.path.join(BASE_DIR, str(user_id))
    os.makedirs(user_dir, exist_ok=True)
    return user_dir

def get_user_projects(user_id):
    user_dir = get_user_dir(user_id)
    projects = []
    for item in os.listdir(user_dir):
        item_path = os.path.join(user_dir, item)
        if os.path.isdir(item_path):
            projects.append(item)
    return projects

def get_user_running_projects(user_id):
    if user_id not in running_projects:
        running_projects[user_id] = {}
    return running_projects[user_id]

def get_user_errors(user_id):
    if user_id not in project_errors:
        project_errors[user_id] = {}
    return project_errors[user_id]

def start_project(user_id, project_name):
    user_dir = get_user_dir(user_id)
    project_path = os.path.join(user_dir, project_name)
    main_file = os.path.join(project_path, "main.py")
    user_running = get_user_running_projects(user_id)
    
    if not os.path.exists(main_file):
        error_msg = f"main.py not found in '{project_name}'"
        user_errors = get_user_errors(user_id)
        user_errors[project_name] = error_msg
        return False
    
    if project_name in user_running:
        return False
    
    try:
        # Load environment variables if .env exists
        env = os.environ.copy()
        env_file = os.path.join(project_path, ".env")
        if os.path.exists(env_file):
            with open(env_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        if '=' in line:
                            key, value = line.split('=', 1)
                            env[key] = value
        
        process = subprocess.Popen(
            [sys.executable, "main.py"],
            cwd=project_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
            env=env
        )
        
        user_running[project_name] = {
            "process": process,
            "cwd": project_path,
            "started_at": datetime.now()
        }
        
        user_errors = get_user_errors(user_id)
        if project_name in user_errors:
            del user_errors[project_name]
        
        log_file = os.path.join(project_path, "project.log")
        with open(log_file, 'a') as f:
            f.write(f"\n[STARTED] at {datetime.now()}\n")
        
        monitor_thread = threading.Thread(target=monitor_single_project, args=(user_id, project_name, process))
        monitor_thread.daemon = True
        monitor_thread.start()
        
        return True
        
    except Exception as e:
        error_msg = str(e)
        user_errors = get_user_errors(user_id)
        user_errors[project_name] = error_msg
        return False

def stop_project(user_id, project_name):
    user_running = get_user_running_projects(user_id)
    
    if project_name in user_running:
        try:
            project_info = user_running[project_name]
            process = project_info["process"] if isinstance(project_info, dict) else project_info
            
            process.terminate()
            
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            
            del user_running[project_name]
            
            user_dir = get_user_dir(user_id)
            project_path = os.path.join(user_dir, project_name)
            log_file = os.path.join(project_path, "project.log")
            if os.path.exists(log_file):
                with open(log_file, 'a') as f:
                    f.write(f"[STOPPED] at {datetime.now()}\n")
            
            return True
            
        except Exception as e:
            if project_name in user_running:
                del user_running[project_name]
            return False
    return False

def monitor_single_project(user_id, project_name, process):
    try:
        process.wait()
        
        user_running = get_user_running_projects(user_id)
        if project_name in user_running:
            del user_running[project_name]
            user_errors = get_user_errors(user_id)
            user_errors[project_name] = f"Process stopped at {datetime.now().strftime('%H:%M:%S')}"
            
    except Exception as e:
        print(f"Monitor error for {project_name}: {e}")

def delete_project(user_id, project_name):
    user_running = get_user_running_projects(user_id)
    
    if project_name in user_running:
        stop_project(user_id, project_name)
    
    user_dir = get_user_dir(user_id)
    project_path = os.path.join(user_dir, project_name)
    if os.path.exists(project_path):
        shutil.rmtree(project_path)
    
    user_errors = get_user_errors(user_id)
    if project_name in user_errors:
        del user_errors[project_name]
    
    # Clean up env vars
    if user_id in project_env_vars and project_name in project_env_vars[user_id]:
        del project_env_vars[user_id][project_name]

@bot.message_handler(content_types=['document'])
def handle_zip(msg):
    user_id = msg.chat.id
    
    if not msg.document.file_name.endswith(".zip"):
        bot.send_message(msg.chat.id, "❌ Send only .zip file")
        return

    status_msg = bot.send_message(msg.chat.id, "📦 *Processing upload...*", parse_mode="Markdown")

    file_info = bot.get_file(msg.document.file_id)
    downloaded = bot.download_file(file_info.file_path)

    original_name = msg.document.file_name.replace(".zip", "")
    project_name = "".join(c for c in original_name if c.isalnum() or c in ('-', '_'))
    
    user_dir = get_user_dir(user_id)
    project_path = os.path.join(user_dir, project_name)
    
    if os.path.exists(project_path):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        project_name = f"{project_name}_{timestamp}"
        project_path = os.path.join(user_dir, project_name)
        bot.send_message(msg.chat.id, f"📝 *Project renamed to '{project_name}' to avoid conflict*", parse_mode="Markdown")
    
    try:
        zip_path = os.path.join(user_dir, f"{project_name}.zip")
        extract_path = project_path
        
        with open(zip_path, 'wb') as f:
            f.write(downloaded)
        
        os.makedirs(extract_path, exist_ok=True)
        
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(extract_path)
            
            extracted_items = os.listdir(extract_path)
            if len(extracted_items) == 1 and os.path.isdir(os.path.join(extract_path, extracted_items[0])):
                subfolder = os.path.join(extract_path, extracted_items[0])
                for item in os.listdir(subfolder):
                    shutil.move(os.path.join(subfolder, item), extract_path)
                os.rmdir(subfolder)
        
        os.remove(zip_path)
        
        bot.edit_message_text("📦 *Extracted!*", msg.chat.id, status_msg.message_id, parse_mode="Markdown")
        
        req_file = os.path.join(extract_path, "requirements.txt")
        if os.path.exists(req_file):
            subprocess.run(["pip", "install", "-r", req_file], cwd=extract_path)
            bot.edit_message_text("📥 *Requirements Installed*", msg.chat.id, status_msg.message_id, parse_mode="Markdown")
        
        main_file = os.path.join(extract_path, "main.py")
        if not os.path.exists(main_file):
            bot.edit_message_text(f"⚠️ *Warning: No main.py found in '{project_name}'*", 
                                msg.chat.id, status_msg.message_id, parse_mode="Markdown")
        
        markup = InlineKeyboardMarkup()
        markup.add(
            InlineKeyboardButton("▶️ Start Now", callback_data=f"start_{project_name}"),
            InlineKeyboardButton("⚙️ Set Env Vars", callback_data=f"env_{project_name}")
        )
        
        bot.edit_message_text(f"✅ *Project '{project_name}' uploaded successfully!*\n\n📁 Size: {get_folder_size(extract_path)}\n📄 main.py: {'✅' if os.path.exists(main_file) else '❌'}\n\nClick below to start:", 
                            msg.chat.id, status_msg.message_id, parse_mode="Markdown", reply_markup=markup)
        
    except Exception as e:
        error_msg = str(e)
        bot.edit_message_text(f"❌ *Upload failed:* `{error_msg[:150]}`", 
                            msg.chat.id, status_msg.message_id, parse_mode="Markdown")
        user_errors = get_user_errors(user_id)
        user_errors[project_name] = error_msg

def get_folder_size(folder_path):
    total = 0
    for dirpath, dirnames, filenames in os.walk(folder_path):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            total += os.path.getsize(fp)
    
    for unit in ['B', 'KB', 'MB', 'GB']:
        if total < 1024.0:
            return f"{total:.1f} {unit}"
        total /= 1024.0
    return f"{total:.1f} TB"

def get_all_users():
    """Get list of all users"""
    users = []
    for user_id in os.listdir(BASE_DIR):
        user_path = os.path.join(BASE_DIR, user_id)
        if os.path.isdir(user_path) and user_id.isdigit():
            users.append(int(user_id))
    return users

def get_user_project_count(user_id):
    """Get project count for a user"""
    user_dir = os.path.join(BASE_DIR, str(user_id))
    if not os.path.exists(user_dir):
        return 0
    return len([d for d in os.listdir(user_dir) if os.path.isdir(os.path.join(user_dir, d))])

def update_admin_stats():
    """Update admin statistics"""
    users = set()
    total_projects = 0
    total_running = 0
    
    for user_id, user_projects in running_projects.items():
        users.add(user_id)
        total_running += len(user_projects)
    
    for user_id in os.listdir(BASE_DIR):
        user_path = os.path.join(BASE_DIR, user_id)
        if os.path.isdir(user_path):
            users.add(int(user_id) if user_id.isdigit() else user_id)
            for project in os.listdir(user_path):
                project_path = os.path.join(user_path, project)
                if os.path.isdir(project_path):
                    total_projects += 1
    
    admin_stats["total_users"] = len(users)
    admin_stats["total_projects"] = total_projects
    admin_stats["total_running"] = total_running

def broadcast_message(message_text, user_ids=None):
    """Broadcast message to users"""
    if user_ids is None:
        user_ids = get_all_users()
    
    success = 0
    failed = 0
    
    for uid in user_ids:
        try:
            bot.send_message(uid, f"📢 *ANNOUNCEMENT*\n\n{message_text}", parse_mode="Markdown")
            success += 1
        except:
            failed += 1
        time.sleep(0.1)
    
    return success, failed

def clean_orphaned_processes():
    """Clean up orphaned process entries"""
    cleaned = 0
    for user_id, user_projects in list(running_projects.items()):
        for project_name, info in list(user_projects.items()):
            process = info["process"] if isinstance(info, dict) else info
            if process.poll() is not None:
                del running_projects[user_id][project_name]
                cleaned += 1
    return cleaned

def monitor_processes():
    while True:
        time.sleep(10)
        for user_id, user_projects in list(running_projects.items()):
            for project_name, info in list(user_projects.items()):
                process = info["process"] if isinstance(info, dict) else info
                if process.poll() is not None:
                    del running_projects[user_id][project_name]
                    if user_id not in project_errors:
                        project_errors[user_id] = {}
                    project_errors[user_id][project_name] = f"⚠️ Crashed at {datetime.now().strftime('%H:%M:%S')}"
                    print(f"⚠️ User {user_id} project '{project_name}' crashed")

monitor_thread = threading.Thread(target=monitor_processes, daemon=True)
monitor_thread.start()

# ============== BOT STARTUP ==============

print("="*50)
print("🔥 PYTHON HOSTING PANEL v2.0 - READY")
print("="*50)
print("✅ Bot Running Successfully!")
print(f"📁 Base Directory: {BASE_DIR}")
print(f"👥 Multi-User Support: ENABLED")
print(f"🔒 Private Workspaces: YES")
print(f"👑 Admin ID: {ADMIN_ID}")
print(f"🐙 GitHub Integration: ENABLED (No git module required)")
print(f"⚙️ Environment Variables: ENABLED")
print(f"💬 Support: @Hexh4ckerOFC")
print("="*50)

bot.infinity_polling()