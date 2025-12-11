from pyrogram import Client, filters
from pyrogram.types import Message
import asyncio
from pymongo import MongoClient
import os

# Load MongoDB
MONGO_URL = os.getenv("MONGO_URL")
mongo = MongoClient(MONGO_URL)
db = mongo["MasterBotDB"]
punishments = db["punishments"]
settings = db["settings"]

# Owner ID
OWNER_ID = int(os.getenv("BOT_OWNER_ID"))

# Utility function: check if user is owner/admin
async def is_bot_owner(message: Message):
    return message.from_user and message.from_user.id == OWNER_ID

# Function to get punishment info from DB
def get_user_data(user_id, group_id):
    return punishments.find_one({"user_id": user_id, "group_id": group_id}) or {"spams": 0}

def update_user_data(user_id, group_id, data):
    punishments.update_one({"user_id": user_id, "group_id": group_id}, {"$set": data}, upsert=True)

# === Soft Mute Command ===
@Client.on_message(filters.command(["mute", "master_mute"]) & filters.group)
async def mute_user(client, message: Message):
    if not message.reply_to_message:
        await message.reply_text("Reply to a user to mute them.")
        return

    target = message.reply_to_message.from_user
    group_id = message.chat.id
    user_data = get_user_data(target.id, group_id)

    # Escalating punishment
    duration = 5 * 60  # 5 minutes default
    if user_data["spams"] == 1:
        duration = 15 * 60
    elif user_data["spams"] >= 2:
        duration = 30 * 60

    # Update DB
    user_data["spams"] = user_data.get("spams", 0) + 1
    update_user_data(target.id, group_id, user_data)

    # Mute (restrict)
    await client.restrict_chat_member(group_id, target.id, until_date=int(message.date.timestamp()) + duration)
    await message.reply_text(f"{target.mention} is muted for {duration//60} minutes due to repeated spam.")

# === Unmute on Sorry Command ===
@Client.on_message(filters.text & filters.group)
async def sorry_reset(client, message: Message):
    if "master sorry" in message.text.lower() or "sorry master" in message.text.lower():
        group_id = message.chat.id
        target = message.from_user
        user_data = get_user_data(target.id, group_id)
        user_data["spams"] = 0
        update_user_data(target.id, group_id, user_data)
        await message.reply_text(f"{target.mention}, your spam limits have been reset. Be careful!")

# === Soft Ban Command ===
@Client.on_message(filters.command(["softban", "master_ban"]) & filters.group)
async def soft_ban(client, message: Message):
    if not message.reply_to_message:
        await message.reply_text("Reply to a user to soft ban them.")
        return

    target = message.reply_to_message.from_user
    group_id = message.chat.id
    await message.reply_text(f"{target.mention} is soft banned (no real ban, just for fun)!")

# === Global Ban (owner only) ===
@Client.on_message(filters.command(["gban", "global_ban"]) & filters.private)
async def global_ban(client, message: Message):
    if not await is_bot_owner(message):
        await message.reply_text("Only bot owner can use this command.")
        return
    if not message.reply_to_message:
        await message.reply_text("Reply to a user to global ban them.")
        return
    target = message.reply_to_message.from_user
    db["global_ban"].update_one({"user_id": target.id}, {"$set": {"banned": True}}, upsert=True)
    await message.reply_text(f"{target.mention} is globally banned from all groups.")

# === Check Global Ban on New Messages ===
@Client.on_message(filters.group)
async def check_global_ban(client, message: Message):
    user_id = message.from_user.id
    gb = db["global_ban"].find_one({"user_id": user_id})
    if gb and gb.get("banned"):
        await message.delete()
        await message.reply_text(f"{message.from_user.mention} is globally banned from this group.")

