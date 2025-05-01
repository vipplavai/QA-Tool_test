import streamlit as st
from pymongo import MongoClient
from datetime import datetime, timezone
import random
import time

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

# === CONSTANTS ===
TIMER_SECONDS = 600
MAX_AUDITORS = 5

# === Intern Login ===
st.title("🧐 JNANA - Short Q&A Auditing Tool")
intern_id = st.text_input("Enter your Intern ID").strip()
if not intern_id:
    st.stop()

# === Session State Init ===
for key in ["eligible_id", "deadline", "assigned_time", "judged",
            "auto_skip_triggered", "current_content_id",
            "eligible_content_ids", "timer_expired", "submitted"]:
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
        st.session_state.submitted = False
        st.session_state.judged = False
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
            st.session_state.judged = False
            st.session_state.auto_skip_triggered = False
            st.session_state.submitted = False
            return
    st.session_state.eligible_id = None

# === Load Content Initially ===
if st.session_state.eligible_id is None:
    assign_new_content()
if st.session_state.eligible_id is None:
    st.success("✅ All content audited!")
    st.stop()

cid = st.session_state.eligible_id

# === Fetch Content & QA ===
@st.cache_data(ttl=300)
def fetch_content_qa(cid):
    return content_col.find_one({"content_id": cid}), qa_col.find_one({"content_id": cid})

content, qa_doc = fetch_content_qa(cid)
qa_pairs = qa_doc.get("questions", {}).get("short", []) if qa_doc else []

# === Reset radios if content changes ===
if st.session_state.current_content_id != cid:
    for i in range(len(qa_pairs)):
        st.session_state[f"j_{i}"] = None
    st.session_state.current_content_id = cid

# === Handle Missing ===
if not content or not qa_pairs:
    skip_col.insert_one({
        "intern_id": intern_id,
        "content_id": cid,
        "status": "missing",
        "assigned_at": st.session_state.assigned_time,
        "timestamp": datetime.now(timezone.utc)
    })
    st.warning(f"⚠️ Missing content or Q&A. Skipping ID {cid}")
    st.session_state.current_content_id = None
    assign_new_content()
    st.rerun()

# === Auto Skip on Timeout ===
remaining = int(st.session_state.deadline - time.time())
if remaining <= 0 and not st.session_state.judged:
    if not st.session_state.auto_skip_triggered:
        skip_col.insert_one({
            "intern_id": intern_id,
            "content_id": cid,
            "status": "timeout",
            "assigned_at": st.session_state.assigned_time,
            "timestamp": datetime.now(timezone.utc)
        })
        st.session_state.auto_skip_triggered = True
        st.session_state.timer_expired = True
    st.rerun()

# === TIMER DISPLAY ===
st.components.v1.html(f"""
<div style='
    text-align: center;
    margin-bottom: 1rem;
    font-size: 22px;
    font-weight: bold;
    color: #ffffff;
    background-color: #212121;
    padding: 10px 20px;
    border-radius: 8px;
    width: fit-content;
    margin-left: auto;
    margin-right: auto;
    border: 2px solid #00bcd4;
    font-family: monospace;
'>
  ⏱ Time Left: <span id="timer">10:00</span>
  <script>
    let total = {remaining};
    const el = document.getElementById('timer');
    const interval = setInterval(() => {{
      let m = Math.floor(total / 60);
      let s = total % 60;
      el.textContent = `${{m.toString().padStart(2,'0')}}:${{s.toString().padStart(2,'0')}}`;
      total--;
      if (total < 0) clearInterval(interval);
    }}, 1000);
  </script>
</div>
""", height=80)

# === LAYOUT ===
left, right = st.columns(2)

with left:
    st.subheader(f"📄 Content ID: {cid}")
    st.markdown(f"<div class='passage-box'>{content['content_text']}</div>", unsafe_allow_html=True)

with right:
    st.subheader("❓ Short Q&A Pairs")
    judgments = []
    unanswered = False

    for i, pair in enumerate(qa_pairs):
        st.markdown(f"**Q{i+1}:** {pair['question']}")
        st.markdown(f"**A{i+1}:** {pair['answer']}")
        selected = st.radio(
            label="",
            options=["Correct", "Incorrect", "Doubt"],
            key=f"j_{i}",
            index=None
        )
        if selected is None:
            unanswered = True
        judgments.append({
            "qa_index": i,
            "question": pair["question"],
            "answer": pair["answer"],
            "judgment": selected
        })
        st.markdown("---")

    if unanswered:
        st.info("📝 Please judge all Q&A pairs before submitting.")

# === BUTTONS ===
submit_btn = st.button("✅ Submit")
next_btn = st.button("➡️ Next", disabled=not st.session_state.submitted)

# === HANDLE SUBMIT ===
if submit_btn and not unanswered:
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
    st.session_state.submitted = True
    st.session_state.judged = True

# === HANDLE NEXT ===
if next_btn and st.session_state.submitted:
    for key in list(st.session_state.keys()):
        if key.startswith("j_"):
            del st.session_state[key]
    st.session_state.current_content_id = None
    st.session_state.submitted = False
    st.session_state.judged = False
    assign_new_content()
    st.rerun()
