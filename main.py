import meshtastic
import meshtastic.serial_interface
from meshtastic import BROADCAST_ADDR
from pubsub import pub
import json
import requests
import time
from datetime import datetime, timedelta, timezone  # Added timezone import
import threading
import os
import smtplib
from email.mime.text import MIMEText
import logging
import traceback
from flask import Flask, request, jsonify, redirect, url_for
import sys
import socket  # for socket error checking
from twilio.rest import Client  # for Twilio SMS support
from unidecode import unidecode   # Added unidecode import for Ollama text normalization
import asyncio
import discord
import pytz
from datetime import datetime
from flask import render_template_string, request, redirect, url_for
from flask import jsonify

# -----------------------------
# Verbose Logging Setup
# -----------------------------
SCRIPT_LOG_FILE = "script.log"
script_logs = []  # In-memory log entries (most recent 200)
server_start_time = datetime.now(timezone.utc)  # Now using UTC time
restart_count = 0

def add_script_log(message):
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    log_entry = f"{timestamp} - {message}"
    script_logs.append(log_entry)
    if len(script_logs) > 200:
        script_logs.pop(0)
    try:
        # Truncate file if larger than 100 MB (keep last 100 lines)
        if os.path.exists(SCRIPT_LOG_FILE):
            filesize = os.path.getsize(SCRIPT_LOG_FILE)
            if filesize > 100 * 1024 * 1024:
                with open(SCRIPT_LOG_FILE, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                last_lines = lines[-100:] if len(lines) >= 100 else lines
                with open(SCRIPT_LOG_FILE, "w", encoding="utf-8") as f:
                    f.writelines(last_lines)
        with open(SCRIPT_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(log_entry + "\n")
    except Exception as e:
        print(f"⚠️ Could not write to {SCRIPT_LOG_FILE}: {e}")

# Redirect stdout and stderr to our log while still printing to terminal.
class StreamToLogger(object):
    def __init__(self, logger_func):
        self.logger_func = logger_func
        self.terminal = sys.__stdout__
    def write(self, buf):
        self.terminal.write(buf)
        if buf.strip():
            self.logger_func(buf.strip())
    def flush(self):
        self.terminal.flush()

sys.stdout = StreamToLogger(add_script_log)
sys.stderr = StreamToLogger(add_script_log)

# -----------------------------
# Global Connection & Reset Status
# -----------------------------
connection_status = "Disconnected"
last_error_message = ""
reset_event = threading.Event()  # Global event to signal a fatal error and trigger reconnect

# -----------------------------
# Meshtastic and Flask Setup
# -----------------------------
try:
    from meshtastic.tcp_interface import TCPInterface
except ImportError:
    TCPInterface = None

try:
    from meshtastic.mesh_interface import MeshInterface
    MESH_INTERFACE_AVAILABLE = True
except ImportError:
    MESH_INTERFACE_AVAILABLE = False

log = logging.getLogger('werkzeug')
log.disabled = True

BANNER = """
.
.
.
███╗   ███╗███████╗███████╗██╗  ██╗████████╗ █████╗ ███████╗████████╗██╗ ██████╗     
████╗ ████║██╔════╝██╔════╝██║  ██║╚══██╔══╝██╔══██╗██╔════╝╚══██╔══╝██║██╔════╝     
██╔████╔██║█████╗  ███████╗███████║   ██║   ███████║███████╗   ██║   ██║██║          
██║╚██╔╝██║██╔══╝  ╚════██║██╔══██║   ██║   ██╔══██║╚════██║   ██║   ██║██║          
██║ ╚═╝ ██║███████╗███████║██║  ██║   ██║   ██║  ██║███████║   ██║   ██║╚██████╗     
╚═╝     ╚═╝╚══════╝╚══════╝╚═╝  ╚═╝   ╚═╝   ╚═╝  ╚═╝╚══════╝   ╚═╝   ╚═╝ ╚═════╝     
                                                                                     
 ██████╗ ██████╗ ███╗   ██╗████████╗██████╗  ██████╗ ██╗     ██╗     ███████╗██████╗ 
██╔════╝██╔═══██╗████╗  ██║╚══██╔══╝██╔══██╗██╔═══██╗██║     ██║     ██╔════╝██╔══██╗
██║     ██║   ██║██╔██╗ ██║   ██║   ██████╔╝██║   ██║██║     ██║     █████╗  ██████╔╝
██║     ██║   ██║██║╚██╗██║   ██║   ██╔══██╗██║   ██║██║     ██║     ██╔══╝  ██╔══██╗
╚██████╗╚██████╔╝██║ ╚████║   ██║   ██║  ██║╚██████╔╝███████╗███████╗███████╗██║  ██║
 ╚═════╝ ╚═════╝ ╚═╝  ╚═══╝   ╚═╝   ╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚══════╝╚══════╝╚═╝  ╚═╝
.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       
Version 2.0 by: Dr.Reeves https://www.nerdscorp.net   
Messaging Dashboard Access: http://localhost:5000/dashboard 
.
.
.
"""
print(BANNER)
add_script_log("Script started.")

# -----------------------------
# Load Config Files
# -----------------------------
CONFIG_FILE = "config/config.json"
CONFIG_PATH = "config/config.json"
COMMANDS_CONFIG_FILE = "config/commands_config.json"
MOTD_FILE = "config/motd.json"
LOG_FILE = "config/messages.log"
ARCHIVE_FILE = "config/messages_archive.json"

print("Loading config files...")

def safe_load_json(path, default_value):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"⚠️ {path} not found. Using defaults.")
    except Exception as e:
        print(f"⚠️ Could not load {path}: {e}")
    return default_value

def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)

config = safe_load_json(CONFIG_FILE, {})
timezone_str = config.get("timezone", "UTC")  # Default to UTC if not set
timezone_obj = pytz.timezone(timezone_str)
print(f"Timezone set to: {timezone_str}")
add_script_log(f"Timezone set to: {timezone_str}")
commands_config = safe_load_json(COMMANDS_CONFIG_FILE, {"commands": []})

def reload_config():
    global config
    config = safe_load_json(CONFIG_FILE, {})
    print("Config reloaded:", config)
    # re-assign any other globals here if needed
    # e.g. global AI_PROVIDER; AI_PROVIDER = config.get("ai_provider", "lmstudio").lower()

def save_commands_config(config):
    import json
    with open(COMMANDS_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

def async_send_message(mode, interface, message, dest_node, channel_idx):
    if mode == "direct" and dest_node:
        send_direct_chunks(interface, message, dest_node)
    else:
        send_broadcast_chunks(interface, message, channel_idx)

try:
    with open(MOTD_FILE, "r", encoding="utf-8") as f:
        motd_content = f.read()
except FileNotFoundError:
    print(f"⚠️ {MOTD_FILE} not found.")
    motd_content = "No MOTD available."

DEBUG_ENABLED = bool(config.get("debug", False))

def dprint(*args, **kwargs):
    if DEBUG_ENABLED:
        print(*args, **kwargs)

def info_print(*args, **kwargs):
    if not DEBUG_ENABLED:
        print(*args, **kwargs)

if DEBUG_ENABLED:
    print(f"DEBUG: Loaded main config => {config}")

# -----------------------------
# AI Provider & Other Config Vars
# -----------------------------
AI_PROVIDER = config.get("ai_provider", "lmstudio").lower()
SYSTEM_PROMPT = config.get("system_prompt", "You are a helpful assistant responding to mesh network chats.")
LMSTUDIO_URL = config.get("lmstudio_url", "http://localhost:1234/v1/chat/completions")
LMSTUDIO_TIMEOUT = config.get("lmstudio_timeout", 60)
OPENAI_API_KEY = config.get("openai_api_key", "")
OPENAI_MODEL = config.get("openai_model", "gpt-3.5-turbo")
OPENAI_TIMEOUT = config.get("openai_timeout", 30)
OLLAMA_URL = config.get("ollama_url", "http://localhost:11434/api/generate")
OLLAMA_MODEL = config.get("ollama_model", "llama2")
OLLAMA_TIMEOUT = config.get("ollama_timeout", 60)
HOME_ASSISTANT_URL = config.get("home_assistant_url", "")
HOME_ASSISTANT_TOKEN = config.get("home_assistant_token", "")
HOME_ASSISTANT_TIMEOUT = config.get("home_assistant_timeout", 30)
HOME_ASSISTANT_ENABLE_PIN = bool(config.get("home_assistant_enable_pin", False))
HOME_ASSISTANT_SECURE_PIN = str(config.get("home_assistant_secure_pin", "1234"))
HOME_ASSISTANT_ENABLED = bool(config.get("home_assistant_enabled", False))
HOME_ASSISTANT_CHANNEL_INDEX = int(config.get("home_assistant_channel_index", -1))
MAX_CHUNK_SIZE = config.get("chunk_size", 200)
MAX_CHUNKS = 5
CHUNK_DELAY = config.get("chunk_delay", 10)
MAX_RESPONSE_LENGTH = MAX_CHUNK_SIZE * MAX_CHUNKS
LOCAL_LOCATION_STRING = config.get("local_location_string", "Unknown Location")
AI_NODE_NAME = config.get("ai_node_name", "AI-Bot")
FORCE_NODE_NUM = config.get("force_node_num", None)

ENABLE_DISCORD = config.get("enable_discord", False)
DISCORD_WEBHOOK_URL = config.get("discord_webhook_url", None)
DISCORD_SEND_EMERGENCY = config.get("discord_send_emergency", False)
DISCORD_SEND_AI = config.get("discord_send_ai", False)
DISCORD_SEND_ALL = config.get("discord_send_all", False)
DISCORD_RESPONSE_CHANNEL_INDEX = config.get("discord_response_channel_index", None)
DISCORD_RECEIVE_ENABLED = config.get("discord_receive_enabled", True)
# New variable for inbound routing
DISCORD_INBOUND_CHANNEL_INDEX = config.get("discord_inbound_channel_index", None)
if DISCORD_INBOUND_CHANNEL_INDEX is not None:
    DISCORD_INBOUND_CHANNEL_INDEX = int(DISCORD_INBOUND_CHANNEL_INDEX)
ENABLE_DISCORD_WEBHOOK = config.get("enable_discord_webhook", False)
# For polling Discord messages (optional)
DISCORD_BOT_TOKEN = config.get("discord_bot_token", None)
DISCORD_CHANNEL_ID = config.get("discord_channel_id", None)

ENABLE_TWILIO = config.get("enable_twilio", False)
ENABLE_SMTP = config.get("enable_smtp", False)
ALERT_PHONE_NUMBER = config.get("alert_phone_number", None)
TWILIO_SID = config.get("twilio_sid", None)
TWILIO_AUTH_TOKEN = config.get("twilio_auth_token", None)
TWILIO_FROM_NUMBER = config.get("twilio_from_number", None)
SMTP_HOST = config.get("smtp_host", None)
SMTP_PORT = config.get("smtp_port", 587)
SMTP_USER = config.get("smtp_user", None)
SMTP_PASS = config.get("smtp_pass", None)
ALERT_EMAIL_TO = config.get("alert_email_to", None)

SERIAL_PORT = config.get("serial_port", "")
USE_WIFI = bool(config.get("use_wifi", False))
WIFI_HOST = config.get("wifi_host", None)
WIFI_PORT = int(config.get("wifi_port", 4403))
USE_MESH_INTERFACE = bool(config.get("use_mesh_interface", False))

app = Flask(__name__)
messages = []
interface = None

lastDMNode = None
lastChannelIndex = None

# -----------------------------
# Location Lookup Function
# -----------------------------
def get_node_location(node_id):
    if interface and hasattr(interface, "nodes") and node_id in interface.nodes:
        pos = interface.nodes[node_id].get("position", {})
        lat = pos.get("latitude")
        lon = pos.get("longitude")
        tstamp = pos.get("time")
        return lat, lon, tstamp
    return None, None, None

def load_archive():
    global messages
    if os.path.exists(ARCHIVE_FILE):
        try:
            with open(ARCHIVE_FILE, "r", encoding="utf-8") as f:
                arr = json.load(f)
            if isinstance(arr, list):
                messages = arr
                print(f"Loaded {len(messages)} messages from archive.")
        except Exception as e:
            print(f"⚠️ Could not load archive {ARCHIVE_FILE}: {e}")
    else:
        print("No archive found; starting fresh.")

def save_archive():
    try:
        with open(ARCHIVE_FILE, "w", encoding="utf-8") as f:
            json.dump(messages, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ Could not save archive to {ARCHIVE_FILE}: {e}")

def parse_node_id(node_str_or_int):
    if isinstance(node_str_or_int, int):
        return node_str_or_int
    if isinstance(node_str_or_int, str):
        if node_str_or_int == '^all':
            return BROADCAST_ADDR
        if node_str_or_int.lower() in ['!ffffffff', '!ffffffffl']:
            return BROADCAST_ADDR
        if node_str_or_int.startswith('!'):
            hex_part = node_str_or_int[1:]
            try:
                return int(hex_part, 16)
            except ValueError:
                dprint(f"parse_node_id: Unable to parse hex from {node_str_or_int}")
                return None
        try:
            return int(node_str_or_int)
        except ValueError:
            dprint(f"parse_node_id: {node_str_or_int} not recognized as int or hex.")
            return None
    return None

def get_node_fullname(node_id):
    """Return the full (long) name if available, otherwise the short name."""
    if interface and hasattr(interface, "nodes") and node_id in interface.nodes:
        user_dict = interface.nodes[node_id].get("user", {})
        return user_dict.get("longName", user_dict.get("shortName", f"Node_{node_id}"))
    return f"Node_{node_id}"

def get_node_shortname(node_id):
    if interface and hasattr(interface, "nodes") and node_id in interface.nodes:
        user_dict = interface.nodes[node_id].get("user", {})
        return user_dict.get("shortName", f"Node_{node_id}")
    return f"Node_{node_id}"

def log_message(node_id, text, is_emergency=False, reply_to=None, direct=False, channel_idx=None):
    if node_id != "WebUI":
        display_id = f"{get_node_shortname(node_id)} ({node_id})"
    else:
        display_id = "WebUI"
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    entry = {
        "timestamp": timestamp,
        "node": display_id,
        "node_id": None if node_id == "WebUI" else node_id,
        "message": text,
        "emergency": is_emergency,
        "reply_to": reply_to,
        "direct": direct,
        "channel_idx": channel_idx
    }
    messages.append(entry)
    if len(messages) > 100:
        messages.pop(0)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as logf:
            logf.write(f"{timestamp} | {display_id} | EMERGENCY={is_emergency} | {text}\n")
    except Exception as e:
        print(f"⚠️ Could not write to {LOG_FILE}: {e}")
    save_archive()
    return entry

def split_message(text):
    if not text:
        return []
    return [text[i: i + MAX_CHUNK_SIZE] for i in range(0, len(text), MAX_CHUNK_SIZE)][:MAX_CHUNKS]

def send_broadcast_chunks(interface, text, channelIndex):
    dprint(f"send_broadcast_chunks: text='{text}', channelIndex={channelIndex}")
    info_print(f"[Info] Sending broadcast on channel {channelIndex} → '{text}'")
    if interface is None:
        print("❌ Cannot send broadcast: interface is None.")
        return
    if not text:
        return
    chunks = split_message(text)
    for i, chunk in enumerate(chunks):
        try:
            interface.sendText(chunk, destinationId=BROADCAST_ADDR, channelIndex=channelIndex, wantAck=True)
            time.sleep(CHUNK_DELAY)
        except Exception as e:
            print(f"❌ Error sending broadcast chunk: {e}")
            # Check both errno and winerror for known connection errors
            error_code = getattr(e, 'errno', None) or getattr(e, 'winerror', None)
            if error_code in (10053, 10054, 10060):
                reset_event.set()
            break
        else:
            info_print(f"[Info] Successfully sent chunk {i+1}/{len(chunks)} on ch={channelIndex}.")

def send_direct_chunks(interface, text, destinationId):
    dprint(f"send_direct_chunks: text='{text}', destId={destinationId}")
    info_print(f"[Info] Sending direct message to node {destinationId} => '{text}'")
    if interface is None:
        print("❌ Cannot send direct message: interface is None.")
        return
    if not text:
        return
    ephemeral_ok = hasattr(interface, "sendDirectText")
    chunks = split_message(text)
    for i, chunk in enumerate(chunks):
        try:
            if ephemeral_ok:
                interface.sendDirectText(destinationId, chunk, wantAck=True)
            else:
                interface.sendText(chunk, destinationId=destinationId, wantAck=True)
            time.sleep(CHUNK_DELAY)
        except Exception as e:
            print(f"❌ Error sending direct chunk: {e}")
            error_code = getattr(e, 'errno', None) or getattr(e, 'winerror', None)
            if error_code in (10053, 10054, 10060):
                reset_event.set()
            break
        else:
            info_print(f"[Info] Direct chunk {i+1}/{len(chunks)} to {destinationId} sent.")

def send_to_lmstudio(user_message):
    dprint(f"send_to_lmstudio: user_message='{user_message}'")
    info_print("[Info] Routing user message to LMStudio...")
    payload = {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message}
        ],
        "max_tokens": MAX_RESPONSE_LENGTH
    }
    try:
        response = requests.post(LMSTUDIO_URL, json=payload, timeout=LMSTUDIO_TIMEOUT)
        if response.status_code == 200:
            j = response.json()
            dprint(f"LMStudio raw => {j}")
            ai_resp = (
                j.get("choices", [{}])[0]
                 .get("message", {})
                 .get("content", "🤖 [No response]")
            )
            return ai_resp[:MAX_RESPONSE_LENGTH]
        else:
            print(f"⚠️ LMStudio error: {response.status_code} - {response.text}")
            return None
    except Exception as e:
        print(f"⚠️ LMStudio request failed: {e}")
        return None

def send_to_openai(user_message):
    dprint(f"send_to_openai: user_message='{user_message}'")
    info_print("[Info] Routing user message to OpenAI...")
    if not OPENAI_API_KEY:
        print("⚠️ No OpenAI API key provided.")
        return None
    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {OPENAI_API_KEY}"
    }
    payload = {
        "model": OPENAI_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message}
        ],
        "max_tokens": MAX_RESPONSE_LENGTH
    }
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=OPENAI_TIMEOUT)
        if r.status_code == 200:
            jr = r.json()
            dprint(f"OpenAI raw => {jr}")
            content = (
                jr.get("choices", [{}])[0]
                  .get("message", {})
                  .get("content", "🤖 [No response]")
            )
            return content[:MAX_RESPONSE_LENGTH]
        else:
            print(f"⚠️ OpenAI error: {r.status_code} => {r.text}")
            return None
    except Exception as e:
        print(f"⚠️ OpenAI request failed: {e}")
        return None

def send_to_ollama(user_message):
    dprint(f"send_to_ollama: user_message='{user_message}'")
    info_print("[Info] Routing user message to Ollama...")
    # Normalize text for non-ASCII characters using unidecode
    user_message = unidecode(user_message)
    combined_prompt = f"{SYSTEM_PROMPT}\n{user_message}"
    payload = {
        "prompt": combined_prompt,
        "model": OLLAMA_MODEL,
        "stream": False  # Added to disable streaming responses
    }
    try:
        r = requests.post(OLLAMA_URL, json=payload, timeout=OLLAMA_TIMEOUT)
        if r.status_code == 200:
            jr = r.json()
            dprint(f"Ollama raw => {jr}")
            return jr.get("response", "🤖 [No response]")[:MAX_RESPONSE_LENGTH]
        else:
            print(f"⚠️ Ollama error: {r.status_code} => {r.text}")
            return None
    except Exception as e:
        print(f"⚠️ Ollama request failed: {e}")
        return None

def send_to_home_assistant(user_message):
    dprint(f"send_to_home_assistant: user_message='{user_message}'")
    info_print("[Info] Routing user message to Home Assistant...")
    if not HOME_ASSISTANT_URL:
        return None
    headers = {"Content-Type": "application/json"}
    if HOME_ASSISTANT_TOKEN:
        headers["Authorization"] = f"Bearer {HOME_ASSISTANT_TOKEN}"
    payload = {"text": user_message}
    try:
        r = requests.post(HOME_ASSISTANT_URL, json=payload, headers=headers, timeout=HOME_ASSISTANT_TIMEOUT)
        if r.status_code == 200:
            data = r.json()
            dprint(f"HA raw => {data}")
            speech = data.get("response", {}).get("speech", {})
            answer = speech.get("plain", {}).get("speech")
            if answer:
                return answer[:MAX_RESPONSE_LENGTH]
            return "🤖 [No response from Home Assistant]"
        else:
            print(f"⚠️ HA error: {r.status_code} => {r.text}")
            return None
    except Exception as e:
        print(f"⚠️ HA request failed: {e}")
        return None

def get_ai_response(prompt):
    if AI_PROVIDER == "lmstudio":
        return send_to_lmstudio(prompt)
    elif AI_PROVIDER == "openai":
        return send_to_openai(prompt)
    elif AI_PROVIDER == "ollama":
        return send_to_ollama(prompt)
    elif AI_PROVIDER == "home_assistant":
        return send_to_home_assistant(prompt)
    else:
        print(f"⚠️ Unknown AI provider: {AI_PROVIDER}")
        return None

def send_discord_message(content):
    if not (ENABLE_DISCORD and ENABLE_DISCORD_WEBHOOK and DISCORD_WEBHOOK_URL):
        return
    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"content": content})
    except Exception as e:
        print(f"⚠️ Discord webhook error: {e}")

# -----------------------------
# Revised Emergency Notification Function
# -----------------------------
def send_emergency_notification(node_id, user_msg, lat=None, lon=None, position_time=None):
    info_print("[Info] Sending emergency notification...")

    sn = get_node_shortname(node_id)
    fullname = get_node_fullname(node_id)
    full_msg = f"EMERGENCY from {sn} ({fullname}) [Node {node_id}]:\n"
    if lat is not None and lon is not None:
        maps_url = f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"
        full_msg += f" - Location: {maps_url}\n"
    if position_time:
        full_msg += f" - Last GPS time: {position_time}\n"
    if user_msg:
        full_msg += f" - Message: {user_msg}\n"
    
    # Attempt to send SMS via Twilio if configured.
    try:
        if ENABLE_TWILIO and TWILIO_SID and TWILIO_AUTH_TOKEN and ALERT_PHONE_NUMBER and TWILIO_FROM_NUMBER:
            client = Client(TWILIO_SID, TWILIO_AUTH_TOKEN)
            client.messages.create(
                body=full_msg,
                from_=TWILIO_FROM_NUMBER,
                to=ALERT_PHONE_NUMBER
            )
            print("✅ Emergency SMS sent via Twilio.")
        else:
            print("Twilio not properly configured for SMS.")
    except Exception as e:
        print(f"⚠️ Twilio error: {e}")

    # Attempt to send email via SMTP if configured.
    try:
        if ENABLE_SMTP and SMTP_HOST and SMTP_USER and SMTP_PASS and ALERT_EMAIL_TO:
            if isinstance(ALERT_EMAIL_TO, list):
                email_to = ", ".join(ALERT_EMAIL_TO)
            else:
                email_to = ALERT_EMAIL_TO
            msg = MIMEText(full_msg)
            msg["Subject"] = f"EMERGENCY ALERT from {sn} ({fullname}) [Node {node_id}]"
            msg["From"] = SMTP_USER
            msg["To"] = email_to
            if SMTP_PORT == 465:
                s = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT)
            else:
                s = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
                s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
            s.sendmail(SMTP_USER, email_to, msg.as_string())
            s.quit()
            print("✅ Emergency email sent via SMTP.")
        else:
            print("SMTP not properly configured for email alerts.")
    except Exception as e:
        print(f"⚠️ SMTP error: {e}")

    # Attempt to post emergency alert to Discord if enabled.
    try:
        if DISCORD_SEND_EMERGENCY and ENABLE_DISCORD and DISCORD_WEBHOOK_URL:
            requests.post(DISCORD_WEBHOOK_URL, json={"content": full_msg})
            print("✅ Emergency alert posted to Discord.")
        else:
            print("Discord emergency notifications disabled or not configured.")
    except Exception as e:
        print(f"⚠️ Discord webhook error: {e}")

# -----------------------------
# Helper: Validate/Strip PIN (for Home Assistant)
# -----------------------------
def pin_is_valid(text):
    lower = text.lower()
    if "pin=" not in lower:
        return False
    idx = lower.find("pin=") + 4
    candidate = lower[idx:idx+4]
    return (candidate == HOME_ASSISTANT_SECURE_PIN.lower())

def strip_pin(text):
    lower = text.lower()
    idx = lower.find("pin=")
    if idx == -1:
        return text
    return text[:idx].strip() + " " + text[idx+8:].strip()

def route_message_text(user_message, channel_idx):
    if HOME_ASSISTANT_ENABLED and channel_idx == HOME_ASSISTANT_CHANNEL_INDEX:
        info_print("[Info] Routing to Home Assistant channel.")
        if HOME_ASSISTANT_ENABLE_PIN:
            if not pin_is_valid(user_message):
                return "Security code missing/invalid. Format: 'PIN=XXXX your msg'"
            user_message = strip_pin(user_message)
        ha_response = send_to_home_assistant(user_message)
        return ha_response if ha_response else "🤖 [No response from Home Assistant]"
    else:
        info_print(f"[Info] Using default AI provider: {AI_PROVIDER}")
        resp = get_ai_response(user_message)
        return resp if resp else "🤖 [No AI response]"

# -----------------------------
# Revised Command Handler (Case-Insensitive)
# -----------------------------
def handle_command(cmd, full_text, sender_id):
    cmd = cmd.lower()
    dprint(f"handle_command => cmd='{cmd}', full_text='{full_text}', sender_id={sender_id}")
    if cmd == "/about":
        return "Meshtastic-Controller Off Grid Chat - By: WWW.NerdsCorp.NET"
    elif cmd == "/time":
        now = datetime.now(timezone_obj)
        return f"Current time in {timezone_str}: {now.strftime('%Y-%m-%d %H:%M:%S')}"
    elif cmd in ["/ai", "/bot", "/query", "/data"]:
        user_prompt = full_text[len(cmd):].strip()
        if AI_PROVIDER == "home_assistant" and HOME_ASSISTANT_ENABLE_PIN:
            if not pin_is_valid(user_prompt):
                return "Security code missing or invalid. Use 'PIN=XXXX'"
            user_prompt = strip_pin(user_prompt)
        ai_answer = get_ai_response(user_prompt)
        return ai_answer if ai_answer else "🤖 [No AI response]"
    elif cmd == "/whereami":
        lat, lon, tstamp = get_node_location(sender_id)
        sn = get_node_shortname(sender_id)
        if lat is None or lon is None:
            return f"🤖 Sorry {sn}, I have no GPS fix for your node."
        tstr = str(tstamp) if tstamp else "Unknown"
        return f"Node {sn} GPS: {lat}, {lon} (time: {tstr})"
    elif cmd in ["/emergency", "/911"]:
        lat, lon, tstamp = get_node_location(sender_id)
        user_msg = full_text[len(cmd):].strip()
        send_emergency_notification(sender_id, user_msg, lat, lon, tstamp)
        log_message(sender_id, f"EMERGENCY TRIGGERED: {full_text}", is_emergency=True)
        return "🚨 Emergency alert sent. Stay safe."
    elif cmd == "/test":
        sn = get_node_shortname(sender_id)
        return f"Hello {sn}! Received {LOCAL_LOCATION_STRING} by {AI_NODE_NAME}."
    elif cmd == "/help":
        built_in = ["/about", "/query", "/whereami", "/emergency", "/911", "/test", "/motd"]
        custom_cmds = [c.get("command") for c in commands_config.get("commands",[])]
        return "Commands:\n" + ", ".join(built_in + custom_cmds)
    elif cmd == "/motd":
        return motd_content
    elif cmd == "/sms":
        parts = full_text.split(" ", 2)
        if len(parts) < 3:
            return "Invalid syntax. Use: /sms <phone_number> <message>"
        phone_number = parts[1]
        message_text = parts[2]
        try:
            client = Client(TWILIO_SID, TWILIO_AUTH_TOKEN)
            client.messages.create(
                body=message_text,
                from_=TWILIO_FROM_NUMBER,
                to=phone_number
            )
            print(f"✅ SMS sent to {phone_number}")
            return "SMS sent successfully."
        except Exception as e:
            print(f"⚠️ Failed to send SMS: {e}")
            return "Failed to send SMS."
    for c in commands_config.get("commands", []):
        if c.get("command").lower() == cmd:
            if "ai_prompt" in c:
                user_input = full_text[len(cmd):].strip()
                custom_text = c["ai_prompt"].replace("{user_input}", user_input)
                if AI_PROVIDER == "home_assistant" and HOME_ASSISTANT_ENABLE_PIN:
                    if not pin_is_valid(custom_text):
                        return "Security code missing or invalid."
                    custom_text = strip_pin(custom_text)
                ans = get_ai_response(custom_text)
                return ans if ans else "🤖 [No AI response]"
            elif "response" in c:
                return c["response"]
            return "No configured response for this command."
    return None

def parse_incoming_text(text, sender_id, is_direct, channel_idx):
    dprint(f"parse_incoming_text => text='{text}' is_direct={is_direct} channel={channel_idx}")
    info_print(f"[Info] Received from node {sender_id} (direct={is_direct}, ch={channel_idx}) => '{text}'")
    text = text.strip()
    if not text:
        return None
    if is_direct and not config.get("reply_in_directs", True):
        return None
    if (not is_direct) and channel_idx != HOME_ASSISTANT_CHANNEL_INDEX and not config.get("reply_in_channels", True):
        return None
    if text.startswith("/"):
        cmd = text.split()[0]
        resp = handle_command(cmd, text, sender_id)
        return resp
    if is_direct:
        return get_ai_response(text)
    if HOME_ASSISTANT_ENABLED and channel_idx == HOME_ASSISTANT_CHANNEL_INDEX:
        return route_message_text(text, channel_idx)
    return None

def on_receive(packet=None, interface=None, **kwargs):
    dprint(f"on_receive => packet={packet}")
    if not packet or 'decoded' not in packet:
        dprint("No decoded packet => ignoring.")
        return
    if packet['decoded']['portnum'] != 'TEXT_MESSAGE_APP':
        dprint("Not TEXT_MESSAGE_APP => ignoring.")
        return
    try:
        text_raw = packet['decoded']['payload']
        text = text_raw.decode('utf-8', errors='replace')
        sender_node = packet.get('fromId', None)
        raw_to = packet.get('toId', None)
        to_node_int = parse_node_id(raw_to)
        ch_idx = packet.get('channel', 0)
        dprint(f"[MSG] from {sender_node} to {raw_to} (ch={ch_idx}): {text}")
        entry = log_message(sender_node, text, direct=(to_node_int != BROADCAST_ADDR), channel_idx=(None if to_node_int != BROADCAST_ADDR else ch_idx))
        global lastDMNode, lastChannelIndex
        if to_node_int != BROADCAST_ADDR:
            lastDMNode = sender_node
        else:
            lastChannelIndex = ch_idx

        # Only forward messages on the configured Discord inbound channel to Discord.
        if ENABLE_DISCORD and DISCORD_SEND_ALL and DISCORD_INBOUND_CHANNEL_INDEX is not None and ch_idx == DISCORD_INBOUND_CHANNEL_INDEX:
            sender_info = f"{get_node_shortname(sender_node)} ({sender_node})"
            disc_content = f"**{sender_info}**: {text}"
            send_discord_message(disc_content)

        my_node_num = None
        if FORCE_NODE_NUM is not None:
            my_node_num = FORCE_NODE_NUM
        else:
            if hasattr(interface, "myNode") and interface.myNode:
                my_node_num = interface.myNode.nodeNum
            elif hasattr(interface, "localNode") and interface.localNode:
                my_node_num = interface.localNode.nodeNum
        is_direct = False
        if to_node_int == BROADCAST_ADDR:
            is_direct = False
        elif my_node_num is not None and to_node_int == my_node_num:
            is_direct = True
        else:
            is_direct = (my_node_num == to_node_int)
        resp = parse_incoming_text(text, sender_node, is_direct, ch_idx)
        if resp:
            info_print("[Info] Wait 10s before responding to reduce collisions.")
            time.sleep(10)
            log_message(AI_NODE_NAME, resp, reply_to=entry['timestamp'])
            # If message originated on Discord inbound channel, also send the AI response back to Discord.
            if ENABLE_DISCORD and DISCORD_SEND_AI and DISCORD_INBOUND_CHANNEL_INDEX is not None and ch_idx == DISCORD_INBOUND_CHANNEL_INDEX:
                disc_msg = f"🤖 **{AI_NODE_NAME}**: {resp}"
                send_discord_message(disc_msg)
            if is_direct:
                send_direct_chunks(interface, resp, sender_node)
            else:
                send_broadcast_chunks(interface, resp, ch_idx)
    except OSError as e:
        error_code = getattr(e, 'errno', None) or getattr(e, 'winerror', None)
        print(f"⚠️ OSError detected in on_receive: {e} (error code: {error_code})")
        if error_code in (10053, 10054, 10060):
            print("⚠️ Connection error detected. Restarting interface...")
            global connection_status
            connection_status = "Disconnected"
            reset_event.set()
        # Instead of re-raising, simply return to prevent thread crash
        return
    except Exception as e:
        print(f"⚠️ Unexpected error in on_receive: {e}")
        return

@app.route("/messages", methods=["GET"])
def get_messages_api():
    dprint("GET /messages => returning current messages")
    return jsonify(messages)

@app.route("/nodes", methods=["GET"])
def get_nodes_api():
    node_list = []
    if interface and hasattr(interface, "nodes"):
        for nid in interface.nodes:
            sn = get_node_shortname(nid)
            node_list.append({"id": nid, "shortName": sn})
    return jsonify(node_list)

@app.route("/connection_status", methods=["GET"], endpoint="connection_status_info")
def connection_status_info():
    return jsonify({"status": connection_status, "error": last_error_message})

@app.route("/logs", methods=["GET"])
def logs():
    uptime = datetime.now(timezone.utc) - server_start_time
    uptime_str = str(uptime).split('.')[0]
    log_entries = '\n'.join(script_logs)
    html = """
    <html>
    <head>
      <meta charset="utf-8">
      <title>Meshtastic Controller Logs</title>
      <style>
        body {
          background: #000;
          color: #fff;
          font-family: Arial, sans-serif;
          padding: 20px;
        }
        h1, h2 {
          color: var(--theme-color);
        }
        pre {
          background: #111;
          padding: 10px;
          border: 1px solid var(--theme-color);
          overflow-x: auto;
        }
        .summary {
          margin-bottom: 20px;
        }
        a {
          color: var(--theme-color);
          text-decoration: none;
        }
      </style>
    </head>
    <body>
      <h1>Script Logs</h1>
      <div class="summary">
        <p><strong>Uptime:</strong> {{ uptime_str }}</p>
        <p><strong>Restarts (since current launch):</strong> {{ restart_count }}</p>
        <p><a href="/dashboard">Back to Dashboard</a></p>
      </div>
      <h2>Log Entries</h2>
      <pre>{{ log_entries | e }}</pre>
    </body>
    </html>
    """
    return render_template_string(html,
        uptime_str=uptime_str,
        restart_count=restart_count,
        log_entries=log_entries
    )

# -----------------------------
# Revised Discord Webhook Route for Inbound Messages
# -----------------------------
@app.route("/discord_webhook", methods=["POST"])
def discord_webhook():
    if not DISCORD_RECEIVE_ENABLED:
        return jsonify({"status": "disabled", "message": "Discord receive is disabled"}), 200
    data = request.json
    if not data:
        return jsonify({"status": "error", "message": "No JSON payload provided"}), 400

    # Extract the username (default if not provided)
    username = data.get("username", "DiscordUser")
    channel_index = DISCORD_INBOUND_CHANNEL_INDEX
    message_text = data.get("message")
    if message_text is None:
        return jsonify({"status": "error", "message": "Missing message"}), 400

    # Prepend username to the message
    formatted_message = f"**{username}**: {message_text}"

    try:
        log_message("Discord", formatted_message, direct=False, channel_idx=int(channel_index))
        if interface is None:
            print("❌ Cannot route Discord message: interface is None.")
        else:
            send_broadcast_chunks(interface, formatted_message, int(channel_index))
        print(f"✅ Routed Discord message back on channel {channel_index}")
        return jsonify({"status": "sent", "channel_index": channel_index, "message": formatted_message})
    except Exception as e:
        print(f"⚠️ Discord webhook error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

# -----------------------------
# New Twilio SMS Webhook Route for Inbound SMS
# -----------------------------
@app.route("/twilio_webhook", methods=["POST"])
def twilio_webhook():
    sms_body = request.form.get("Body")
    from_number = request.form.get("From")
    if not sms_body:
        return "No SMS body received", 400
    target = config.get("twilio_inbound_target", "channel")
    if target == "channel":
        channel_index = config.get("twilio_inbound_channel_index")
        if channel_index is None:
            return "No inbound channel index configured", 400
        log_message("Twilio", f"From {from_number}: {sms_body}", direct=False, channel_idx=int(channel_index))
        send_broadcast_chunks(interface, sms_body, int(channel_index))
        print(f"✅ Routed incoming SMS from {from_number} to channel {channel_index}")
    elif target == "node":
        node_id = config.get("twilio_inbound_node")
        if node_id is None:
            return "No inbound node configured", 400
        log_message("Twilio", f"From {from_number}: {sms_body}", direct=True)
        send_direct_chunks(interface, sms_body, node_id)
        print(f"✅ Routed incoming SMS from {from_number} to node {node_id}")
    else:
        return "Invalid twilio_inbound_target config", 400
    return "SMS processed", 200

@app.route("/dashboard", methods=["GET"])
def dashboard():
    channel_names = config.get("channel_names", {})
    html = """
<head>
  <meta charset="UTF-8" />
  <title>Meshtastic Controller Dashboard</title>
  <style>
    :root {
      --theme-color: #ffa500;
    }
    body {
      background: #000;
      color: #fff;
      font-family: sans-serif;
      margin: 0;
      padding-top: 40px;
      transition: filter 0.5s linear;
    }
    #connectionStatus {
      position: fixed;
      top: 0;
      left: 0;
      width: 100%;
      z-index: 350;
      text-align: center;
      padding: 6px;
      font-size: 14px;
      font-weight: bold;
    }
    .header-buttons {
      position: fixed;
      top: 0;
      right: 0;
      z-index: 400;
    }
    .header-buttons a {
      background: var(--theme-color);
      color: #000;
      padding: 8px 12px;
      margin: 5px;
      text-decoration: none;
      border-radius: 4px;
      font-weight: bold;
    }
    .header-buttons a:hover {
      background: #ffc84a;
    }
    #ticker {
      position: fixed;
      top: 70px;
      background: #111;
      color: var(--theme-color);
      white-space: nowrap;
      overflow: hidden;
      width: 100%;
      padding: 8px 0;
      z-index: 250;
      font-size: 32px;
      display: none;
    }
    .lcars-panel {
      background: #111;
      padding: 20px;
      border: 2px solid var(--theme-color);
      border-radius: 10px;
      margin-bottom: 20px;
    }
    .lcars-panel h2 {
      color: var(--theme-color);
      font-size: 20px;
      margin: 0 0 12px;
    }
    #sendForm {
      margin: 20px;
      padding: 20px;
    }
    .three-col {
      display: flex;
      gap: 20px;
      margin: 20px;
      height: calc(100vh - 220px);
    }
    .three-col .col {
      flex: 1;
      overflow-y: auto;
    }
    .message {
      border: 1px solid var(--theme-color);
      border-radius: 4px;
      padding: 8px 10px;
      margin-bottom: 8px;
      background: #222;
      word-wrap: break-word;
    }
    .message.outgoing {
      background: #333;
    }
    .message.newMessage {
      border-color: #00ff00;
    }
    .timestamp {
      font-size: 0.75em;
      color: #ccc;
      margin-bottom: 5px;
    }
    .btn {
      background: var(--theme-color);
      color: #000;
      border: none;
      padding: 4px 8px;
      font-size: 0.8em;
      font-weight: bold;
      border-radius: 4px;
      cursor: pointer;
      margin-left: 8px;
    }
    .btn:hover {
      background: #ffc84a;
    }
    .switch {
      position: relative;
      display: inline-block;
      width: 50px;
      height: 28px;
    }
    .switch input {
      display: none;
    }
    .slider {
      position: absolute;
      cursor: pointer;
      top: 0;
      left: 0;
      right: 0;
      bottom: 0;
      background-color: #555;
      transition: 0.4s;
      border-radius: 34px;
    }
    .slider:before {
      position: absolute;
      content: "";
      height: 20px;
      width: 20px;
      left: 4px;
      bottom: 4px;
      background-color: #fff;
      transition: 0.4s;
      border-radius: 50%;
    }
    input:checked + .slider {
      background-color: #2196F3;
    }
    input:checked + .slider:before {
      transform: translateX(22px);
    }
    #charCounter {
      font-size: 0.85em;
      color: #ccc;
      text-align: right;
      margin-top: 5px;
    }
    .settings-toggle {
      background: var(--theme-color);
      color: #000;
      padding: 12px;
      text-align: center;
      cursor: pointer;
      position: fixed;
      bottom: 0;
      left: 0;
      width: 100%;
      z-index: 300;
      border-top: 2px solid #fff;
      font-weight: bold;
    }
    .settings-panel {
      background: #111;
      margin: 20px;
      padding: 20px;
      border: 2px solid var(--theme-color);
      border-radius: 10px;
      position: fixed;
      bottom: 50px;
      left: 20px;
      right: 20px;
      z-index: 300;
      display: none;
    }
    .settings-panel label {
      font-weight: bold;
      margin-right: 8px;
    }
    .settings-panel input[type="color"],
    .settings-panel input[type="text"],
    .settings-panel input[type="range"] {
      margin: 8px 0 16px;
    }
    textarea,
    select,
    input[type="text"] {
      font-family: sans-serif;
      font-size: 1em;
      padding: 6px;
      border-radius: 4px;
      border: 1px solid #333;
      background: #222;
      color: #fff;
      width: 100%;
      box-sizing: border-box;
    }
    textarea {
      resize: vertical;
    }
  </style>
  <script>
    // Global variables to store the last DM and channel targets
    var lastDMTarget = null;
    var lastChannelTarget = null;

    // Reply using the last direct message target
    function replyToLastDM() {
      if (lastDMTarget !== null) {
        replyToMessage('direct', lastDMTarget);
      } else {
        alert("No direct message target available.");
      }
    }

    // Reply using the last broadcast channel target
    function replyToLastChannel() {
      if (lastChannelTarget !== null) {
        replyToMessage('broadcast', lastChannelTarget);
      } else {
        alert("No broadcast channel target available.");
      }
    }

    // Set the incoming sound source and save it in localStorage
    function setIncomingSound(url) {
      var soundElem = document.getElementById('incomingSound');
      if (soundElem) {
        soundElem.src = url;
        localStorage.setItem("incomingSoundURL", url);
      }
    }

    // On load, retrieve stored incoming sound URL
    window.addEventListener("load", function() {
      var storedSoundURL = localStorage.getItem("incomingSoundURL");
      if (storedSoundURL) {
        document.getElementById("soundURL").value = storedSoundURL;
        setIncomingSound(storedSoundURL);
      }
    });

    var hueRotateInterval = null;
    var currentHue = 0;
    function applyThemeColor(color) {
      document.documentElement.style.setProperty("--theme-color", color);
      localStorage.setItem("uiThemeColor", color);
    }
    function startHueRotate(speed) {
      var degPerSec = 360 / speed;
      if (hueRotateInterval) clearInterval(hueRotateInterval);
      hueRotateInterval = setInterval(function(){
        currentHue = (currentHue + degPerSec * 0.1) % 360;
        document.body.style.filter = "hue-rotate(" + currentHue + "deg)";
      }, 100);
      localStorage.setItem("hueRotateSpeed", speed);
    }
    function stopHueRotate() {
      if (hueRotateInterval) {
        clearInterval(hueRotateInterval);
        hueRotateInterval = null;
      }
      document.body.style.filter = "none";
    }
    function toggleHueRotate(enabled, speed) {
      if (enabled) {
        startHueRotate(speed);
        localStorage.setItem("hueRotateEnabled", "true");
      } else {
        stopHueRotate();
        localStorage.setItem("hueRotateEnabled", "false");
      }
    }
    function toggleSettingsPanel() {
      var panel = document.getElementById("settingsPanel");
      var toggleBtn = document.getElementById("settingsToggle");
      if(panel.style.display === "none" || panel.style.display === "") {
        panel.style.display = "block";
        toggleBtn.textContent = "Hide UI Settings";
      } else {
        panel.style.display = "none";
        toggleBtn.textContent = "Show UI Settings";
      }
    }
    async function fetchMessagesAndNodes() {
      try {
        var msgResp = await fetch("/messages");
        var msgData = await msgResp.json();
        updateMessagesUI(msgData);
        var nodeResp = await fetch("/nodes");
        var nodeData = await nodeResp.json();
        updateNodesUI(nodeData);
      } catch (e) {
        console.error("Error fetching data:", e);
      }
    }
    function updateMessagesUI(messages) {
      messages = messages.sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp));

      // Update global targets from the most recent messages
      lastDMTarget = null;
      lastChannelTarget = null;
      for (var i = 0; i < messages.length; i++) {
        var m = messages[i];
        if (m.direct && m.node_id && lastDMTarget === null) {
          lastDMTarget = m.node_id;
        }
        if (!m.direct && m.channel_idx !== null && lastChannelTarget === null) {
          lastChannelTarget = m.channel_idx;
        }
      }

      var channelDiv = document.getElementById("channelDiv");
      var dmMessagesDiv = document.getElementById("dmMessagesDiv");
      var discordDiv = document.getElementById("discordMessagesDiv");
      if (channelDiv) channelDiv.innerHTML = "";
      if (dmMessagesDiv) dmMessagesDiv.innerHTML = "";
      if (discordDiv) discordDiv.innerHTML = "";
      var now = new Date().getTime();
      messages.forEach(function(m) {
        var msgTime = new Date(m.timestamp).getTime();
        var wrap = document.createElement("div");
        wrap.className = "message" + (m.emergency ? " emergency" : "");
        if (m.node === "WebUI") {
          wrap.classList.add("outgoing");
        }
        if (m.node !== "WebUI" && m.node !== "AI-Bot" && m.node !== "Home Assistant") {
          var replyBtn = document.createElement("button");
          replyBtn.className = "btn";
          replyBtn.textContent = "Reply";
          if (m.direct && m.node_id) {
            replyBtn.onclick = function(){ replyToMessage('direct', m.node_id); };
          } else if (!m.direct && m.channel_idx !== null) {
            replyBtn.onclick = function(){ replyToMessage('broadcast', m.channel_idx); };
          }
          wrap.appendChild(replyBtn);
        }
        if (now - msgTime < 7200000) {
          wrap.classList.add("newMessage");
        }
        var icon = "";
        if (m.direct) {
          icon = (m.node === "WebUI") ? "📤" : "📥";
        } else {
          icon = (m.node === "WebUI") ? "📣" : "📢";
        }
        wrap.innerHTML += "<div class='timestamp'>" + icon + " " + m.timestamp + " | " + m.node + "</div>" +
                           "<div>" + m.message + "</div>";
        // Place messages based on the node field
        if (m.node.indexOf("Discord") !== -1) {
          if (discordDiv) discordDiv.appendChild(wrap);
        } else if (m.direct) {
          if (dmMessagesDiv) dmMessagesDiv.appendChild(wrap);
        } else {
          if (channelDiv) channelDiv.appendChild(wrap);
        }
      });
    }

    function updateNodesUI(nodes) {
      var nodeListDiv = document.getElementById("nodeListDiv");
      var destSelect = document.getElementById("destNode");
      if(nodeListDiv) nodeListDiv.innerHTML = "";
      if(destSelect) destSelect.innerHTML = "<option value=''>--Select Node--</option>";
      nodes.forEach(function(n) {
        var d = document.createElement("div");
        d.className = "nodeItem";
        d.textContent = n.shortName + " (" + n.id + ")";
        var dmBtn = document.createElement("button");
        dmBtn.className = "btn";
        dmBtn.textContent = "DM";
        dmBtn.onclick = function(){ dmToNode(n.id, n.shortName); };
        d.appendChild(dmBtn);
        if(nodeListDiv) nodeListDiv.appendChild(d);
        if(destSelect) {
          var opt = document.createElement("option");
          opt.value = n.id;
          opt.text = n.shortName + " (" + n.id + ")";
          destSelect.appendChild(opt);
        }
      });
    }
    function pollStatus() {
      fetch("/connection_status")
        .then(response => response.json())
        .then(data => {
          var statusDiv = document.getElementById("connectionStatus");
          if(data.status !== "Connected") {
            statusDiv.style.background = "red";
            statusDiv.style.height = "40px";
            statusDiv.textContent = "Connection Error: " + data.error;
          } else {
            statusDiv.style.background = "green";
            statusDiv.style.height = "20px";
            statusDiv.textContent = "Connected";
          }
        })
        .catch(err => console.error("Error fetching connection status:", err));
    }
    setInterval(pollStatus, 5000);
    function onPageLoad() {
      setInterval(fetchMessagesAndNodes, 10000);
      fetchMessagesAndNodes();
    }

    var function_reply_js = `
    function replyToMessage(mode, target) {
      if (mode === 'direct') {
        document.getElementById('modeSwitch').checked = true;
        toggleMode();
        document.getElementById('destNode').value = target;
        var destText = document.getElementById('destNode').options[document.getElementById('destNode').selectedIndex].text;
        var shortName = destText.split(" (")[0];
        document.getElementById('messageBox').value = "Reply to @" + shortName + ": ";
      } else if (mode === 'broadcast') {
        document.getElementById('modeSwitch').checked = false;
        toggleMode();
        document.getElementById('channelSel').value = target;
        document.getElementById('messageBox').value = "Reply in Channel " + target + ": ";
      }
    }
    function dmToNode(nodeId, shortName) {
      document.getElementById('modeSwitch').checked = true;
      toggleMode();
      var destSelect = document.getElementById('destNode');
      for (var i = 0; i < destSelect.options.length; i++) {
        if (destSelect.options[i].value === nodeId) {
          destSelect.selectedIndex = i;
          break;
        }
      }
      document.getElementById('messageBox').value = "@" + shortName + ": ";
    }
    `;
    eval(function_reply_js);

    function toggleMode() {
      var isDM = document.getElementById('modeSwitch').checked;
      if (isDM) {
        document.getElementById('dmField').style.display = 'block';
        document.getElementById('channelField').style.display = 'none';
        document.getElementById('modeLabel').textContent = "Direct";
      } else {
        document.getElementById('dmField').style.display = 'none';
        document.getElementById('channelField').style.display = 'block';
        document.getElementById('modeLabel').textContent = "Broadcast";
      }
    }

    window.addEventListener("load", function(){
      document.getElementById('modeSwitch').addEventListener('change', toggleMode);
      document.getElementById('messageBox').addEventListener('input', updateCharCounter);
      onPageLoad();

      // Restore theme color from localStorage if present
      var storedColor = localStorage.getItem("uiThemeColor");
      if(storedColor) applyThemeColor(storedColor);

      // Restore hue rotate setting
      var hueEnabled = localStorage.getItem("hueRotateEnabled") === "true";
      var hueSpeed = parseFloat(localStorage.getItem("hueRotateSpeed")) || 10;
      document.getElementById('hueRotateEnabled').checked = hueEnabled;
      document.getElementById('hueRotateSpeed').value = hueSpeed;
      toggleHueRotate(hueEnabled, hueSpeed);
    });

    function updateCharCounter() {
      var text = document.getElementById('messageBox').value;
      var count = text.length;
      var chunks = Math.ceil(count / 200);
      if(chunks > 5) { chunks = 5; }
      document.getElementById('charCounter').textContent = "Characters: " + count + "/1000, Chunks: " + chunks + "/5";
    }

    function sendMessage() {
    var message = document.getElementById('messageBox').value;
    var isDM = document.getElementById('modeSwitch').checked;
    var data = new FormData();
    data.append('message', message);

    if (isDM) {
        var destNode = document.getElementById('destNode').value;
        data.append('destination_node', destNode);
    } else {
        var channelIdx = document.getElementById('channelSel').value;
        data.append('channel_index', channelIdx);
    }

    fetch('/ui_send', {
        method: 'POST',
        body: data
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            document.getElementById('messageBox').value = '';
            document.getElementById('charCounter').textContent = "Characters: 0/1000, Chunks: 0/5";
            alert("Message sent!");
        } else {
            alert("Error: " + (data.error || "Unknown error"));
        }
    })
    .catch(error => alert("Network error: " + error));
}
    
  </script>
</head>
<body>
  <div id="connectionStatus"></div>
  <div class="header-buttons">
    <a href="/instructions">Instructions</a>
    <a href="/config">Edit Config</a>
    <a href="/commands">Edit Commands</a>
    <a href="/logs" target="_blank">Logs</a>
  </div>
  <div id="flashDiv" style="display:none;">NEW MESSAGE!</div>
  <div id="ticker"><p></p></div>
  <audio id="beepAudio" src="data:audio/wav;base64,UklGRgAAAABXQVZFZm10IBAAAAABAAEAQB8AAIA+/+cAAACAAAACAAACAAACAAACAAAACAAACAAAAAAAAAAA"></audio>
  <audio id="incomingSound"></audio>

  <div style="display: flex; gap: 20px; margin: 20px;">
  <div class="lcars-panel" id="sendForm" style="flex: 0 0 40%; display: flex; flex-direction: column; max-height: 450px; overflow-y: auto;">
  <h2>Send a Message</h2>
  <form method="POST" action="/ui_send" style="flex-grow: 1; display: flex; flex-direction: column;">
    <label>Message Mode:</label>
    <label class="switch">
      <input type="checkbox" id="modeSwitch" />
      <span class="slider round"></span>
    </label>
    <span id="modeLabel">Direct</span>
    <br/><br/>
    <div id="dmField" style="display: none;">
      <label>Destination Node:</label><br/>
      <select id="destNode" name="destination_node">
        <option value="">--Select Node--</option>
      </select><br/><br/>
    </div>
    <div id="channelField" style="display: block;">
      <label>Channel:</label><br/>
      <select id="channelSel" name="channel_index">
        <option value="0">0 - Channel 0</option>
        <option value="1">1 - Channel 1</option>
        <option value="2">2 - Channel 2</option>
        <option value="3">3 - Channel 3</option>
        <option value="4">4 - Channel 4</option>
        <option value="5">5 - Channel 5</option>
        <option value="6">6 - Channel 6</option>
        <option value="7">7 - Channel 7</option>
      </select><br/><br/>
    </div>
    <label>Message:</label><br/>
    <textarea id="messageBox" name="message" rows="3" style="width: 100%;"></textarea>
    <div id="charCounter">Characters: 0/1000, Chunks: 0/5</div>
    <br/>
    <div style="display: flex; flex-wrap: wrap; gap: 10px;">
      <button type="button" onclick="sendMessage()">Send</button>
      <button type="button" onclick="replyToLastDM()">Reply to Last DM</button>
      <button type="button" onclick="replyToLastChannel()">Reply to Last Channel</button>
    </div>
  </form>
</div>

  <div class="lcars-panel" style="flex: 1; max-height: 450px; overflow-y: auto;">
    <h2>Discord Messages</h2>
    <div id="discordMessagesDiv"></div>
  </div>
</div>

<!-- Existing three-column layout remains below -->
<div class="three-col">
  <div class="col">
    <div class="lcars-panel">
      <h2>Channel Messages</h2>
      <div id="channelDiv"></div>
    </div>
  </div>
  <div class="col">
    <div class="lcars-panel">
      <h2>Available Nodes</h2>
      <div id="nodeListDiv"></div>
    </div>
  </div>
  <div class="col">
    <div class="lcars-panel">
      <h2>Direct Messages</h2>
      <div id="dmMessagesDiv"></div>
    </div>
  </div>
</div>

  <div class="settings-toggle" id="settingsToggle" onclick="toggleSettingsPanel()">Show UI Settings</div>
  <div class="settings-panel" id="settingsPanel">
    <h2>UI Settings</h2>
    <label for="uiColorPicker">Theme Color:</label>
    <input type="color" id="uiColorPicker" value="#ffa500" onchange="applyThemeColor(this.value)" />
    <br/>
    <label for="hueRotateEnabled">Enable Hue Rotation:</label>
    <input type="checkbox" id="hueRotateEnabled" onchange="toggleHueRotate(this.checked, parseFloat(document.getElementById('hueRotateSpeed').value))" />
    <br/>
    <label for="hueRotateSpeed">Hue Rotation Speed (seconds per full rotation):</label>
    <input type="range" id="hueRotateSpeed" min="5" max="60" step="0.1" value="10" onchange="if(document.getElementById('hueRotateEnabled').checked){ startHueRotate(parseFloat(this.value)); }" />
    <br/>
    <label for="soundURL">Incoming Message Sound URL:</label>
    <input type="text" id="soundURL" placeholder="/static/sound.mp3" onchange="setIncomingSound(this.value)" />
  </div>
</body>
</html>

"""
    return html

@app.route("/ui_send", methods=["POST"])
def ui_send():
    message = request.form.get("message", "").strip()
    mode = "direct" if request.form.get("destination_node", "") != "" else "broadcast"
    dest_node = request.form.get("destination_node", "").strip() if mode == "direct" else None
    channel_idx = int(request.form.get("channel_index", "0")) if mode == "broadcast" else None

    if not message:
        return jsonify({"success": False, "error": "No message provided."})

    try:
        # Start the sending in a background thread!
        threading.Thread(
            target=async_send_message,
            args=(mode, interface, message, dest_node, channel_idx),
            daemon=True
        ).start()

        # Log as "sent" instantly!
        if mode == "direct" and dest_node:
            dest_info = f"{get_node_shortname(dest_node)} ({dest_node})"
            log_message("WebUI", f"{message} [to: {dest_info}]", direct=True)
            info_print(f"[UI] Direct message to node {dest_info} => '{message}'")
        else:
            log_message("WebUI", f"{message} [to: Broadcast Channel {channel_idx}]", direct=False, channel_idx=channel_idx)
            info_print(f"[UI] Broadcast on channel {channel_idx} => '{message}'")

        return jsonify({"success": True})
    except Exception as e:
        print(f"⚠️ /ui_send error: {e}")
        return jsonify({"success": False, "error": str(e)})

@app.route("/send", methods=["POST"])
def send_message():
    dprint("POST /send => manual JSON send")
    data = request.json
    if not data:
        return jsonify({"status": "error", "message": "No JSON payload"}), 400
    message = data.get("message")
    node_id = data.get("node_id")
    channel_idx = data.get("channel_index", 0)
    direct = data.get("direct", False)
    if not message or node_id is None:
        return jsonify({"status": "error", "message": "Missing 'message' or 'node_id'"}), 400
    try:
        if direct:
            log_message("WebUI", f"{message} [to: {get_node_shortname(node_id)} ({node_id})]", direct=True)
            info_print(f"[Info] Direct send to node {node_id} => '{message}'")
            send_direct_chunks(interface, message, node_id)
            return jsonify({"status": "sent", "to": node_id, "direct": True, "message": message})
        else:
            log_message("WebUI", f"{message} [to: Broadcast Channel {channel_idx}]", direct=False, channel_idx=channel_idx)
            info_print(f"[Info] Broadcast on ch={channel_idx} => '{message}'")
            send_broadcast_chunks(interface, message, channel_idx)
            return jsonify({"status": "sent", "to": f"channel {channel_idx}", "message": message})
    except Exception as e:
        print(f"⚠️ Failed to send: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

def connect_interface():
    global connection_status, last_error_message
    try:
        if USE_WIFI and WIFI_HOST and TCPInterface is not None:
            print(f"Trying TCPInterface to {WIFI_HOST}:{WIFI_PORT} ...")
            connection_status = "Connected"
            last_error_message = ""
            return TCPInterface(hostname=WIFI_HOST, portNumber=WIFI_PORT)
        if USE_MESH_INTERFACE and MESH_INTERFACE_AVAILABLE:
            print("Trying MeshInterface() for ephemeral direct messages ...")
            connection_status = "Connected"
            last_error_message = ""
            return MeshInterface()
        if SERIAL_PORT:
            print(f"Trying SerialInterface on port '{SERIAL_PORT}' ...")
            connection_status = "Connected"
            last_error_message = ""
            return meshtastic.serial_interface.SerialInterface(devPath=SERIAL_PORT)
        else:
            print("Trying SerialInterface (auto-detect) ...")
            connection_status = "Connected"
            last_error_message = ""
            return meshtastic.serial_interface.SerialInterface()
    except Exception as e:
        connection_status = "Disconnected"
        last_error_message = str(e)
        add_script_log(f"Connection error: {e}")
        raise

def thread_excepthook(args):
    logging.error(f"Meshtastic thread error: {args.exc_value}")
    traceback.print_exception(args.exc_type, args.exc_value, args.exc_traceback)
    global connection_status
    connection_status = "Disconnected"
    reset_event.set()

threading.excepthook = thread_excepthook

@app.route("/connection_status", methods=["GET"])
def connection_status_route():
    return jsonify({"status": connection_status, "error": last_error_message})

# Insert start_discord_presence() here
def start_discord_presence():
    if not config.get("enable_discord", False) or not config.get("discord_bot_token"):
        return

    intents = discord.Intents.none()
    class PresenceClient(discord.Client):
        async def on_ready(self):
            print(f"[Discord Presence] Logged in as {self.user}")
            presence_status = config.get("discord_presence_status", "online").lower()
            status = discord.Status.online
            if presence_status == "idle":
                status = discord.Status.idle
            elif presence_status == "dnd":
                status = discord.Status.dnd
            activity_text = config.get("discord_presence_activity", "Meshtastic Controller Online")
            activity = discord.Game(name=activity_text)
            await self.change_presence(status=status, activity=activity)

    def run_presence():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        client = PresenceClient(intents=intents)
        try:
            loop.run_until_complete(client.start(config["discord_bot_token"]))
        except Exception as e:
            print(f"Discord presence client error: {e}")

    threading.Thread(target=run_presence, daemon=True).start()
    
def main():
    global interface, restart_count, server_start_time, reset_event
    server_start_time = server_start_time or datetime.now(timezone.utc)
    restart_count += 1
    add_script_log(f"Server restarted. Restart count: {restart_count}")
    print("Starting Meshtastic Controller Server...")
    load_archive()
    start_discord_presence()
        # Additional startup info:
    if ENABLE_DISCORD:
        print(f"Discord configuration enabled: Inbound channel index: {DISCORD_INBOUND_CHANNEL_INDEX}, Webhook URL is {'set' if DISCORD_WEBHOOK_URL else 'not set'}, Bot Token is {'set' if DISCORD_BOT_TOKEN else 'not set'}, Channel ID is {'set' if DISCORD_CHANNEL_ID else 'not set'}.")
    else:
        print("Discord configuration disabled.")
    if ENABLE_TWILIO:
        if TWILIO_SID and TWILIO_AUTH_TOKEN and ALERT_PHONE_NUMBER and TWILIO_FROM_NUMBER:
            print("Twilio is configured for emergency SMS.")
        else:
            print("Twilio is not properly configured for emergency SMS.")
    else:
        print("Twilio is disabled.")
    if ENABLE_SMTP:
        if SMTP_HOST and SMTP_USER and SMTP_PASS and ALERT_EMAIL_TO:
            print("SMTP is configured for emergency email alerts.")
        else:
            print("SMTP is not properly configured for emergency email alerts.")
    else:
        print("SMTP is disabled.")
    print("Launching Flask in the background on port 5000...")
    api_thread = threading.Thread(
        target=app.run,
        kwargs={"host": "0.0.0.0", "port": 5000, "debug": False},
        daemon=True
    )
    api_thread.start()
    # If Discord polling is configured, start that thread.
    if DISCORD_BOT_TOKEN and DISCORD_CHANNEL_ID:
        threading.Thread(target=poll_discord_channel, daemon=True).start()
    while True:
        try:
            print("---------------------------------------------------")
            print("Attempting to connect to Meshtastic device...")
            try:
                pub.unsubscribe(on_receive, "meshtastic.receive")
            except Exception:
                pass
            try:
                if interface:
                    interface.close()
            except Exception:
                pass
            interface = connect_interface()
            print("Subscribing to on_receive callback...")
            pub.subscribe(on_receive, "meshtastic.receive")
            print(f"AI provider set to: {AI_PROVIDER}")
            if HOME_ASSISTANT_ENABLED:
                print(f"Home Assistant multi-mode is ENABLED. Channel index: {HOME_ASSISTANT_CHANNEL_INDEX}")
                if HOME_ASSISTANT_ENABLE_PIN:
                    print("Home Assistant secure PIN protection is ENABLED.")
            print("Connection successful. Running until error or Ctrl+C.")
            add_script_log("Connection established successfully.")
            # Inner loop: periodically check if a reset has been signaled
            while not reset_event.is_set():
                time.sleep(1)
            raise OSError("Reset event triggered due to connection loss")
        except KeyboardInterrupt:
            print("User interrupted the script. Shutting down.")
            add_script_log("Server shutdown via KeyboardInterrupt.")
            break
        except OSError as e:
            error_code = getattr(e, 'errno', None) or getattr(e, 'winerror', None)
            if error_code in (10053, 10054, 10060):
                print("⚠️ Connection was forcibly closed. Attempting to reconnect...")
                add_script_log(f"Connection forcibly closed: {e} (error code: {error_code})")
                time.sleep(5)
                reset_event.clear()
                continue
        except Exception as e:
            logging.error(f"⚠️ Connection/runtime error: {e}")
            add_script_log(f"Error: {e}")
            print("Will attempt reconnect in 30 seconds...")
            try:
                interface.close()
            except Exception:
                pass
            time.sleep(30)
            reset_event.clear()
            continue

def connection_monitor(initial_delay=30):
    global connection_status
    time.sleep(initial_delay)
    while True:
        if connection_status == "Disconnected":
            print("⚠️ Connection lost! Triggering reconnect...")
            reset_event.set()
        time.sleep(5)

# Start the watchdog thread after 20 seconds to give node a chance to connect
def poll_discord_channel():
    """Polls the Discord channel for new messages using the Discord API."""
    # Wait a short period for interface to be set up
    time.sleep(5)
    last_message_id = None
    headers = {"Authorization": f"Bot {DISCORD_BOT_TOKEN}"}
    url = f"https://discord.com/api/v9/channels/{DISCORD_CHANNEL_ID}/messages"
    while True:
        try:
            params = {"limit": 10}
            if last_message_id:
                params["after"] = last_message_id
            response = requests.get(url, headers=headers, params=params)
            if response.status_code == 200:
                msgs = response.json()
                msgs = sorted(msgs, key=lambda m: int(m["id"]))
                for msg in msgs:
                    if msg["author"].get("bot"):
                        continue
                    # Only process messages that arrived after the script started
                    if last_message_id is None:
                        msg_timestamp_str = msg.get("timestamp")
                        if msg_timestamp_str:
                            msg_time = datetime.fromisoformat(msg_timestamp_str.replace("Z", "+00:00"))
                            if msg_time < server_start_time:
                                continue
                    username = msg["author"].get("username", "DiscordUser")
                    content = msg.get("content")
                    if content:
                        formatted = f"**{username}**: {content}"
                        log_message("DiscordPoll", formatted, direct=False, channel_idx=DISCORD_INBOUND_CHANNEL_INDEX)
                        if interface is None:
                            print("❌ Cannot send polled Discord message: interface is None.")
                        else:
                            send_broadcast_chunks(interface, formatted, DISCORD_INBOUND_CHANNEL_INDEX)
                        print(f"Polled and routed Discord message: {formatted}")
                        last_message_id = msg["id"]
            else:
                print(f"Discord poll error: {response.status_code} {response.text}")
        except Exception as e:
            print(f"Error polling Discord: {e}")
        time.sleep(10)

@app.route("/instructions", methods=["GET"])
def instructions():
    html = """
    <html>
<head>
  <title>Meshtastic Controller - Usage Instructions</title>
  <style>
    body {
      background: #000;
      color: #fff;
      font-family: Arial, sans-serif;
      padding: 20px;
      line-height: 1.6;
    }
    a {
      color: #ffa500;
      text-decoration: none;
    }
    a:hover {
      text-decoration: underline;
    }
    .content {
      max-width: 800px;
      margin: 0 auto;
    }
    h1, h2 {
      color: #ffa500;
      border-bottom: 1px solid #333;
      padding-bottom: 5px;
    }
    ul {
      padding-left: 20px;
    }
    code {
      background: #222;
      color: #ffa500;
      padding: 2px 6px;
      border-radius: 4px;
    }
    .back {
      margin-top: 30px;
    }
  </style>
</head>
<body>
  <div class="content">
    <h1>Meshtastic Controller - Usage Instructions</h1>

    <p>Welcome to <strong>Meshtastic Controller</strong>! Below is a quick guide to get you started:</p>

    <h2>📡 Basic Usage</h2>
    <ul>
      <li>Use <code>/ai</code>, <code>/bot</code>, <code>/query</code>, or <code>/data</code> followed by your message to get an AI response.</li>
      <li>For direct messages, DM the AI node if configured to reply.</li>
      <li>Send <code>/whereami</code> to retrieve the node’s GPS coordinates (if available).</li>
      <li>Send <code>/time</code> to retrieve the current time,(if your timezone is set correctily).</li>
      <li>Trigger an emergency alert with <code>/emergency &lt;message&gt;</code> or <code>/911 &lt;message&gt;</code>. Alerts go via Twilio, SMTP, and Discord if enabled, including GPS and timestamps.</li>
      <li>Send SMS via <code>/sms &lt;+15555555555&gt; &lt;message&gt;</code>.</li>
    </ul>

    <h2>Config</h2>
    <ul>
        <li>Make sure that if you have not already edit you config file.</li>
        <a href="/config">Edit Config</a>
        <a href="/commands">Edit Commands</a>
    <ul>


    <h2>🏠 Home Assistant Integration</h2>
    <ul>
      <li>When enabled, messages in the designated Home Assistant channel (as per <code>home_assistant_channel_index</code>) are forwarded to Home Assistant’s conversation API.</li>
      <li>In secure mode, include the PIN in your message like: <code>PIN=XXXX your message</code>.</li>
    </ul>

    <h2>🖥️ WebUI Messaging</h2>
    <ul>
      <li>Use the dashboard’s message form to send broadcast or direct messages.</li>
      <li>The mode toggle and node selection simplify quick replies.</li>
    </ul>

    <h2>🔌 API Usage</h2>
    <p>The Meshtastic Controller server (Flask-based) exposes the following endpoints (see API docs for details).</p>

    <h2>📖 Dashboard Quick Commands</h2>
    <ul>
      <li>Use the <strong>Dashboard</strong> to send/receive messages, monitor nodes, and manage settings.</li>
      <li>Direct message a node by selecting it and typing your message.</li>
      <li>Broadcast messages via "Broadcast" mode to the desired channel.</li>
      <li>For available commands, type <code>/help</code> in the message box.</li>
    </ul>

    <div class="back">
      <a href="/dashboard">🡪 Onward to the Dashboard!</a>
    </div>
  </div>
</body>
</html>

    """
    return html

@app.route("/", methods=["GET"])
def root():
    return redirect(url_for("instructions"))

@app.route("/config", methods=["GET", "POST"])
def config_editor():
    global config  # Ensure we update the global config

    # Load config (make sure CONFIG_PATH is defined, e.g. CONFIG_PATH = "config/config.json")
    config = safe_load_json(CONFIG_PATH, {})

    if request.method == "POST":
        # Update config from form fields
        for key in config.keys():
            value = request.form.get(key)
            if isinstance(config[key], bool):
                config[key] = (value == "on")
            elif isinstance(config[key], int):
                try:
                    config[key] = int(value)
                except (ValueError, TypeError):
                    pass
            elif isinstance(config[key], dict):
                try:
                    config[key] = json.loads(value)
                except Exception:
                    config[key] = {}
            else:
                config[key] = value
        save_config(config)
        reload_config()  # <--- ADD THIS LINE!
         # Instead of redirect, render a template with a JS alert and redirect
        return render_template_string("""
        <html>
        <head>
        <script>
            alert('You need to restart the server for changes to take effect.');
            window.location.href = '/config';
        </script>
        </head>
        <body>
            <p>Config saved. You need to restart.</p>
        </body>
        </html>
        """)

    # Render each config option as an input
    form_fields = ""
    for key, value in config.items():
        if isinstance(value, bool):
            checked = "checked" if value else ""
            field = f'<label style="margin-right:20px;">{key}: <input type="checkbox" name="{key}" {checked}></label><br>'
        elif isinstance(value, int):
            field = f'<label style="margin-right:20px;">{key}: <input type="text" name="{key}" value="{value}"></label><br>'
        elif isinstance(value, dict):
            field = f'''<label style="margin-right:20px;">{key}:<br>
            <textarea name="{key}" rows="8" cols="60">{json.dumps(value, indent=2)}</textarea></label><br>'''
        else:
            field = f'<label style="margin-right:20px;">{key}: <input type="text" name="{key}" value="{value}"></label><br>'
        form_fields += field

    return render_template_string(f"""
    <html>
    <head>
      <title>Edit Config - Meshtastic Controller</title>
      <style>
        body {{
          background: #000;
          color: #fff;
          font-family: Arial, sans-serif;
          padding: 0;
          margin: 0;
        }}
        .header {{
          background: #111;
          padding: 20px;
          border-bottom: 2px solid #ffa500;
          display: flex;
          justify-content: space-between;
          align-items: center;
        }}
        .header a {{
          color: #ffa500;
          text-decoration: none;
          font-weight: bold;
          margin: 0 10px;
        }}
        .container {{
          max-width: 800px;
          margin: 40px auto;
          background: #111;
          padding: 30px 40px;
          border-radius: 12px;
          border: 2px solid #ffa500;
          box-shadow: 0 0 20px #000a;
        }}
        h1 {{
          color: #ffa500;
        }}
        label {{
          display: block;
          margin: 15px 0 5px 0;
        }}
        input[type="text"], input[type="password"] {{
          width: 70%;
          padding: 6px;
          border-radius: 4px;
          border: 1px solid #ffa500;
          background: #222;
          color: #fff;
        }}
        input[type="checkbox"] {{
          transform: scale(1.3);
          margin-left: 8px;
        }}
        button[type="submit"] {{
          margin-top: 20px;
          padding: 8px 24px;
          background: #ffa500;
          color: #000;
          border: none;
          border-radius: 6px;
          font-weight: bold;
          font-size: 1em;
          cursor: pointer;
        }}
        a.back {{
          display: inline-block;
          margin-top: 20px;
          color: #ffa500;
          text-decoration: none;
        }}
        a.back:hover {{
          text-decoration: underline;
        }}
      </style>
    </head>
    <body>
      <div class="header">
        <span><a href="{{{{ url_for('dashboard') }}}}">🡨 Dashboard</a></span>
        <span>
          <a href="{{{{ url_for('instructions') }}}}">Instructions</a>
          <a href="/commands">Edit Commands</a>
          <a href="{{{{ url_for('logs') }}}}" target="_blank">Logs</a>
        </span>
      </div>
      <div class="container">
        <h1>Edit Config</h1>
        <form method="post">
            {form_fields}
            <button type="submit">Save Changes</button>
        </form>
        <a class="back" href="{{{{ url_for('dashboard') }}}}">← Back to Dashboard</a>
      </div>
    </body>
    </html>
    """)

@app.route("/commands", methods=["GET", "POST"])
def commands_editor():
    config = safe_load_json(COMMANDS_CONFIG_FILE, {"commands": []})

    # Handle POST: Save changes, delete, or add empty rows
    if request.method == "POST":
        # Handle deletion via JS-generated POST
        if "delete_index" in request.form:
            idx = int(request.form["delete_index"])
            if 0 <= idx < len(config["commands"]):
                del config["commands"][idx]
                save_commands_config(config)
                return redirect(url_for("commands_editor"))

        # Save all commands
        new_commands = []
        total = int(request.form.get("total_commands", "0"))
        for i in range(total):
            command = request.form.get(f"command_{i}", "").strip()
            ai_prompt = request.form.get(f"ai_prompt_{i}", "").strip()
            response = request.form.get(f"response_{i}", "").strip()
            if command:
                entry = {"command": command}
                if ai_prompt:
                    entry["ai_prompt"] = ai_prompt
                if response:
                    entry["response"] = response
                new_commands.append(entry)
        config["commands"] = new_commands
        save_commands_config(config)
        return render_template_string("""
        <script>alert('Commands saved!'); window.location.href='/commands';</script>
        """)

    # How many empty rows to show (default: 1, or more if requested)
    try:
        add_rows = int(request.args.get("add_rows", "1"))
    except Exception:
        add_rows = 1

    # Render the form fields
    form_fields = ""
    for i, cmd in enumerate(config.get("commands", [])):
        form_fields += f"""
        <div style='margin-bottom:20px; border:1px solid #ccc; padding:10px;'>
          <label>Command: <input type='text' name='command_{i}' value="{cmd.get('command','')}" /></label><br>
          <label>AI Prompt: <input type='text' name='ai_prompt_{i}' value="{cmd.get('ai_prompt','')}" /></label><br>
          <label>Response: <input type='text' name='response_{i}' value="{cmd.get('response','')}" /></label><br>
          <button type="button" onclick="deleteCommand({i})" style="background:#ff4444;color:#fff;border:none;padding:4px 16px;margin-top:8px; border-radius:4px;">Delete</button>
        </div>
        """

    # Add empty rows for new commands
    total_commands = len(config.get("commands", []))
    for j in range(add_rows):
        idx = total_commands + j
        form_fields += f"""
        <div style='margin-bottom:20px; border:1px dashed #ffa500; padding:10px;'>
          <label>Command: <input type='text' name='command_{idx}' /></label><br>
          <label>AI Prompt: <input type='text' name='ai_prompt_{idx}' /></label><br>
          <label>Response: <input type='text' name='response_{idx}' /></label><br>
        </div>
        """

    total_commands += add_rows

    # Main output
    return render_template_string(f"""
    <html>
    <head>
      <title>Edit Commands</title>
      <style>
        body {{ background: #000; color: #fff; font-family: Arial; }}
        .container {{ max-width: 600px; margin:40px auto; background:#111; padding:30px; border-radius:12px; border:2px solid #ffa500; }}
        label {{ display:block; margin-top:8px; }}
        input[type="text"] {{ width:90%; padding:5px; margin-bottom:8px; border-radius:4px; border:1px solid #ffa500; background:#222; color:#fff; }}
        button {{ background:#ffa500; color:#000; border:none; border-radius:4px; padding:8px 24px; margin-top:16px; font-size:1em; cursor:pointer; }}
        .delete-btn {{ background:#ff4444; color:#fff; border:none; padding:4px 16px; margin-top:8px; border-radius:4px; }}
        a {{ color:#ffa500; }}
      </style>
    </head>
    <body>
      <div class="container">
        <h1>Edit Commands Config</h1>
        <form method="post">
            {form_fields}
            <input type="hidden" name="total_commands" value="{total_commands}">
            <button type="submit">Save Commands</button>
        </form>
        <form method="get" style="margin-top:20px;">
            <label>Add more empty rows:
                <input type="number" name="add_rows" min="1" max="10" value="1" style="width:60px;">
            </label>
            <button type="submit">Add Rows</button>
        </form>
        <a href='/dashboard'>← Back to Dashboard</a>
      </div>
      <script>
      function deleteCommand(idx) {{
          if(confirm("Are you sure you want to delete this command?")) {{
              var form = document.createElement("form");
              form.method = "POST";
              form.style.display = "none";
              var input = document.createElement("input");
              input.type = "hidden";
              input.name = "delete_index";
              input.value = idx;
              form.appendChild(input);
              document.body.appendChild(form);
              form.submit();
          }}
      }}
      </script>
    </body>
    </html>
    """)
    
if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("User interrupted the script. Exiting.")
        add_script_log("Server exited via KeyboardInterrupt.")
    except Exception as e:
        logging.error(f"Unhandled error in main: {e}")
        add_script_log(f"Unhandled error: {e}")
        print("Encountered an error. Exiting...")
