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
        max-height: 700px;
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

# === MongoDB Setup ===
try:
    client = MongoClient(st.secrets["mongo_uri"])
    client.admin.command("ping")
except Exception:
    st.error("❌ Cannot connect to MongoDB. Check `mongo_uri` in Streamlit Secrets.")
    st.stop()

db = client["Tel_QA"]
content_col = db["Content"]
qa_col = db["QA_pairs"]
audit_col = db["audit_logs"]
doubt_col = db["doubt_logs"]
skip_col = db["skipped_logs"]

# === Constants ===
TIMER_SECONDS = 600
MAX_AUDITORS = 5

# === Intern Login ===
st.title("🧐 JNANA - Short Q&A Auditing Tool")
intern_id = st.text_input("Enter your Intern ID").strip()
if not intern_id:
    st.warning("Please enter your Intern ID.")
    st.stop()

# === Session State Init ===
for key in ["eligible_id", "deadline", "assigned_time", "judged", "auto_skip_triggered", "current_content_id"]:
    if key not in st.session_state:
        st.session_state[key] = None if key == "eligible_id" or key == "current_content_id" else False

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

# === Load Content and QA ===
cid = st.session_state.eligible_id

# ✅ Clear radios if content_id changes
if st.session_state.current_content_id != cid:
    # Reset previous radio answers to None
    for i in range(20):  # Assuming max 20 Q&A pairs (you can adjust)
        key = f"j_{i}"
        if key in st.session_state:
            st.session_state[key] = None
    st.session_state.current_content_id = cid


content = content_col.find_one({"content_id": cid})
qa_doc = qa_col.find_one({"content_id": cid})
qa_pairs = qa_doc.get("questions", {}).get("short", []) if qa_doc else []

# === Handle Missing Content or QA ===
if not content or not qa_pairs:
    skip_col.insert_one({
        "intern_id": intern_id,
        "content_id": cid,
        "status": "missing",
        "assigned_at": st.session_state.assigned_time,
        "timestamp": datetime.now(timezone.utc)
    })
    st.warning(f"⚠️ Skipping ID {cid} — missing content or Q&A.")
    st.session_state.current_content_id = None
    assign_new_content()
    st.rerun()

# === Auto Skip on Timeout ===
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
        st.warning(f"⏰ Time expired — Skipped ID {cid}.")
        st.session_state.current_content_id = None
        assign_new_content()
        st.rerun()

# === HTML + JS Timer Visual
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

# === Render Content and Form ===
# === Render Content and Form ===
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

    # Info message inside right panel
    if unanswered:
        st.info("📝 Please judge all Q&A pairs before submitting.")

# === Submit button OUTSIDE the right column and form ===
submit_btn = st.button("✅ Submit and Next")

# === Handle Submit Click ===
if submit_btn:
    if unanswered:
        st.warning("⚠️ Please judge all Q&A pairs before submitting.")
    else:
        now = datetime.now(timezone.utc)
        for entry in judgments:
            entry.update({
                "content_id": cid,
                "intern_id": intern_id,
                "timestamp": now,
                "assigned_at": st.session_state.assigned_time,
                "length": "short"
            })
            if entry["judgment"] == "Doubt":
                doubt_col.insert_one(entry)
            else:
                audit_col.insert_one(entry)

        st.success("✅ Judgments submitted.")
        
        # Clear session state for next content
        for key in list(st.session_state.keys()):
            if key.startswith("j_"):
                del st.session_state[key]

        st.session_state.judged = True
        st.session_state.current_content_id = None
        assign_new_content()
        st.rerun()
