import streamlit as st
from pymongo import MongoClient
from datetime import datetime, timezone
import time
import re
from auth0_component import login_button
import streamlit.components.v1 as components
import random

# === APP STATE TRACKING ===
if "profile_step" not in st.session_state:
    st.session_state["profile_step"] = 1
if "prev_auth0_id" not in st.session_state:
    st.session_state["prev_auth0_id"] = None

# === CONFIG & STYLING ===
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

# Helper: Show Login Screen Title & Description
def show_login_intro():
    st.title("🔐 Welcome to JNANA QA Auditing Tool")
    st.markdown("Please log in to audit short Q&A pairs.")

# === MONGO CONNECTION ===
@st.cache_resource
def get_client():
    client = MongoClient(st.secrets["mongo_uri"])
    client.admin.command("ping")
    return client

client      = get_client()
db          = client["Tel_QA"]
users_col   = db["users"]
content_col = db["Content"]
qa_col      = db["QA_pairs"]
audit_col   = db["audit_logs"]
doubt_col   = db["doubt_logs"]
skip_col    = db["skipped_logs"]

TIMER_SECONDS = 60
MAX_AUDITORS  = 5

# === AUTH0 LOGIN & LOGOUT HANDLING ===
try:
    user_info = login_button(
        st.secrets["AUTH0_CLIENT_ID"],          # ← first positional: your Client ID
        st.secrets["AUTH0_DOMAIN"],             # ← second positional: your Auth0 domain
        redirectUri="https://audittool-test2.streamlit.app/~/+/component/auth0_component.login_button/index.html",
        logoutUri="https://audittool-test2.streamlit.app/"
    )
    st.write("DEBUG ▶ user_info:", user_info)
    st.write("DEBUG ▶ prev_auth0_id:", st.session_state.get("prev_auth0_id"))
except Exception as e:
    st.error("🔴 Auth0 Login Failed. Check your settings.")
    st.exception(e)
    st.stop()


# ── If they were logged in, and now user_info is empty ⇒ logout
if st.session_state.get("prev_auth0_id") and not user_info:
    # clear every key in session_state
    for k in list(st.session_state.keys()):
        del st.session_state[k]
    # reinitialize the minimal state you need
    st.session_state["profile_step"] = 1
    st.session_state["prev_auth0_id"] = None

    st.success("🎉 You have been logged out successfully.")
    if st.button("🔄 Reload to log in again"):
        st.experimental_rerun()
    # stop everything else
    st.stop()

# ── If they’ve just logged in, store their sub
if user_info:
    st.session_state["prev_auth0_id"] = user_info.get("sub")

# ── If still no user_info, show intro + login prompt
if not user_info:
    show_login_intro()
    st.warning("⚠️ Please log in to continue.")
    st.stop()

# ── AUTH0 LOGOUT DETECTION & AUTO-RELOAD ──
if st.session_state.get("prev_auth0_id") and not user_info:
    # completely clear session state
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    st.session_state["profile_step"] = 1
    st.session_state["prev_auth0_id"] = None

    st.success("🎉 You have been logged out successfully.")
    # inject JS to force a full browser reload
    st.components.v1.html(
        """
        <script>
          // reload the page at the browser level
          window.location.href = window.location.origin + window.location.pathname;
        </script>
        """,
        height=0,
    )
    st.stop()


# === EXTRACT USER INFO ===
auth0_id   = user_info.get("sub")
given_name = user_info.get("given_name", "")
email      = user_info.get("email", "")
picture    = user_info.get("picture", "")

# === INTERN‐ID GENERATOR & UNIQUE 5 OPTIONS ===
def generate_intern_ids(first, last):
    # load existing intern_ids (if any)
    try:
        existing = set(doc["intern_id"] for doc in users_col.find({}, {"intern_id": 1}))
    except:
        existing = set()

    # possible base patterns mixing first/last
    patterns = [
        first[:3] + last[:2],
        first[:2] + last[:3],
        first[:1] + last[:5],
        last[:3] + first[:2],
        last[:2] + first[:3],
    ]

    candidates = []
    for pat in patterns:
        # strip non-alpha, lowercase
        base = re.sub(r'[^A-Za-z]', '', pat).lower()
        # pad or trim to 6 letters
        base = (base[:6]).ljust(6, 'x')
        if base not in existing and base not in candidates:
            candidates.append(base)
        if len(candidates) == 5:
            break

    # if still fewer than 5, fill with random alpha suffixes
    import string, random as _rand
    while len(candidates) < 5:
        suffix = ''.join(_rand.choices(string.ascii_lowercase, k=6))
        if suffix not in existing and suffix not in candidates:
            candidates.append(suffix)

    return candidates


# === FIRST‐TIME SIGNUP FLOW ===
existing_user = users_col.find_one({"auth0_id": auth0_id})
if existing_user is None:
    # STEP 1: collect basic info
    if st.session_state.profile_step == 1:
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

    # === STEP 2: choose intern ID (replace your existing) ===
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

# === POST‐SIGNUP UI ===
intern_id = existing_user["intern_id"]
st.title("🔍 JNANA – Short Q&A Auditing Tool")
st.markdown(f"Hello, **{existing_user['first_name']} {existing_user['last_name']}**! Your Intern ID: **{intern_id}**.")

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

# === Assign New Content ===
def assign_new_content():
    if not st.session_state.eligible_content_ids:
        all_ids = qa_col.distinct("content_id")
        random.shuffle(all_ids)
        st.session_state.eligible_content_ids = all_ids

    while st.session_state.eligible_content_ids:
        cid = st.session_state.eligible_content_ids.pop()
        # ← use audit_col, not aud_col!
        judged_by = audit_col.distinct("intern_id", {"content_id": cid})
        if len(judged_by) < MAX_AUDITORS and intern_id not in judged_by:
            st.session_state.eligible_id    = cid
            st.session_state.deadline       = time.time() + TIMER_SECONDS
            st.session_state.assigned_time  = datetime.now(timezone.utc)
            return
    st.session_state.eligible_id = None


# === Load Content Initially ===
if st.session_state.eligible_id is None:
    assign_new_content()
if st.session_state.eligible_id is None:
    st.success("✅ All content audited!")
    st.stop()

cid = st.session_state.eligible_id
remaining = int(st.session_state.deadline - time.time())

# === Fetch Content & QA ===
@st.cache_data(ttl=300)
def fetch_content_qa(cid):
    content = content_col.find_one({"content_id": cid})
    qa_doc = qa_col.find_one({"content_id": cid})
    return content, qa_doc

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
    now = datetime.now(timezone.utc)
    time_taken = (now - st.session_state.assigned_time).total_seconds()

    for entry in judgments:
        entry.update({
            "content_id": cid,
            "intern_id": intern_id,
            "timestamp": now,
            "assigned_at": st.session_state.assigned_time,
            "time_taken": time_taken,
            "length": "short"
        })
        if entry["judgment"] == "Doubt":
            doubt_col.insert_one(entry)
        else:
            audit_col.insert_one(entry)

    st.success(f"✅ Judgments saved in {time_taken:.1f}s")

if next_:
    for key in list(st.session_state.keys()):
        if key.startswith("j_"):
            del st.session_state[key]
    st.session_state.current_content_id = None
    assign_new_content()
    st.rerun()
