# main.py - Master Bot (final)
# Persona: Cute 18-year-old girl (gpt-5.1) + robust moderation
# Paste into ~/MasterBot/main.py and run with your venv active.

import os
import re
import logging
import random
from datetime import datetime, timedelta
from typing import Optional

from dotenv import load_dotenv
from pyrogram import Client, filters
from pyrogram.types import Message, ChatPermissions, User

# Modern OpenAI client
try:
    from openai import OpenAI as OpenAIClient
except Exception:
    OpenAIClient = None

# ----------------------------
# Configuration & logging
# ----------------------------
load_dotenv()  # expects .env in working dir or parent

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", None)

# List of additional bot-level admin user IDs (optional)
BOT_ADMINS = set()  # e.g. {12345678, 98765432}

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger("masterbot")

# ----------------------------
# Initialize OpenAI modern client (v1+)
# ----------------------------
openai_client = None
if OPENAI_API_KEY and OpenAIClient is not None:
    try:
        openai_client = OpenAIClient(api_key=OPENAI_API_KEY)
        log.info("OpenAI client initialised successfully.")
    except Exception as e:
        openai_client = None
        log.exception("Failed to initialize OpenAI client: %s", e)
elif OPENAI_API_KEY and OpenAIClient is None:
    log.warning("OPENAI_API_KEY present but OpenAI SDK not installed. pip install openai")
else:
    log.info("No OPENAI_API_KEY found; using local persona fallbacks.")

# ----------------------------
# Pyrogram client init
# ----------------------------
app = Client(
    "MasterBot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

# ----------------------------
# Persona and local fallbacks
# ----------------------------
PERSONA_PROMPT = (
    "You are 'Master' — an 18-year-old girl. Speak sweetly, warmly, and naturally. "
    "Use short, friendly sentences and mix simple Hindi phrases if it feels natural. "
    "Avoid any content that is sexual, illegal, or harmful. Keep replies concise (1-4 sentences)."
)

PERSONA_FALLBACKS = [
    "Hey! I'm Master — your friendly AI girl. 😊 How can I help you?",
    "Hi! I'm here and listening — tell me what's on your mind!",
    "Hello! Feeling cheerful today — ask me anything!",
    "Hi there — I'm Master. What would you like to do?"
]

# ----------------------------
# Utility helpers
# ----------------------------
async def is_bot_admin_or_owner(client: Client, user_id: int) -> bool:
    if user_id == OWNER_ID:
        return True
    if user_id in BOT_ADMINS:
        return True
    return False

async def can_moderate(client: Client, message: Message, user_id: int) -> bool:
    """
    Returns True if user_id may perform moderation:
    - bot owner
    - bot admin (BOT_ADMINS)
    - chat creator
    - chat admin with restrictive rights
    """
    if await is_bot_admin_or_owner(client, user_id):
        return True
    try:
        member = await client.get_chat_member(message.chat.id, user_id)
    except Exception:
        return False

    if member.status == "creator":
        return True
    if member.status == "administrator":
        if getattr(member, "can_restrict_members", False) or getattr(member, "can_promote_members", False):
            return True
    return False

def parse_duration(text: str) -> Optional[timedelta]:
    """
    Parses the first duration expression in text.
    Supports: min(s), minute(s), hour(s), day(s)
    Returns timedelta or None.
    """
    m = re.search(r"(\d+)\s*(min(?:ute)?s?|hour(?:s?)|day(?:s?))", text, flags=re.IGNORECASE)
    if not m:
        return None
    amt = int(m.group(1))
    unit = m.group(2).lower()
    if "min" in unit:
        return timedelta(minutes=amt)
    if "hour" in unit:
        return timedelta(hours=amt)
    if "day" in unit:
        return timedelta(days=amt)
    return None

async def resolve_user(client: Client, message: Message) -> Optional[User]:
    """
    Resolve a target user in priority:
    1) reply_to_message.from_user
    2) text_mention entities (entity.user)
    3) mention entity (@username) -> client.get_users
    4) raw @username in text -> client.get_users
    5) best-effort first-name/username match among recent chat members (limited)
    """
    # 1) reply
    if message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user

    # 2) text_mention entity (contains user)
    if message.entities:
        for ent in message.entities:
            if ent.type == "text_mention" and ent.user:
                return ent.user

    # 3) entity mention -> resolve
    if message.entities:
        for ent in message.entities:
            if ent.type == "mention":
                try:
                    start = ent.offset
                    length = ent.length
                    mention_text = message.text[start:start+length]  # like @username
                    user = await client.get_users(mention_text)
                    if user:
                        return user
                except Exception:
                    continue

    # 4) raw @username regex fallback
    m = re.search(r"@([A-Za-z0-9_]{5,})", message.text or "")
    if m:
        uname = m.group(1)
        try:
            user = await client.get_users(f"@{uname}")
            if user:
                return user
        except Exception:
            pass

    # 5) best-effort name lookup (limited)
    # Try: find exact first-name or username among recent members (not guaranteed)
    try:
        candidate = None
        m2 = re.search(r"master(?:\s+|:)\s*([A-Za-z0-9_ ]{2,40})", message.text or "", flags=re.IGNORECASE)
        if m2:
            candidate = m2.group(1).strip().split()[0]
        if not candidate:
            # fallback to first token
            candidate = (message.text or "").strip().split()[0]
        if candidate:
            candidate = candidate.strip()
            async for member in client.get_chat_members(message.chat.id, limit=200):
                u = member.user
                if not u:
                    continue
                if (u.username and u.username.lower() == candidate.lower()) or (u.first_name and u.first_name.lower() == candidate.lower()):
                    return u
    except Exception:
        pass

    return None

def detect_action(text: str) -> Optional[str]:
    """
    Detect 'mute','unmute','ban','unban','kick' from text (Hindi/English tolerant).
    """
    t = text.lower()
    if re.search(r"\b(mute|chup|silent|silence|restrict|restricted)\b", t):
        return "mute"
    if re.search(r"\b(unmute|allow|undo mute|remove mute|kholo|bolne do)\b", t):
        return "unmute"
    if re.search(r"\b(ban|nikal|remove from group|ban karo|nikal do)\b", t):
        return "ban"
    if re.search(r"\b(unban|wapis|unblock|remove ban|unban karo)\b", t):
        return "unban"
    if re.search(r"\b(kick|kick out|bahar|nakaal)\b", t):
        return "kick"
    return None

# ----------------------------
# OpenAI conversation / persona function using modern v1 API
# ----------------------------
async def ai_generate_reply(user_text: str) -> str:
    """
    Generate persona reply. If openai key/client exists, call modern API:
    client.chat.completions.create(model="gpt-5.1", messages=[...])
    Otherwise fall back to local persona messages.
    """
    # Use OpenAI if available
    if openai_client:
        try:
            # Use recommended chat completions interface for v1 client
            resp = openai_client.chat.completions.create(
                model="gpt-5.1",
                messages=[
                    {"role": "system", "content": PERSONA_PROMPT},
                    {"role": "user", "content": user_text}
                ],
                temperature=0.85,
                max_tokens=300,
            )
            # resp.choices[0].message.content or similar structure
            if resp and getattr(resp, "choices", None):
                choice = resp.choices[0]
                # Different SDK builds expose shape differently; attempt robust extraction
                content = None
                # Attempt attribute access first
                try:
                    msg = getattr(choice, "message", None)
                    if isinstance(msg, dict):
                        content = msg.get("content")
                    elif msg and hasattr(msg, "get"):
                        content = msg.get("content")
                except Exception:
                    content = None
                # Fallback to dict-style
                if not content:
                    try:
                        content = choice["message"]["content"]
                    except Exception:
                        try:
                            # Some SDKs return choice.text
                            content = getattr(choice, "text", None)
                        except Exception:
                            content = None
                if content:
                    return content.strip()
        except Exception as e:
            log.exception("OpenAI call failed: %s", e)
            # fall through to local fallback

    # Local fallback heuristics
    lower = (user_text or "").lower()
    if any(kw in lower for kw in ["how are you", "kya haal", "how r u", "kya haal hai"]):
        return "I'm doing great — thank you for asking! 😊 What about you?"
    if "joke" in lower:
        return "Why did the programmer go broke? Because he used up all his cache! 😅"
    if "song" in lower or "music" in lower:
        return "I love music! Tell me the song name and I will pretend I'm playing it. 🎧"
    # default fallback
    return random.choice(PERSONA_FALLBACKS)

# ----------------------------
# Commands: /start, /ping
# ----------------------------
@app.on_message(filters.command("start"))
async def cmd_start(_, message: Message):
    first = message.from_user.first_name or "there"
    await message.reply_text(
        f"Hi {first}, I'm Master — a cute AI girl. 💕\n\n"
        "• In private chat I can chat with you freely.\n"
        "• In groups I only respond when someone mentions 'master' or replies to a 'master' message.\n"
        "• Moderation (mute/unmute/ban/unban/kick) is available to group admins and owners."
    )

@app.on_message(filters.command("ping") & filters.user(OWNER_ID))
async def cmd_ping(_, message: Message):
    await message.reply_text("Pong! Bot owner verified.")

# ----------------------------
# Moderation handler (priority group=1)
# ----------------------------
@app.on_message(filters.text & filters.group, group=1)
async def moderation_handler(client: Client, message: Message):
    """
    This handler only runs for group messages and has priority.
    It first checks if the message is 'master' related and contains moderation keywords.
    Then checks moderator rights and performs actions.
    """
    try:
        text = (message.text or "").strip()
        if not text:
            return
        lower = text.lower()

        # Must be master-related: message contains 'master' OR reply-to contains 'master'
        contains_master = "master" in lower
        is_reply_to_master = False
        if message.reply_to_message and message.reply_to_message.text:
            is_reply_to_master = "master" in (message.reply_to_message.text.lower() or "")

        if not (contains_master or is_reply_to_master):
            return  # not a master-related message

        # If message doesn't contain moderation keywords, ignore in moderation handler
        if not re.search(r"(mute|unmute|ban|unban|kick|nikal|chup|remove|allow|unblock)", lower):
            return

        # Permission check
        sender_id = message.from_user.id
        if not await can_moderate(client, message, sender_id):
            await message.reply_text("You are not allowed to perform moderation actions.")
            return

        # Resolve target user
        target = await resolve_user(client, message)
        if not target or not getattr(target, "id", None):
            await message.reply_text("Cannot detect the target user. Reply to the user or mention them with @username.")
            return

        # Detect action
        action = detect_action(lower)
        if not action:
            await message.reply_text("No recognized moderation action found (mute/unmute/ban/unban/kick).")
            return

        # Parse duration if mute
        duration_td = None
        if action == "mute":
            duration_td = parse_duration(lower) or timedelta(minutes=10)

        # Execute actions
        if action == "mute":
            until_time = datetime.utcnow() + duration_td
            await message.chat.restrict_member(
                target.id,
                permissions=ChatPermissions(
                    can_send_messages=False,
                    can_send_media_messages=False,
                    can_send_other_messages=False,
                    can_add_web_page_previews=False
                ),
                until_date=until_time
            )
            await message.reply_text(f"{target.first_name or target.username} muted for {duration_td}.")
            return

        if action == "unmute":
            # Hardening: ensure target exists and has id
            if not target or not getattr(target, "id", None):
                await message.reply_text("Unmute failed: target not resolvable.")
                return
            try:
                # Attempt to restore send permissions (safe operation).
                await message.chat.restrict_member(
                    target.id,
                    permissions=ChatPermissions(
                        can_send_messages=True,
                        can_send_media_messages=True,
                        can_send_other_messages=True,
                        can_add_web_page_previews=True
                    ),
                    until_date=None
                )
                await message.reply_text(f"{target.first_name or target.username} has been unmuted.")
            except Exception as e:
                log.exception("Unmute error: %s", e)
                await message.reply_text(f"Unmute failed: {e}")
            return

        if action == "ban":
            try:
                await message.chat.ban_member(target.id)
                await message.reply_text(f"{target.first_name or target.username} has been banned.")
            except Exception as e:
                log.exception("Ban error: %s", e)
                await message.reply_text(f"Ban failed: {e}")
            return

        if action == "unban":
            try:
                await message.chat.unban_member(target.id)
                await message.reply_text(f"{target.first_name or target.username} has been unbanned.")
            except Exception:
                await message.reply_text(f"{target.first_name or target.username} was not banned or unban failed.")
            return

        if action == "kick":
            try:
                await message.chat.ban_member(target.id)
                await message.chat.unban_member(target.id)
                await message.reply_text(f"{target.first_name or target.username} has been kicked.")
            except Exception as e:
                log.exception("Kick error: %s", e)
                await message.reply_text(f"Kick failed: {e}")
            return

    except Exception as err:
        log.exception("Error in moderation_handler: %s", err)
        try:
            await message.reply_text("An error occurred while processing moderation.")
        except Exception:
            pass

# ----------------------------
# AI handler (priority group=2)
# DM: reply to all messages
# Group: only when 'master' is in message OR reply-to contains 'master'
# ----------------------------
@app.on_message(filters.text & (filters.private | filters.group), group=2)
async def ai_handler(client: Client, message: Message):
    try:
        text = (message.text or "").strip()
        if not text:
            return

        # Private chat: respond to all messages
        if message.chat.type == "private":
            reply = await ai_generate_reply(text)
            await message.reply_text(reply)
            return

        # Group chat: only respond when 'master' or reply-to contains 'master'
        lower = text.lower()
        contains_master = "master" in lower
        reply_to_master = False
        if message.reply_to_message and message.reply_to_message.text:
            reply_to_master = "master" in (message.reply_to_message.text.lower() or "")

        if not (contains_master or reply_to_master):
            return

        reply = await ai_generate_reply(text)
        await message.reply_text(reply)
        return

    except Exception as err:
        log.exception("Error in AI handler: %s", err)
        try:
            await message.reply_text("Sorry, I couldn't reply right now.")
        except Exception:
            pass

# ----------------------------
# Application start
# ----------------------------
if __name__ == "__main__":
    log.info("Starting Master Bot...")
    app.run()
