import streamlit as st
from pymongo import MongoClient
from datetime import datetime, timezone
import time
import re
from auth0_component import login_button
import streamlit.components.v1 as components
import random
from pymongo import ReturnDocument

# === APP STATE TRACKING ===
if "profile_step" not in st.session_state:
    st.session_state["profile_step"] = 1
if "prev_auth0_id" not in st.session_state:
    st.session_state["prev_auth0_id"] = None

# === CONFIG & STYLING ===
try:
    st.set_page_config(page_title="JNANA Auditing", layout="wide", initial_sidebar_state="collapsed")
    st.markdown("""
        <style>
        .passage-box {
            background-color: #fafafa;
            border: 1px solid #ddd;
            border-radius: 8px;
            padding: 1rem;
            font-family: sans-serif;
            color: #333;
            white-space: pre-wrap;
            max-height: 1000px;
            overflow-y: auto;
        }
        div.stButton > button {
            background-color: #00bcd4 !important;
            color: white !important;
            border: none !important;
            padding: 0.5rem 1.2rem !important;
            border-radius: 5px !important;
            font-weight: bold !important;
            margin-top: 1rem !important;
        }
        div.stButton > button:hover {
            background-color: #0097a7 !important;
        }
        </style>
    """, unsafe_allow_html=True)
except Exception as e:
    import traceback
    log_system_event(
        "unexpected_error",
        str(e),
        {"traceback": traceback.format_exc()}
    )
    st.error("🔴 An unexpected error occurred. Please reload or contact support.")
    raise

# Helper: Show Login Screen Title & Description
def show_login_intro():
    st.title("🔐 Welcome to JNANA QA Auditing Tool")
    st.markdown("Please log in to audit short Q&A pairs.")

 # === SYSTEM / INFRASTRUCTURE LOGGING (standalone) ===
def log_system_event(event, message, details=None):
    """
    Logs directly to MongoDB using a fresh client so that
    it can be called before `db` is set up.
    """
    try:
        temp_client = MongoClient(
            st.secrets.get("mongo_uri", ""),
            serverSelectionTimeoutMS=2000
        )
        temp_db = temp_client["Tel_QA"]
        temp_db["system_logs"].insert_one({
            "timestamp": datetime.now(timezone.utc),
            "event":     event,
            "message":   message,
            "details":   details or {}
        })
    except Exception:
        # best‐effort only, swallow errors
        pass

# === MONGO CONNECTION ===
@st.cache_resource
def get_client():
    try:
        client = MongoClient(
            st.secrets["mongo_uri"],
            serverSelectionTimeoutMS=5000
        )
        client.admin.command("ping")
        return client
    except Exception as e:
        # now log_system_event is already defined
        log_system_event("db_connect_error", str(e))
        st.error("🔴 Cannot connect to database. Please try again later.")
        st.stop()

client      = get_client()
db          = client["Tel_QA"]
users_col   = db["users"]
content_col = db["Content"]
qa_col      = db["QA_pairs"]
audit_col   = db["audit_logs"]
doubt_col   = db["doubt_logs"]
skip_col    = db["skipped_logs"]

# track “reserved” slots so we can block concurrent assignments
assign_col = db["assignment_placeholders"]

# === USER ACTION LOGGING ===
user_logs = db["user_logs"]

 # === SYSTEM / INFRASTRUCTURE LOGGING ===
system_logs = db["system_logs"]

def log_user_action(intern_id, action, details=None):
    """
    Persist a timestamped action for audit.
    action: str, e.g. "assigned", "skipped", "timeout", "submitted", "next_clicked", "error"
    details: dict of any extra context (e.g. content_id, time_taken, error message)
    """
    user_logs.insert_one({
        "intern_id": intern_id,
        "date":      datetime.now(timezone.utc),
        "action":    action,
        "details":   details or {}
    })



TIMER_SECONDS = 60 * 7
MAX_AUDITORS  = 5

# === AUTH0 LOGIN ===

# only call login_button() until we have user_info
if "user_info" not in st.session_state:
    try:
        auth0_user_info = login_button(
            st.secrets["AUTH0_CLIENT_ID"],
            st.secrets["AUTH0_DOMAIN"],
        )
    except Exception as e:
        log_system_event("auth0_error", str(e))
        st.error("🔴 Auth0 Login Failed. Please contact support.")
        st.stop()

    if not auth0_user_info:
        show_login_intro()
        st.warning("⚠️ Please log in to continue.")
        st.stop()

    # store and immediately rerun to clear the login UI
    st.session_state.user_info = auth0_user_info
    st.rerun()

# from here on down, we know user_info is set and login_button() won't be called again
user_info = st.session_state.user_info

# === EXTRACT USER INFO ===

auth0_id   = user_info.get("sub")
given_name = user_info.get("given_name", "")
email      = user_info.get("email", "")
picture    = user_info.get("picture", "")

# === INTERN‑ID GENERATOR & UNIQUE 5 OPTIONS ===

def generate_intern_ids(first, last):
    try:
        existing = set(doc["intern_id"] for doc in users_col.find({}, {"intern_id": 1}))
    except:
        existing = set()

    patterns = [
        first[:3] + last[:2],
        first[:2] + last[:3],
        first[:1] + last[:5],
        last[:3] + first[:2],
        last[:2] + first[:3],
    ]

    candidates = []
    for pat in patterns:
        base = re.sub(r'[^A-Za-z]', '', pat).lower()
        base = (base[:6]).ljust(6, 'x')
        if base not in existing and base not in candidates:
            candidates.append(base)
        if len(candidates) == 5:
            break

    import random, string
    while len(candidates) < 5:
        suffix = ''.join(random.choices(string.ascii_lowercase, k=6))
        if suffix not in existing and suffix not in candidates:
            candidates.append(suffix)

    return candidates

# === FIRST‑TIME SIGNUP FLOW ===

existing_user = users_col.find_one({"auth0_id": auth0_id})
if existing_user is None:
    if st.session_state.get("profile_step", 1) == 1:
        st.subheader("📝 Complete Your Profile")
        fn = st.text_input("First Name", value=given_name)
        ln = st.text_input("Last Name")
        phone = st.text_input("Phone Number")
        if st.button("➡️ Next Step"):
            if not (fn and ln and phone):
                st.warning("⚠️ All fields are required.")
            else:
                st.session_state.first_name = fn
                st.session_state.last_name  = ln
                st.session_state.phone_number = phone
                st.session_state.profile_step = 2
                st.rerun()
        st.stop()

    if st.session_state.profile_step == 2:
        st.subheader("🆔 Choose Your Intern ID")
        options = generate_intern_ids(
            st.session_state.first_name,
            st.session_state.last_name
        )
        selected = st.radio("Select one of these IDs:", options)
        if selected and st.button("✅ Submit Profile Information"):
            users_col.insert_one({
                "auth0_id":   auth0_id,
                "first_name": st.session_state.first_name,
                "last_name":  st.session_state.last_name,
                "phone":      st.session_state.phone_number,
                "email":      email,
                "picture":    picture,
                "intern_id":  selected,
                "created_at": datetime.now(timezone.utc)
            })
            st.success("✅ Profile saved! Reloading…")
            st.session_state.profile_step = 1
            st.rerun()
        st.stop()

# === POST‑SIGNUP UI ===

intern_id = existing_user["intern_id"] if existing_user else selected
first = existing_user["first_name"] if existing_user else st.session_state.first_name
last = existing_user["last_name"] if existing_user else st.session_state.last_name

st.title("🔍 JNANA – Short Q&A Auditing Tool")
st.markdown(f"Hello, **{first} {last}**! Your Intern ID: **{intern_id}**.")

# === MANUAL LOGOUT BUTTON ===

if st.button("🔒 Logout"):
    # Clear only our custom session keys
    for k in list(st.session_state.keys()):
        if k not in ["global_config", "secrets"]:
            del st.session_state[k]
    domain = st.secrets["AUTH0_DOMAIN"]
    client_id = st.secrets["AUTH0_CLIENT_ID"]
    st.components.v1.html(f"""
        <script>
          const domain = "{domain}";
          const clientId = "{client_id}";
          const returnTo = window.location.origin;
          alert("🎉 You have been logged out successfully. The app will now reload.");
          window.top.location.href = `https://${{domain}}/v2/logout?client_id=${{clientId}}&returnTo=${{returnTo}}`;
        </script>
    """, height=0)
    st.rerun()

# === Session State Init ===
for key in ["eligible_id", "deadline", "assigned_time", "judged",
            "auto_skip_triggered", "current_content_id",
            "eligible_content_ids", "timer_expired"]:
    if key not in st.session_state:
        st.session_state[key] = None if key in ["eligible_id", "current_content_id"] else False

# === Timer Expired Screen ===
if st.session_state.timer_expired:
    st.title("⏰ Time Expired")
    st.warning("This content ID has been skipped due to timeout.")
    if st.button("🔄 Fetch New Content"):
        st.session_state.timer_expired = False
        st.session_state.eligible_id = None
        st.session_state.current_content_id = None
        st.rerun()
    st.stop()

# === ATOMIC ASSIGNMENT via placeholder collection ===
def assign_new_content():
    # 1) get all content IDs
    all_ids = qa_col.distinct("content_id")

    # 2) aggregate real audit counts once
    real_counts = {
        doc["_id"]: doc["count"]
        for doc in audit_col.aggregate([
            {"$group": {"_id": "$content_id", "count": {"$sum": 1}}}
        ])
    }

    # 3) aggregate pending (placeholder) counts once
    pending_counts = {
        doc["_id"]: doc["count"]
        for doc in assign_col.aggregate([
            {"$group": {"_id": "$content_id", "count": {"$sum": 1}}}
        ])
    }

    # 4) lookup which IDs this intern already saw or reserved
    seen = set(audit_col.distinct("content_id", {"intern_id": intern_id}))
    reserved = set(assign_col.distinct("content_id", {"intern_id": intern_id}))

    # 5) filter to those under the cap and not yet seen by this intern
    candidates = []
    for cid in all_ids:
        total = real_counts.get(cid, 0) + pending_counts.get(cid, 0)
        if total < MAX_AUDITORS and cid not in seen and cid not in reserved:
            candidates.append(cid)

    # 6) if nothing remains, signal “done”
    if not candidates:
        st.session_state.eligible_id = None
        return

    # 7) pick one at random, reserve it, and update state
    cid = random.choice(candidates)
    assign_col.insert_one({
        "content_id":  cid,
        "intern_id":   intern_id,
        "assigned_at": datetime.now(timezone.utc)
    })
    log_user_action(intern_id, "assigned_content", {"content_id": cid})

    st.session_state.eligible_id   = cid
    st.session_state.deadline      = time.time() + TIMER_SECONDS
    st.session_state.assigned_time = datetime.now(timezone.utc)


# kick things off
if st.session_state.eligible_id is None:
    assign_new_content()
if st.session_state.eligible_id is None:
    st.success("✅ All content audited!")
    st.stop()


cid = st.session_state.eligible_id
remaining = int(st.session_state.deadline - time.time())

# === Fetch Content & QA ===
# @st.cache_data(ttl=300)
def fetch_content_qa(cid):
    try:
        start = time.time()
        content = content_col.find_one({"content_id": cid})
        qa_doc  = qa_col.find_one({"content_id": cid})
        duration = time.time() - start
        if duration > 1.0:
            log_system_event("slow_db_query",
                             f"fetch_content_qa took {duration:.2f}s",
                             {"content_id": cid})
        return content, qa_doc
    except Exception as e:
        log_system_event("db_query_error",
                         f"Error fetching content {cid}: {e}",
                         {"content_id": cid})
        raise

content, qa_doc = fetch_content_qa(cid)
qa_pairs = qa_doc.get("questions", {}).get("short", []) if qa_doc else []

# === Reset radios if content changes ===
if st.session_state.current_content_id != cid:
    for i in range(len(qa_pairs)):
        st.session_state[f"j_{i}"] = None
    st.session_state.current_content_id = cid
    
# === Handle Invalid or Missing Short QA ===
content_text = content.get("content_text", "").strip() if content and isinstance(content.get("content_text"), str) else ""

qa_valid = (
    qa_doc and
    isinstance(qa_doc.get("questions", {}).get("short"), list) and
    all("question" in q and "answer" in q for q in qa_doc["questions"]["short"])
)

if not content or not content_text or not qa_valid:
    skip_col.insert_one({
        "intern_id": intern_id,
        "content_id": cid,
        "status": "missing_or_invalid",
        "assigned_at": st.session_state.assigned_time,
        "timestamp": datetime.now(timezone.utc)
    })
    assign_col.delete_many({
        "content_id": cid,
        "intern_id":  intern_id
    })
    # log the skip
    log_user_action(intern_id, "skipped_invalid", {"content_id": cid})

    st.warning(f"⚠️ Skipping ID {cid} — content or valid short QA missing.")
    st.session_state.current_content_id = None
    assign_new_content()
    st.rerun()


# === Timeout Logic ===
if remaining <= 0:
    skip_col.insert_one({
        "intern_id": intern_id,
        "content_id": cid,
        "status": "timeout",
        "assigned_at": st.session_state.assigned_time,
        "timestamp": datetime.now(timezone.utc)
    })
    assign_col.delete_many({
        "content_id": cid,
        "intern_id":  intern_id
    })
    # log the timeout
    log_user_action(intern_id, "skipped_timeout", {"content_id": cid})
    st.session_state.timer_expired = True
    st.rerun()

# === Timer Display ===
st.components.v1.html(f"""
<div style='text-align:center;margin-bottom:1rem;font-size:22px;font-weight:bold;color:white;
    background-color:#212121;padding:10px 20px;border-radius:8px;width:fit-content;margin:auto;
    border:2px solid #00bcd4;font-family:monospace;'>
  ⏱ Time Left: <span id="timer">{remaining//60:02d}:{remaining%60:02d}</span>
  <script>
    let total = {remaining};
    const el = document.getElementById('timer');
    const interval = setInterval(() => {{
      let m = Math.floor(total/60);
      let s = total % 60;
      el.textContent = `${{m.toString().padStart(2,'0')}}:${{s.toString().padStart(2,'0')}}`;
      total--;
      if (total < 0) clearInterval(interval);
    }}, 1000);
  </script>
</div>
""", height=80)

# === UI Layout ===
left, right = st.columns(2)

with left:
    st.subheader(f"📄 Content ID: {cid}")
    st.markdown(f"<div class='passage-box'>{content_text}</div>", unsafe_allow_html=True)

with right:
    st.subheader("❓ Short Q&A Pairs")
    judgments = []
    for i, pair in enumerate(qa_pairs):
        st.markdown(f"**Q{i+1}:** {pair['question']}")
        st.markdown(f"**A{i+1}:** {pair['answer']}")
        selected = st.radio("", ["Correct", "Incorrect", "Doubt"], key=f"j_{i}", index=None)
        judgments.append({
            "qa_index": i,
            "question": pair["question"],
            "answer": pair["answer"],
            "judgment": selected
        })
        st.markdown("---")

# === Buttons ===
submit = st.button("✅ Submit")
next_ = st.button("➡️ Next")

if submit:
    now        = datetime.now(timezone.utc)
    time_taken = (now - st.session_state.assigned_time).total_seconds()

    # remove the placeholder reservation
    assign_col.delete_many({
        "content_id": cid,
        "intern_id":  intern_id
    })

        # log the submission
    log_user_action(intern_id, "submitted", {
        "content_id": cid,
        "time_taken": time_taken
    })

    for entry in judgments:
        entry.update({
            "content_id":   cid,
            "intern_id":    intern_id,
            "timestamp":    now,
            "assigned_at":  st.session_state.assigned_time,
            "time_taken":   time_taken,
            "length":       "short"
        })
        if entry["judgment"] == "Doubt":
            doubt_col.insert_one(entry)
        else:
            audit_col.insert_one(entry)

    st.success(f"✅ Judgments saved in {time_taken:.1f}s")


if next_:
        # log clicking “Next” (content was skipped by intern’s choice)
    log_user_action(intern_id, "next_clicked", {"previous_content_id": cid})
    for key in list(st.session_state.keys()):
        if key.startswith("j_"):
            del st.session_state[key]
    st.session_state.current_content_id = None
    assign_new_content()
    st.rerun()
