import streamlit as st
from pymongo import MongoClient
from datetime import datetime, timezone
import random
import time
import re
from auth0_component import login_button
import streamlit.components.v1 as components

# === INITIAL STATE TRACKING ===
if "profile_step" not in st.session_state:
    st.session_state.profile_step = 1
if "prev_auth0_id" not in st.session_state:
    st.session_state.prev_auth0_id = None
if "auth_exchanged" not in st.session_state:
    st.session_state.auth_exchanged = False

# === CONFIG & STYLING ===
st.set_page_config(page_title="JNANA Auditing", layout="wide", initial_sidebar_state="collapsed")
st.markdown("""
    <style>
    .passage-box { /* ...styles... */ }
    div.stButton > button { /* ...styles... */ }
    div.stButton > button:hover { /* ...styles... */ }
    </style>
""", unsafe_allow_html=True)

# === MONGO CONNECTION & INDEXES ===
@st.cache_resource
def get_client():
    client = MongoClient(st.secrets["mongo_uri"])
    client.admin.command("ping")
    db = client["Tel_QA"]
    db["users"].create_index("auth0_id", unique=True)
    db["audit_logs"].create_index("content_id")
    db["skipped_logs"].create_index("content_id")
    return client

client      = get_client()
users_col   = client["Tel_QA"]["users"]
content_col = client["Tel_QA"]["Content"]
qa_col      = client["Tel_QA"]["QA_pairs"]
audit_col   = client["Tel_QA"]["audit_logs"]
doubt_col   = client["Tel_QA"]["doubt_logs"]
skip_col    = client["Tel_QA"]["skipped_logs"]

TIMER_SECONDS = 60
MAX_AUDITORS  = 5

# === FULL-PAGE AUTH0 LOGIN REDIRECT ===
params = st.query_params
if not st.session_state.auth_exchanged and "code" not in params:
    login_url = (
        f"https://{st.secrets['AUTH0_DOMAIN']}/authorize?"
        f"client_id={st.secrets['AUTH0_CLIENT_ID']}&"
        "redirect_uri=https://audit-tooltest.streamlit.app/&"
        "response_type=code&scope=openid%20profile%20email"
    )
    st.markdown(f'<meta http-equiv="refresh" content="0;url={login_url}"/>', unsafe_allow_html=True)
    st.stop()

# === AUTH0 TOKEN EXCHANGE ===
try:
    user_info = login_button(
        st.secrets["AUTH0_CLIENT_ID"],
        domain=st.secrets["AUTH0_DOMAIN"],
        logout_url=(
            f"https://{st.secrets['AUTH0_DOMAIN']}/v2/logout?"
            f"client_id={st.secrets['AUTH0_CLIENT_ID']}&"
            "returnTo=https://audit-tooltest.streamlit.app/"
        )
    )
    st.session_state.auth_exchanged = True
except Exception as e:
    st.error("❌ Auth0 Login Failed.")
    st.exception(e)
    st.stop()

# — detect logout and clear state —
if st.session_state.prev_auth0_id and not user_info:
    st.session_state.clear()
    st.success("✅ Successfully logged out.")
    st.stop()

# — store on login for next-round detection —
if user_info:
    st.session_state.prev_auth0_id = user_info.get("sub")

# — enforce login —
if not user_info:
    st.warning("Please log in to continue.")
    st.stop()

# === EXTRACT USER INFO ===
auth0_id   = user_info.get("sub")
given_name = user_info.get("given_name", "")
email      = user_info.get("email", "")
picture    = user_info.get("picture", "")

# === INTERN-ID GENERATOR ===
def generate_intern_ids(first, last):
    first_clean = re.sub(r'[^a-zA-Z]', '', first).lower()
    last_clean  = re.sub(r'[^a-zA-Z]', '', last).lower()
    return [
        first_clean + last_clean,
        first_clean[:2] + last_clean,
        first_clean + last_clean[:2],
        first_clean[:3] + last_clean,
        first_clean + last_clean[:3]
    ]

# === FIRST-TIME SIGNUP WIZARD ===
existing_user = users_col.find_one({"auth0_id": auth0_id})
if existing_user is None:
    # STEP 1: Basic info
    if st.session_state.profile_step == 1:
        st.subheader("👤 Complete Your Profile")
        fn = st.text_input("First Name", value=given_name)
        ln = st.text_input("Last Name")
        phone = st.text_input("Phone Number")
        if st.button("➡️ Next"):
            if not (fn and ln and phone):
                st.warning("⚠️ Please fill all fields before proceeding.")
            elif not re.fullmatch(r"\+?\d{7,15}", phone):
                st.error("❌ Invalid phone format. Use digits, optional leading +.")
            else:
                st.session_state.first_name   = fn
                st.session_state.last_name    = ln
                st.session_state.phone_number = phone
                st.session_state.profile_step  = 2
                st.rerun()
        st.stop()
    # STEP 2: Intern ID
    if st.session_state.profile_step == 2:
        st.subheader("👤 Choose Your Intern ID")
        ids = generate_intern_ids(
            st.session_state.first_name,
            st.session_state.last_name
        )
        selected = st.radio("Select an Intern ID", ids)
        if selected and st.button("✅ Submit Profile Information"):
            users_col.insert_one({
                "auth0_id":   auth0_id,
                "first_name": st.session_state.first_name,
                "last_name":  st.session_state.last_name,
                "phone":      st.session_state.phone_number,
                "email":      email,
                "picture":    picture,
                "intern_id":  selected,
                "created_at": datetime.utcnow()
            })
            # clean up wizard state
            del st.session_state.first_name
            del st.session_state.last_name
            del st.session_state.phone_number
            st.session_state.profile_step = 1
            st.success("✅ Profile saved! Reloading…")
            st.rerun()
        st.stop()

# === MAIN APP UI ===
intern_id = existing_user["intern_id"]
st.title(f"🧐 JNANA - Short Q&A Auditing Tool")
st.markdown(f"Hi {existing_user['first_name']} {existing_user['last_name']}, Intern ID: {intern_id}")

# === CACHED CONTENT FETCH ===
@st.cache_data(ttl=300)
def fetch_content_qa(cid):
    content = content_col.find_one({"content_id": cid})
    qa_doc  = qa_col.find_one({"content_id": cid})
    return content, qa_doc

# INIT SESSION
for key in ["eligible_id","deadline","assigned_time","judged",
            "auto_skip_triggered","current_content_id",
            "eligible_content_ids","timer_expired"]:
    if key not in st.session_state:
        st.session_state[key] = None if key in ["eligible_id","current_content_id"] else False

# HANDLE TIMEOUT
if st.session_state.timer_expired:
    st.title("⏰ Time Expired")
    st.warning("This content ID has been skipped due to timeout.")
    if st.button("🔄 Fetch New Content"):
        st.session_state.timer_expired = False
        st.session_state.eligible_id = None
        st.session_state.current_content_id = None
        st.rerun()
    st.stop()

# ASSIGN NEW CONTENT
if st.session_state.eligible_id is None:
    assign_new_content()
if st.session_state.eligible_id is None:
    st.success("✅ All content audited!")
    st.stop()

cid = st.session_state.eligible_id
remaining = int(st.session_state.deadline - time.time())

# FETCH & VALIDATE CONTENT
content, qa_doc = fetch_content_qa(cid)
qa_pairs = qa_doc.get("questions",{}).get("short",[]) if qa_doc else []
content_text = content.get("content_text","") if content else ""
qa_valid = (
    qa_doc and isinstance(qa_doc.get("questions",{}).get("short"), list) and
    all("question" in q and "answer" in q for q in qa_doc["questions"]["short"])
)
if not content_text.strip() or not qa_valid:
    skip_col.insert_one({
        "intern_id": intern_id,
        "content_id": cid,
        "status": "missing_or_invalid",
        "assigned_at": st.session_state.assigned_time,
        "timestamp": datetime.utcnow()
    })
    st.warning(f"⚠️ Skipping ID {cid} — content or valid short QA missing.")
    st.session_state.current_content_id = None
    assign_new_content()
    st.rerun()

# TIMEOUT CHECK
if remaining <= 0:
    skip_col.insert_one({
        "intern_id": intern_id,
        "content_id": cid,
        "status": "timeout",
        "assigned_at": st.session_state.assigned_time,
        "timestamp": datetime.utcnow()
    })
    st.session_state.timer_expired = True
    st.rerun()

# DISPLAY TIMER
st.components.v1.html(f"""
<div style='text-align:center;margin-bottom:1rem;font-size:22px;font-weight:bold;color:white;
    background-color:#212121;padding:10px 20px;border-radius:8px;width:fit-content;margin:auto;
    border:2px solid #00bcd4;font-family:monospace;'>
  ⏱ Time Left: {remaining//60:02d}:{remaining%60:02d}
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

# UI LAYOUT & AUDIT LOOP
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
        sel = st.radio("", ["Correct","Incorrect","Doubt"], key=f"j_{i}")
        judgments.append({
            "qa_index": i,
            "question": pair['question'],
            "answer": pair['answer'],
            "judgment": sel
        })
        st.markdown("---")

# SUBMISSION & BULK INSERT
submit = st.button("✅ Submit")
next_ = st.button("➡️ Next")
if submit:
    now = datetime.utcnow()
    elapsed = (now - st.session_state.assigned_time).total_seconds()
    for e in judgments:
        e.update({
            "content_id": cid,
            "intern_id": intern_id,
            "timestamp": now,
            "assigned_at": st.session_state.assigned_time,
            "time_taken": elapsed,
            "length": "short"
        })
    audit_entries = [e for e in judgments if e['judgment'] != 'Doubt']
    doubt_entries = [e for e in judgments if e['judgment'] == 'Doubt']
    if audit_entries:
        audit_col.insert_many(audit_entries)
    if doubt_entries:
        doubt_col.insert_many(doubt_entries)
    st.success(f"✅ Judgments saved in {elapsed:.1f}s")
if next_:
    for k in list(st.session_state.keys()):
        if k.startswith("j_"):
            del st.session_state[k]
    st.session_state.eligible_id = None
    st.rerun()
