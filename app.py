import streamlit as st
from pymongo import MongoClient
from datetime import datetime, timezone
import random
import time
from streamlit_auth0 import login_button

# === CONFIG ===
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

# === MongoDB Connection ===
@st.cache_resource
def get_client():
    client = MongoClient(st.secrets["mongo_uri"])
    client.admin.command("ping")
    return client

client = get_client()
db = client["Tel_QA"]
content_col = db["Content"]
qa_col = db["QA_pairs"]
audit_col = db["audit_logs"]
doubt_col = db["doubt_logs"]
skip_col = db["skipped_logs"]

TIMER_SECONDS = 60
MAX_AUDITORS = 5

users_col = db["users"]  # New users collection

# Auth0 Login
try:
    auth_result = login_button(
        domain=st.secrets["AUTH0_DOMAIN"],
        client_id=st.secrets["AUTH0_CLIENT_ID"],
        client_secret=st.secrets["AUTH0_CLIENT_SECRET"],
        audience=st.secrets["AUTH0_AUDIENCE"]
    )
except Exception as e:
    st.error("❌ Auth0 Login Failed. Check secrets.toml and Auth0 settings.")
    st.exception(e)
    st.stop()

import streamlit.components.v1 as components

logout_url = f"https://{st.secrets['AUTH0_DOMAIN']}/v2/logout?client_id={st.secrets['AUTH0_CLIENT_ID']}&returnTo=https://audittool.streamlit.app"
st.markdown(
    f"<div style='text-align:right;'><a href='{logout_url}' target='_self'>🔓 Logout</a></div>",
    unsafe_allow_html=True
)


user_info = auth_result["user"]
auth0_id = user_info.get("sub")
email = user_info.get("email")
picture = user_info.get("picture", "")
name = user_info.get("name", "")
given_name = user_info.get("given_name", name.split()[0] if name else "")

def generate_intern_ids(first, last):
    base = (first[:2] + last[:2]).lower()
    return [base + str(i) for i in range(10, 100) if len(base + str(i)) == 5][:5]

existing_user = users_col.find_one({"auth0_id": auth0_id})
if not existing_user:
    st.subheader("👤 Complete Your Profile")

    first_name = st.text_input("First Name", value=given_name)
    last_name = st.text_input("Last Name")
    phone_number = st.text_input("Phone Number")

    intern_id_options = []
    selected_intern_id = None
    if first_name and last_name:
        intern_id_options = generate_intern_ids(first_name, last_name)
        selected_intern_id = st.radio("Choose Intern ID", intern_id_options)

    if first_name and last_name and phone_number and selected_intern_id:
        if st.button("Submit Profile"):
            users_col.insert_one({
                "auth0_id": auth0_id,
                "first_name": first_name,
                "last_name": last_name,
                "phone_number": phone_number,
                "email": email,
                "intern_id": selected_intern_id,
                "picture": picture,
                "created_at": datetime.utcnow()
            })
            st.success("✅ Profile completed successfully. Reloading...")
            st.experimental_rerun()
    st.stop()


# === Intern Login ===
intern_id = existing_user["intern_id"]
st.title("🧐 JNANA - Short Q&A Auditing Tool")
st.markdown(f"Hi {existing_user['first_name']} {existing_user['last_name']}, Intern ID: {intern_id}")



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
        judged_by = audit_col.distinct("intern_id", {"content_id": cid})
        if len(judged_by) < MAX_AUDITORS and intern_id not in judged_by:
            st.session_state.eligible_id = cid
            st.session_state.deadline = time.time() + TIMER_SECONDS
            st.session_state.assigned_time = datetime.now(timezone.utc)
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
