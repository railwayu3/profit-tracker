import os
import logging
import uuid
import io
import pandas as pd
from dotenv import load_dotenv
from supabase import create_client, Client
from telegram import ReplyKeyboardMarkup, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, ContextTypes, 
    ConversationHandler, MessageHandler, filters, CallbackQueryHandler
)

load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)

# States - Added CONFIRM_EXPORT
(CHOOSING, SETUP_BIZ, ADD_CAT_LATER, 
 SALE_CAT, SALE_AMOUNT, SALE_EXPENSE, SALE_REMARK, CONFIRM_EXPORT) = range(8)

# Updated MAIN_MENU with Export button
MAIN_MENU = [
    ['💰 Add New Sale', '📊 Reports'],
    ['📜 Manage Sales', '⚙️ Add Category'],
    ['📥 Export Data', '❌ Cancel Flow']
]
main_markup = ReplyKeyboardMarkup(MAIN_MENU, resize_keyboard=True)

def is_menu_button(text):
    flat_menu = [item for sublist in MAIN_MENU for item in sublist]
    return text in flat_menu or text == "❌ Cancel Flow"

# --- CORE HANDLERS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    user_id = update.message.from_user.id
    res = supabase.table("users").select("business_name").eq("user_id", user_id).execute()
    
    if not res.data:
        await update.message.reply_text("👋 **Welcome to Profit Tracker!**\n\nWhat is your **Business Name**? 🏢", parse_mode="Markdown")
        return SETUP_BIZ
    
    await update.message.reply_text(
        f"🏧 **{res.data[0]['business_name']}** Dashboard",
        reply_markup=main_markup,
        parse_mode="Markdown"
    )
    return CHOOSING

async def save_business_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    biz_name = update.message.text
    if is_menu_button(biz_name): return await cancel(update, context)
    supabase.table("users").upsert({"user_id": user_id, "business_name": biz_name}).execute()
    await update.message.reply_text(f"✅ Business **'{biz_name}'** registered!", parse_mode="Markdown")
    return ADD_CAT_LATER

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("🔄 Action cancelled.", reply_markup=main_markup)
    return CHOOSING

# --- EXPORT LOGIC ---

async def start_export(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kbd = [['✅ Yes, Export CSV', '❌ No, Cancel']]
    await update.message.reply_text(
        "📥 **Export Data**\n\nThis will generate a CSV file of all your transactions. Proceed?",
        reply_markup=ReplyKeyboardMarkup(kbd, one_time_keyboard=True, resize_keyboard=True),
        parse_mode="Markdown"
    )
    return CONFIRM_EXPORT

async def handle_export(update: Update, context: ContextTypes.DEFAULT_TYPE):
    choice = update.message.text
    if choice == '✅ Yes, Export CSV':
        user_id = update.message.from_user.id
        await update.message.reply_text("⏳ Generating report...")

        # Fetch all transactions for this user
        res = supabase.table("transactions").select(
            "created_at, type, amount, remark"
        ).eq("user_id", user_id).order("created_at", desc=True).execute()

        if not res.data:
            await update.message.reply_text("📭 No data found to export.", reply_markup=main_markup)
            return CHOOSING

        # Create DataFrame and format
        df = pd.DataFrame(res.data)
        df.columns = ['Date', 'Type', 'Amount', 'Remark']
        df['Date'] = pd.to_datetime(df['Date']).dt.strftime('%Y-%m-%d %H:%M')

        # Buffer to hold CSV data in memory
        csv_buffer = io.BytesIO()
        df.to_csv(csv_buffer, index=False)
        csv_buffer.seek(0)

        await context.bot.send_document(
            chat_id=user_id,
            document=csv_buffer,
            filename=f"Transactions_Export.csv",
            caption="📊 Your full transaction history."
        )
        await update.message.reply_text("✅ Export sent!", reply_markup=main_markup)
    else:
        await update.message.reply_text("🙌 Export cancelled.", reply_markup=main_markup)
    
    return CHOOSING

# --- SALE FLOW ---

async def start_sale(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    res = supabase.table("categories").select("id, name").eq("user_id", user_id).execute()
    if not res.data:
        await update.message.reply_text("⚠️ Add a category first!")
        return CHOOSING
    
    kbd = [[item['name']] for item in res.data]
    kbd.append(['❌ Cancel Flow'])
    context.user_data['cat_map'] = {item['name']: item['id'] for item in res.data}
    await update.message.reply_text("📂 **Select Category:**", reply_markup=ReplyKeyboardMarkup(kbd, one_time_keyboard=True, resize_keyboard=True))
    return SALE_CAT

async def sale_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_menu_button(update.message.text): return await cancel(update, context)
    context.user_data['cat_id'] = context.user_data['cat_map'].get(update.message.text)
    await update.message.reply_text("💰 **Selling Price:**")
    return SALE_AMOUNT

async def sale_expense(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if is_menu_button(text): return await cancel(update, context)
    context.user_data['sale_p'] = float(text)
    await update.message.reply_text("💸 **Cost Price (Expense):**\n(Enter 0 if none)")
    return SALE_EXPENSE

async def sale_remark(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if is_menu_button(text): return await cancel(update, context)
    context.user_data['cost_p'] = float(text)
    await update.message.reply_text("✍️ **Remarks:**")
    return SALE_REMARK

async def finish_sale(update: Update, context: ContextTypes.DEFAULT_TYPE):
    remark = update.message.text
    if is_menu_button(remark): return await cancel(update, context)
    
    user_id = update.message.from_user.id
    sale_p, cost_p, cat_id = context.user_data['sale_p'], context.user_data['cost_p'], context.user_data['cat_id']
    deal_id = str(uuid.uuid4())

    supabase.table("transactions").insert({
        "user_id": user_id, "category_id": cat_id, "amount": sale_p, "type": "Sale", "remark": remark, "link_id": deal_id
    }).execute()
    
    if cost_p > 0:
        supabase.table("transactions").insert({
            "user_id": user_id, "category_id": cat_id, "amount": cost_p, "type": "Expense", "remark": f"Cost: {remark}", "link_id": deal_id
        }).execute()
    
    await update.message.reply_text(f"✅ **Recorded!**\nProfit: **{sale_p - cost_p}**", reply_markup=main_markup, parse_mode="Markdown")
    return CHOOSING

# --- MANAGE SALES ---

async def manage_sales(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    res = supabase.table("transactions").select("amount, remark, link_id, created_at").eq("user_id", user_id).eq("type", "Sale").order("created_at", desc=True).limit(5).execute()
    
    if not res.data:
        await update.message.reply_text("📭 No sales found to manage.")
        return CHOOSING

    await update.message.reply_text("🗑️ **Select a sale to delete:**\n(This also removes its associated cost)", parse_mode="Markdown")
    
    for item in res.data:
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Delete Transaction", callback_data=f"del_{item['link_id']}") ]])
        await update.message.reply_text(
            f"💰 Sale: `{item['amount']}`\n📝 Note: {item['remark']}\n📅 {item['created_at'][11:16]}", 
            reply_markup=keyboard, parse_mode="Markdown"
        )
    return CHOOSING

async def handle_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    deal_id = query.data.replace("del_", "")
    supabase.table("transactions").delete().eq("link_id", deal_id).execute()
    await query.edit_message_text(text="🗑️ **Deal Deleted!** Revenue and Costs were removed.")

# --- CATEGORIES & REPORTS ---

async def start_add_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🆕 **New Category Name:**", reply_markup=ReplyKeyboardMarkup([['❌ Cancel Flow']], resize_keyboard=True))
    return ADD_CAT_LATER

async def add_cat_later(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_menu_button(update.message.text): return await cancel(update, context)
    supabase.table("categories").insert({"user_id": update.message.from_user.id, "name": update.message.text}).execute()
    await update.message.reply_text(f"✅ Category added!", reply_markup=main_markup)
    return CHOOSING

async def view_reports(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    res = supabase.table("transactions").select("type, amount").eq("user_id", user_id).execute()
    s = sum(i['amount'] for i in res.data if i['type'] == 'Sale')
    e = sum(i['amount'] for i in res.data if i['type'] == 'Expense')
    profit = s - e
    report = f"📊 **Financial Summary**\n━━━━━━━━━━━━━━━\n💰 Revenue: `{int(s)}`\n💸 Expenses: `{int(e)}`\n━━━━━━━━━━━━━━━\n🟢 **Profit: {int(profit)}**"
    await update.message.reply_text(report, parse_mode="Markdown", reply_markup=main_markup)
    return CHOOSING

# --- ADD THIS LOGIC TO YOUR main() FUNCTION ---

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # 1. Create a shared list of handlers for the main menu 
    # so they can be used both as entry points and as fallbacks.
    menu_handlers = [
        MessageHandler(filters.Regex("^💰 Add New Sale$"), start_sale),
        MessageHandler(filters.Regex("^📊 Reports$"), view_reports),
        MessageHandler(filters.Regex("^📜 Manage Sales$"), manage_sales),
        MessageHandler(filters.Regex("^⚙️ Add Category$"), start_add_category),
        MessageHandler(filters.Regex("^📥 Export Data$"), start_export),
        MessageHandler(filters.Regex("^❌ Cancel Flow$"), start),
    ]

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)] + menu_handlers, # Add here
        states={
            SETUP_BIZ: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_business_name)],
            CHOOSING: menu_handlers, # Use the shared list
            ADD_CAT_LATER: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_cat_later)],
            SALE_CAT: [MessageHandler(filters.TEXT & ~filters.COMMAND, sale_amount)],
            SALE_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, sale_expense)],
            SALE_EXPENSE: [MessageHandler(filters.TEXT & ~filters.COMMAND, sale_remark)],
            SALE_REMARK: [MessageHandler(filters.TEXT & ~filters.COMMAND, finish_sale)],
            CONFIRM_EXPORT: [MessageHandler(filters.Regex("^(✅ Yes, Export CSV|❌ No, Cancel)$"), handle_export)],
        },
        # 2. Add menu_handlers to fallbacks. 
        # This allows buttons to "break out" of a stuck state.
        fallbacks=[CommandHandler("start", start)] + menu_handlers, 
    )
    
    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(handle_delete_callback)) 
    app.run_polling()

if __name__ == "__main__": main()