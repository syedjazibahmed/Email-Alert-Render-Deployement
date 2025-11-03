import imaplib, email, smtplib, os, json, re
from email.mime.text import MIMEText
from email.utils import formatdate, parsedate_to_datetime
from dotenv import load_dotenv
from datetime import datetime, timedelta, timezone

# ---------------- Config ----------------
STATE_PATH = "state.json"
EXPECTED_PARTS = {1, 2, 3}  # Expected numbering in subjects (e.g., 1, 2, 3)

load_dotenv()

# Monitored inbox (Account A)
MONITOR_EMAIL = os.getenv("MONITOR_EMAIL")
MONITOR_PASS = os.getenv("MONITOR_APP_PASSWORD")
MONITOR_IMAP = os.getenv("MONITOR_IMAP_SERVER", "imap.gmail.com")
MONITOR_PORT = int(os.getenv("MONITOR_IMAP_PORT", 993))

# Alert sender (Account B)
ALERT_EMAIL_SENDER = os.getenv("ALERT_EMAIL_SENDER")
ALERT_EMAIL_PASSWORD = os.getenv("ALERT_EMAIL_PASSWORD")
ALERT_EMAIL_RECIPIENT = os.getenv("ALERT_EMAIL_RECIPIENT")
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))


# ---------------- State Management ----------------
def load_state():
    if not os.path.exists(STATE_PATH):
        return {"completed_subjects": {}, "last_check": None}
    with open(STATE_PATH, "r") as f:
        return json.load(f)


def save_state(state):
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


# ---------------- Email Alert ----------------
def send_alert(subject):
    body = f"✅ ALERT: All parts (1, 2, 3) received for subject '{subject}' in Bulleteconomics Gmail."
    msg = MIMEText(body)
    msg["Subject"] = f"[ALERT] {subject} - Complete Set Received"
    msg["From"] = ALERT_EMAIL_SENDER
    msg["To"] = ALERT_EMAIL_RECIPIENT
    msg["Date"] = formatdate(localtime=True)

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(ALERT_EMAIL_SENDER, ALERT_EMAIL_PASSWORD)
        server.sendmail(ALERT_EMAIL_SENDER, [ALERT_EMAIL_RECIPIENT], msg.as_string())

    print(f"📨 Alert sent for completed subject: {subject}")


# ---------------- Subject Parsing ----------------
def parse_subject(subject):
    """
    Extract base subject and numeric part (1, 2, 3 etc.)
    Works with flexible formats like:
    '1,hi', '(2).hi', '3/hi', 'hi 1', 'hi-2', 'hi_3'
    """
    subject = subject.strip()

    # Try number at start
    match = re.match(r"^[\(\[\{]*\s*([0-9]+)[\)\]\}]*[,\.\-_/:\s]*([^\d].*)$", subject)
    if match:
        part = int(match.group(1))
        base = match.group(2).strip()
        return base, part

    # Try number at end
    match = re.match(r"^(.*?[^\d])[\s,\.\-_/:\(\)\[\]]*([0-9]+)[\)\]\}]*\s*$", subject)
    if match:
        base = match.group(1).strip()
        part = int(match.group(2))
        return base, part

    return subject, None


# ---------------- Gmail Checker ----------------
def check_gmail():
    state = load_state()
    completed_subjects = state.get("completed_subjects", {})
    last_check_str = state.get("last_check")

    now = datetime.now(timezone.utc)
    if last_check_str:
        try:
            last_check = datetime.fromisoformat(last_check_str)
        except Exception:
            last_check = now - timedelta(hours=1)
        if last_check.tzinfo is None:
            last_check = last_check.replace(tzinfo=timezone.utc)
    else:
        last_check = now - timedelta(hours=1)

    print(f"\n[{now}] Checking emails from the past 1 hour...")

    mail = imaplib.IMAP4_SSL(MONITOR_IMAP, MONITOR_PORT)
    mail.login(MONITOR_EMAIL, MONITOR_PASS)
    mail.select("inbox")

    since_date = (now - timedelta(days=1)).strftime("%d-%b-%Y")
    status, data = mail.search(None, f'(SINCE "{since_date}")')

    if status != "OK":
        print("⚠️ No messages found or search failed.")
        mail.close()
        mail.logout()
        return

    mail_ids = data[0].split()
    new_parts = {}

    for mid in mail_ids:
        status, msg_data = mail.fetch(mid, "(RFC822)")
        raw = msg_data[0][1]
        msg = email.message_from_bytes(raw)

        try:
            email_date = parsedate_to_datetime(msg["Date"])
            if email_date is None:
                raise ValueError("no date")
            if email_date.tzinfo is None:
                email_date = email_date.replace(tzinfo=timezone.utc)
            else:
                email_date = email_date.astimezone(timezone.utc)
        except Exception:
            email_date = now

        # Only consider emails from the last 1 hour
        if email_date < now - timedelta(hours=1):
            continue

        subject = msg["Subject"] or "No Subject"
        base, part = parse_subject(subject)
        if part is None:
            continue

        # Skip if subject already completed
        if base in completed_subjects and completed_subjects[base].get("completed"):
            continue

        # Track received parts
        if base not in new_parts:
            new_parts[base] = set()
        new_parts[base].add(part)

    mail.close()
    mail.logout()

    # Merge with previous state and check for completion
    for base, parts in new_parts.items():
        prev = set(completed_subjects.get(base, {}).get("received", []))
        updated = prev.union(parts)

        if updated == EXPECTED_PARTS:
            send_alert(base)
            completed_subjects[base] = {"received": list(updated), "completed": True}
        else:
            completed_subjects[base] = {"received": list(updated), "completed": False}

    # Save new state
    state["completed_subjects"] = completed_subjects
    state["last_check"] = now.isoformat()
    save_state(state)

    print(f"✅ Completed check at {now}. Exiting...")


# ---------------- Run Once ----------------
if __name__ == "__main__":
    try:
        check_gmail()
    except Exception as e:
        print(f"❌ Error: {e}")
