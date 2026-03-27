from flask import Flask, render_template, request, jsonify
from openai import OpenAI
from datetime import datetime
import os
import json
import re
import requests

app = Flask(__name__)

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

client = OpenAI(api_key=OPENAI_API_KEY)

LOG_FILE = "chat_log.txt"
LEADS_FILE = "patient_leads.txt"

SYSTEM_PROMPT = """
You are the AI assistant for Lin Ma DDS Inc in West Covina.

Office info:
- Phone: (626) 966-4514
- Services: dental implants, implant supported dentures, dentures, emergency dental care,
  tooth extraction, root canal treatment, dental cleaning, crowns and bridges,
  tooth filling, gum treatment, cosmetic dentistry, oral surgery, braces, denture repair.
- Same-day appointments may be available depending on schedule.
- If pricing depends on exam or x-rays, say that fees vary by case and invite the patient to call.

Behavior rules:
1. Be warm, simple, and professional.
2. Keep answers short.
3. Do not diagnose definitively.
4. For urgent symptoms like swelling, severe pain, bleeding, trauma, broken tooth, infection,
   encourage calling the office promptly.
5. When the user shows strong treatment intent or booking intent, invite contact info.

Trigger contact request when user asks about:
- cost, price, fees
- implants, dentures, braces
- emergency tooth pain, swelling, broken tooth
- booking, appointment, consultation
- insurance / Medi-Cal

When appropriate, ask:
"Would you like our office to contact you? Please reply with your name and phone number."

If the user already provided name and phone, thank them and say:
"Thank you. We’ve saved your information and our office can follow up with you."
"""

def append_text(path: str, text: str) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(text + "\n")

def normalize_phone(text: str) -> str:
    return re.sub(r"\D", "", text)

def looks_like_phone(text: str) -> bool:
    digits = re.sub(r"\D", "", text)
    return len(digits) >= 10

def extract_phone(text: str) -> str | None:
    digits = re.sub(r"\D", "", text)
    match = re.search(r"\d{10}", digits)

    if match:
        phone = match.group()
        return f"{phone[:3]}-{phone[3:6]}-{phone[6:]}"

    return None

def extract_name(text: str) -> str | None:
    text = text.strip()

    patterns = [
        r"(?:my name is|this is)\s+([A-Za-z][A-Za-z\s\.\'-]{1,40}?)(?:,|\s+and\b|\s+phone\b|\s+my phone\b|$)",
        r"(?:i am|i'm)\s+([A-Za-z][A-Za-z\s\.\'-]{1,40}?)(?:,|\s+and\b|\s+phone\b|\s+my phone\b|$)",
        r"^([A-Za-z][A-Za-z\s\.\'-]{1,40}?)\s+\d"
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip()

    return None

def extract_contact_info(text: str) -> dict:
    return {
        "name": extract_name(text),
        "phone": extract_phone(text)
    }

def send_telegram_notification(message: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram notification skipped: missing bot token or chat id.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message
    }

    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
    except Exception as e:
        print(f"Telegram notification failed: {e}")

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json(force=True)
    user_message = data.get("message", "").strip()
    history = data.get("history", [])

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    append_text(LOG_FILE, f"\n[{timestamp}] USER: {user_message}")

    contact = extract_contact_info(user_message)
    saved_lead = False

    if contact["name"] and contact["phone"]:
        lead_record = {
            "timestamp": timestamp,
            "name": contact["name"],
            "phone": contact["phone"],
            "source_message": user_message,
            "source": "chat"
        }
        append_text(LEADS_FILE, json.dumps(lead_record, ensure_ascii=False))
        saved_lead = True

        send_telegram_notification(
            f"🚨 New Dental Lead (Chat)\n\n"
            f"👤 {contact['name']}\n"
            f"📞 {contact['phone']}\n"
            f"⏰ {timestamp}\n\n"
            f"👉 Call now: tel:{contact['phone'].replace('-', '')}"
        )

        reply = (
            "Thank you. We’ve saved your information and our office can follow up with you. "
            "If this is urgent, please call (626) 966-4514."
        )
        append_text(LOG_FILE, f"[{timestamp}] ASSISTANT: {reply}\n")
        return jsonify({"reply": reply, "saved_lead": saved_lead})

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    for item in history[-10:]:
        role = item.get("role")
        content = item.get("content")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})

    messages.append({"role": "user", "content": user_message})

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages,
        temperature=0.4
    )

    reply = response.choices[0].message.content.strip()
    append_text(LOG_FILE, f"[{timestamp}] ASSISTANT: {reply}\n")

    return jsonify({"reply": reply, "saved_lead": saved_lead})

@app.route("/lead", methods=["POST"])
def lead():
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    phone = (data.get("phone") or "").strip()
    reason = (data.get("reason") or "").strip()

    if not name or not phone:
        return jsonify({"success": False, "error": "Name and phone are required."})

    if len(normalize_phone(phone)) < 10:
        return jsonify({"success": False, "error": "Please enter a valid phone number."})

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lead_record = {
        "timestamp": timestamp,
        "name": name,
        "phone": phone,
        "reason": reason,
        "source": "lead_form"
    }

    append_text(LEADS_FILE, json.dumps(lead_record, ensure_ascii=False))

    append_text(
        LOG_FILE,
        f"\n[{timestamp}] LEAD FORM SUBMISSION\n"
        f"[{timestamp}] NAME: {name}\n"
        f"[{timestamp}] PHONE: {phone}\n"
        f"[{timestamp}] REASON: {reason if reason else '(none)'}\n"
    )

    send_telegram_notification(
        f"📋 New Appointment Request\n\n"
        f"👤 {name}\n"
        f"📞 {phone}\n"
        f"🦷 {reason if reason else 'General inquiry'}\n"
        f"⏰ {timestamp}\n\n"
        f"👉 Call now: tel:{phone.replace('-', '')}"
    )

    return jsonify({"success": True})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)