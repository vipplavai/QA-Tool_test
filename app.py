import streamlit as st
from pymongo import MongoClient, errors
from datetime import datetime, timezone
import random
import time

# === Streamlit Page Setup ===
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
        max-height: 400px;
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

# === MongoDB Setup (only mongo_uri from secrets) ===
try:
    client = MongoClient(st.secrets["mongo_uri"])
    client.admin.command("ping")
except Exception:
    st.error("❌ Cannot connect to MongoDB. Check `mongo_uri` in secrets.toml.")
    st.stop()

db = client["Tel_QA"]  # hardcoded
content_col = db["Content"]
qa_col = db["QA_pairs"]
audit_col = db["audit_logs"]
doubt_col = db["doubt_logs"]
skip_col = db["skipped_logs"]

# === Intern Login ===
st.title("🧐 JNANA - Q&A Auditing Tool")
intern_id = st.text_input("Enter your Intern ID").strip()
if not intern_id:
    st.warning("Please enter your Intern ID.")
    st.stop()

# === Timer Config ===
TIMER_SECONDS = 600
MAX_AUDITORS = 5

# === State Initialization ===
if "eligible_id" not in st.session_state:
    st.session_state.eligible_id = None
if "deadline" not in st.session_state:
    st.session_state.deadline = None
if "assigned_time" not in st.session_state:
    st.session_state.assigned_time = None
if "judged" not in st.session_state:
    st.session_state.judged = False
if "auto_skip_triggered" not in st.session_state:
    st.session_state.auto_skip_triggered = False

# === Assign New Content ===
def assign_new_content():
    all_ids = qa_col.distinct("content_id")
    random.shuffle(all_ids)
    for cid in all_ids:
        judged_by = audit_col.distinct("intern_id", {"content_id": cid})
        if len(judged_by) < MAX_AUDITORS and intern_id not in judged_by:
            st.session_state.eligible_id = cid
            st.session_state.deadline = time.time() + TIMER_SECONDS
            st.session_state.assigned_time = datetime.now(timezone.utc)
            st.session_state.judged = False
            st.session_state.auto_skip_triggered = False
            return

if st.session_state.eligible_id is None:
    assign_new_content()
if st.session_state.eligible_id is None:
    st.success("✅ All content audited!")
    st.stop()

cid = st.session_state.eligible_id
content = content_col.find_one({"content_id": cid})
qa_data = qa_col.find_one({"content_id": cid})

# === Handle Missing Content ===
if not content or not qa_data or "questions" not in qa_data or "short" not in qa_data["questions"]:
    skip_col.insert_one({
        "intern_id": intern_id,
        "content_id": cid,
        "status": "missing",
        "assigned_at": st.session_state.assigned_time,
        "timestamp": datetime.now(timezone.utc)
    })
    st.warning(f"⚠️ Skipped ID {cid} due to missing content/Q&A.")
    assign_new_content()
    st.rerun()

# === Auto-skip if Timer Expires ===
remaining = int(st.session_state.deadline - time.time())
if remaining <= 0 and not st.session_state.judged:
    if not st.session_state.auto_skip_triggered:
        st.session_state.auto_skip_triggered = True
        skip_col.insert_one({
            "intern_id": intern_id,
            "content_id": cid,
            "status": "timeout",
            "assigned_at": st.session_state.assigned_time,
            "timestamp": datetime.now(timezone.utc)
        })
        st.warning(f"⏰ Time expired for Content ID: {cid} — skipped automatically.")
        assign_new_content()
        st.rerun()

# === Timer Display ===
st.markdown(f"""
<div style='position:sticky;top:0;z-index:1000;
            text-align:center;background:#212121;color:white;
            padding:0.5rem;font-family:monospace;'>
  ⏱ Time Left: {remaining//60:02d}:{remaining%60:02d}
</div>
""", unsafe_allow_html=True)

# === Q&A Layout ===
qa_short = qa_data["questions"].get("short", [])
qa_medium = qa_data["questions"].get("medium", [])
qa_long = qa_data["questions"].get("long", [])

left, right = st.columns(2)
with left:
    st.subheader(f"📄 Content ID: {cid}")
    st.markdown(f"<div class='passage-box'>{content.get('content_text', '') or content.get('Content', '')}</div>", unsafe_allow_html=True)

with right:
    with st.form("judgment_form"):
        judgments = []

        if qa_short:
            st.subheader("Short Q&A")
            for i, pair in enumerate(qa_short):
                st.markdown(f"**Q{i+1}:** {pair['question']}")
                st.markdown(f"**A{i+1}:** {pair['answer']}")
                j = st.radio("", ["Correct", "Incorrect", "Doubt"], key=f"s_{i}")
                judgments.append({**pair, "qa_index": i, "length": "short", "judgment": j})

        if qa_medium:
            st.subheader("Medium Q&A")
            for i, pair in enumerate(qa_medium):
                st.markdown(f"**Q{i+1}:** {pair['question']}")
                st.markdown(f"**A{i+1}:** {pair['answer']}")
                j = st.radio("", ["Correct", "Incorrect", "Doubt"], key=f"m_{i}")
                judgments.append({**pair, "qa_index": i, "length": "medium", "judgment": j})

        if qa_long:
            st.subheader("Long Q&A")
            for i, pair in enumerate(qa_long):
                st.markdown(f"**Q{i+1}:** {pair['question']}")
                st.markdown(f"**A{i+1}:** {pair['answer']}")
                j = st.radio("", ["Correct", "Incorrect", "Doubt"], key=f"l_{i}")
                judgments.append({**pair, "qa_index": i, "length": "long", "judgment": j})

        # Submit button is only enabled if all judgments made
        can_submit = all(j["judgment"] in ["Correct", "Incorrect", "Doubt"] for j in judgments)
        submit = st.form_submit_button("✅ Submit and Next", disabled=not can_submit)
        if not can_submit:
            st.info("📝 Please judge all Q&A pairs before submitting.")

    if submit:
        now = datetime.now(timezone.utc)
        for entry in judgments:
            doc = {
                **entry,
                "content_id": cid,
                "intern_id": intern_id,
                "timestamp": now,
                "assigned_at": st.session_state.assigned_time,
            }
            if entry["judgment"] == "Doubt":
                doubt_col.insert_one(doc)
            else:
                audit_col.insert_one(doc)
        st.success("✅ Judgments submitted.")
        st.session_state.judged = True
        assign_new_content()
        st.rerun()

# === Manual Skip Button ===
if st.button("➡️ Skip"):
    skip_col.insert_one({
        "intern_id": intern_id,
        "content_id": cid,
        "status": "manual_skip",
        "assigned_at": st.session_state.assigned_time,
        "timestamp": datetime.now(timezone.utc)
    })
    st.info(f"➡️ Manually skipped ID {cid}")
    assign_new_content()
    st.rerun()
