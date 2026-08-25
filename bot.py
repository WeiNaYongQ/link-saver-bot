import os
import re
import urllib.parse
import psycopg2
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

# ========== DATABASE SETUP (PostgreSQL) ==========
def get_db_connection():
    """Get connection to PostgreSQL database"""
    database_url = os.environ.get('DATABASE_URL')
    if database_url:
        result = urllib.parse.urlparse(database_url)
        return psycopg2.connect(
            database=result.path[1:],
            user=result.username,
            password=result.password,
            host=result.hostname,
            port=result.port
        )
    else:
        # Fallback for local testing
        return psycopg2.connect(
            database="postgres",
            user="postgres",
            password="password",
            host="localhost",
            port="5432"
        )

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (user_id BIGINT PRIMARY KEY, is_premium INTEGER DEFAULT 0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS links
                 (id SERIAL PRIMARY KEY, 
                  user_id BIGINT, 
                  url TEXT, 
                  title TEXT,
                  saved_date TEXT)''')
    conn.commit()
    conn.close()

def add_user(user_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("INSERT INTO users (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (user_id,))
    conn.commit()
    conn.close()

def is_premium_user(user_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT is_premium FROM users WHERE user_id = %s", (user_id,))
    result = c.fetchone()
    conn.close()
    return result and result[0] == 1

def get_link_count(user_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM links WHERE user_id = %s", (user_id,))
    count = c.fetchone()[0]
    conn.close()
    return count

def save_link(user_id, url):
    conn = get_db_connection()
    c = conn.cursor()
    title = url.replace('https://', '').replace('http://', '').split('/')[0]
    if len(title) > 50:
        title = title[:50] + '...'
    c.execute("INSERT INTO links (user_id, url, title, saved_date) VALUES (%s, %s, %s, %s)",
              (user_id, url, title, datetime.now().strftime("%Y-%m-%d %H:%M")))
    conn.commit()
    conn.close()
    return title

def get_links(user_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT id, url, title, saved_date FROM links WHERE user_id = %s ORDER BY id DESC", (user_id,))
    links = c.fetchall()
    conn.close()
    return links

def delete_link(user_id, link_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("DELETE FROM links WHERE id = %s AND user_id = %s", (link_id, user_id))
    affected = c.rowcount
    conn.commit()
    conn.close()
    return affected > 0

def search_links(user_id, query):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT id, url, title, saved_date FROM links WHERE user_id = %s AND (url LIKE %s OR title LIKE %s) ORDER BY id DESC",
              (user_id, f'%{query}%', f'%{query}%'))
    links = c.fetchall()
    conn.close()
    return links

# ========== BOT COMMANDS ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    add_user(user_id)
    
    welcome = """📌 *Welcome to Link Saver Bot!*

I save your links so you don't forget them.

📖 *Commands:*
/save [url] - Save a link
/list - View all your links
/search [term] - Search your links
/delete [id] - Delete a link
/stats - See your usage
/premium - Upgrade to premium

🔓 *Free Tier:* 20 links
⭐ *Premium:* Unlimited links + search

Send /save https://example.com to start saving!
"""
    await update.message.reply_text(welcome, parse_mode='Markdown')

async def save_link_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    add_user(user_id)
    
    if not is_premium_user(user_id):
        count = get_link_count(user_id)
        if count >= 20:
            keyboard = [[InlineKeyboardButton("⭐ Upgrade to Premium", callback_data='upgrade')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(
                "❌ You've reached the free limit of 20 links!\n"
                "Upgrade to Premium for unlimited links.",
                reply_markup=reply_markup
            )
            return
    
    text = update.message.text
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        await update.message.reply_text("❌ Please provide a URL.\nExample: /save https://example.com")
        return
    
    url = parts[1].strip()
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    
    title = save_link(user_id, url)
    count = get_link_count(user_id)
    
    await update.message.reply_text(
        f"✅ *Link Saved!*\n\n"
        f"📌 {title}\n"
        f"🔗 {url}\n"
        f"📊 You have {count} saved link{'s' if count > 1 else ''}."
        f"\n\nUse /list to view all.",
        parse_mode='Markdown'
    )

async def list_links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    add_user(user_id)
    
    links = get_links(user_id)
    if not links:
        await update.message.reply_text("📭 You haven't saved any links yet.\n\nUse /save [url] to get started!")
        return
    
    text = f"📌 *Your Saved Links ({len(links)})*\n\n"
    for i, (link_id, url, title, date) in enumerate(links[:20], 1):
        display_url = url[:40] + '...' if len(url) > 40 else url
        text += f"{i}. [{title}]({url})\n   🕐 {date}\n   `/delete {link_id}`\n\n"
    
    if len(links) > 20:
        text += f"*Showing 20 of {len(links)} links.*\nUse /premium to upgrade for more."
    
    await update.message.reply_text(text, parse_mode='Markdown')

async def search_links_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    add_user(user_id)
    
    text = update.message.text
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        await update.message.reply_text("❌ Please provide a search term.\nExample: /search python")
        return
    
    query = parts[1].strip()
    links = search_links(user_id, query)
    
    if not links:
        await update.message.reply_text(f"🔍 No links found for '{query}'")
        return
    
    text = f"🔍 *Search Results for '{query}' ({len(links)})\n\n"
    for link_id, url, title, date in links[:10]:
        text += f"• [{title}]({url})\n  🕐 {date}\n  `/delete {link_id}`\n\n"
    
    if len(links) > 10:
        text += f"*Showing 10 of {len(links)} links.*"
    
    await update.message.reply_text(text, parse_mode='Markdown')

async def delete_link_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    text = update.message.text
    parts = text.split()
    if len(parts) < 2:
        await update.message.reply_text("❌ Please provide a link ID.\nUse /list to see IDs.\nExample: /delete 1")
        return
    
    try:
        link_id = int(parts[1])
    except ValueError:
        await update.message.reply_text("❌ Invalid ID. Please use a number.\nExample: /delete 1")
        return
    
    if delete_link(user_id, link_id):
        await update.message.reply_text(f"✅ Link #{link_id} deleted successfully!")
    else:
        await update.message.reply_text(f"❌ Link #{link_id} not found or already deleted.")

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    add_user(user_id)
    
    count = get_link_count(user_id)
    premium = is_premium_user(user_id)
    
    text = f"""📊 *Your Statistics*

📌 Total Links: {count}
⭐ Premium: {'✅ Yes' if premium else '❌ No'}
📈 Free Limit: {'20' if not premium else 'Unlimited'}
{'🚀 You can save more!' if not premium and count < 20 else ''}

{'Use /premium to upgrade!' if not premium else 'Thanks for being premium! 🎉'}"""
    
    await update.message.reply_text(text, parse_mode='Markdown')

async def premium_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    add_user(user_id)
    
    if is_premium_user(user_id):
        await update.message.reply_text("🎉 You're already a Premium user! Thank you!")
        return
    
    text = """⭐ *Upgrade to Premium*

✨ *Benefits:*
• Unlimited links (no 20-link limit)
• Advanced search
• Priority support
• Future features

💰 *Price:* 50 Stars/month

💳 *How to pay:*
1. Tap the button below
2. Confirm payment with Stars
3. Enjoy unlimited saving!
"""
    
    keyboard = [[InlineKeyboardButton("⭐ Pay 50 Stars", callback_data='premium_pay')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(text, parse_mode='Markdown', reply_markup=reply_markup)

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    data = query.data
    
    if data == 'premium_pay':
        text = """⭐ *Payment Requested*

Please send 50 Stars to @YourBotName.

*How to send Stars:*
1. Tap the 3 dots (...) in this chat
2. Select "Send Stars"
3. Enter 50
4. Send!

After sending, type /verify to confirm.
"""
        await query.edit_message_text(text, parse_mode='Markdown')
    
    elif data == 'upgrade':
        await query.edit_message_text("Use /premium to see upgrade options!")

async def verify_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    add_user(user_id)
    
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("UPDATE users SET is_premium = 1 WHERE user_id = %s", (user_id,))
    conn.commit()
    conn.close()
    
    await update.message.reply_text("🎉 *Premium Activated!*\n\nYou now have unlimited links. Enjoy! 🚀", parse_mode='Markdown')

# ========== MAIN ==========
def main():
    print("🤖 Starting Link Saver Bot...")
    
    init_db()
    print("✅ Database ready")
    
    TOKEN = os.environ.get("BOT_TOKEN")
    if not TOKEN:
        print("❌ BOT_TOKEN environment variable not set!")
        return
    
    application = Application.builder().token(TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("save", save_link_command))
    application.add_handler(CommandHandler("list", list_links))
    application.add_handler(CommandHandler("search", search_links_command))
    application.add_handler(CommandHandler("delete", delete_link_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("premium", premium_command))
    application.add_handler(CommandHandler("verify", verify_payment))
    application.add_handler(CallbackQueryHandler(button_callback))
    
    print("✅ Bot is running! Press Ctrl+C to stop.")
    application.run_polling()

if __name__ == '__main__':
    main()
