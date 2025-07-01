
import streamlit as st
import streamlit.components.v1 as components
from datetime import datetime, timezone
import time
import random
from pymongo import InsertOne
from pymongo.errors import BulkWriteError

TIMER_SECONDS = 60 * 7
MAX_AUDITORS = 5

def handle_short_queue(intern_id, db):
    """Handle the existing short Q&A auditing functionality"""
    
    content_col = db["Content"]
    qa_col = db["QA_pairs"]
    audit_col = db["audit_logs"]
    doubt_col = db["doubt_logs"]
    skip_col = db["skipped_logs"]
    assign_col = db["assignment_placeholders"]

    st.subheader("📝 Short Q&A Auditing")
    st.markdown("Audit short question-answer pairs for correctness.")
    
    # Back button
    if st.button("⬅️ Back to Queue Selection", key="back_to_main"):
        st.session_state.current_page = "queue_selection"
        st.rerun()

    # Initialize session state
    for key in ["eligible_id", "deadline", "assigned_time", "judged",
                "auto_skip_triggered", "current_content_id",
                "eligible_content_ids", "timer_expired", "submitted"]:
        if key not in st.session_state:
            st.session_state[key] = None if key in ["eligible_id", "current_content_id"] else False

    # Build candidate queue
    if "candidate_queue" not in st.session_state:
        st.session_state.candidate_queue = build_candidate_queue(intern_id, qa_col, audit_col, skip_col, assign_col)

    # Check if queue is empty
    if not st.session_state.candidate_queue:
        st.success("✅ All short content audited!")
        return

    # Assign new content if needed
    if st.session_state.eligible_id is None:
        assign_new_content(intern_id, st.session_state.candidate_queue, assign_col, db)

    if st.session_state.eligible_id is None:
        st.success("✅ All short content audited!")
        return

    cid = st.session_state.eligible_id
    remaining = int(st.session_state.deadline - time.time())

    # Fetch content and QA
    content, qa_doc = fetch_content_qa(cid, content_col, qa_col)
    qa_pairs = qa_doc.get("questions", {}).get("short", []) if qa_doc else []

    # Validate content
    content_text = content.get("content_text", "").strip() if content and isinstance(content.get("content_text"), str) else ""
    qa_valid = (
        qa_doc and
        isinstance(qa_doc.get("questions", {}).get("short"), list) and
        all("question" in q and "answer" in q for q in qa_doc["questions"]["short"])
    )

    if not content or not content_text or not qa_valid:
        skip_invalid_content(intern_id, cid, skip_col, assign_col)
        st.warning(f"⚠️ Skipping ID {cid} — content or valid short QA missing.")
        st.session_state.current_content_id = None
        assign_new_content(intern_id, st.session_state.candidate_queue, assign_col, db)
        st.rerun()

    # Handle timeout
    if remaining <= 0 and not st.session_state.submitted:
        handle_timeout(intern_id, cid, skip_col, assign_col)
        st.session_state.timer_expired = True
        st.rerun()

    # Timer expired screen
    if st.session_state.timer_expired:
        st.title("⏰ Time Expired")
        st.warning("This content ID has been skipped due to timeout.")
        if st.button("🔄 Fetch New Content"):
            reset_session_state()
            st.rerun()
        return

    # Display timer
    display_timer(remaining)

    # Reset radios if content changes
    if st.session_state.current_content_id != cid:
        for i in range(len(qa_pairs)):
            st.session_state[f"j_{i}"] = None
        st.session_state.current_content_id = cid
        st.session_state.submitted = False

    # UI Layout
    left, right = st.columns(2)

    with left:
        st.subheader(f"📄 Content ID: {cid}")
        st.markdown(f"<div class='passage-box'>{content_text}</div>", unsafe_allow_html=True)

    with right:
        st.subheader("❓ Short Q&A Pairs")

        if not st.session_state.submitted:
            with st.form("judgment_form"):
                for i, pair in enumerate(qa_pairs):
                    st.markdown(f"**Q{i+1}:** {pair['question']}")
                    st.markdown(f"**A{i+1}:** {pair['answer']}")
                    st.radio("", ["Correct", "Incorrect", "Doubt"], key=f"j_{i}")
                    st.markdown("---")

                form_submitted = st.form_submit_button("✅ Submit Judgments")

            if form_submitted:
                missing = [
                    i for i in range(len(qa_pairs))
                    if st.session_state.get(f"j_{i}") not in ("Correct", "Incorrect", "Doubt")
                ]
                if missing:
                    st.error("⚠️ Please answer every question before submitting.")
                else:
                    judgments = [
                        {
                            "qa_index": i,
                            "question": qa_pairs[i]["question"],
                            "answer": qa_pairs[i]["answer"],
                            "judgment": st.session_state[f"j_{i}"]
                        }
                        for i in range(len(qa_pairs))
                    ]
                    handle_submit(judgments, intern_id, cid, audit_col, doubt_col, assign_col)

        else:
            st.success("✅ Judgments submitted successfully!")

    # Next button
    if st.button("➡️ Next"):
        handle_next(intern_id, cid, skip_col)

def build_candidate_queue(intern_id, qa_col, audit_col, skip_col, assign_col):
    """Build a queue of eligible content IDs"""
    # Get all content IDs
    all_content_ids = set(qa_col.distinct("content_id"))
    
    # Get already audited by this intern
    seen = set(audit_col.distinct("content_id", {"intern_id": intern_id}))
    
    # Get skipped by this intern
    skipped = set(skip_col.distinct("content_id", {"intern_id": intern_id}))
    
    # Get reserved by this intern
    reserved = set(assign_col.distinct("content_id", {"intern_id": intern_id}))
    
    # Filter eligible IDs
    eligible = []
    for cid in all_content_ids:
        if cid in seen or cid in skipped or cid in reserved:
            continue
        
        # Check if already has max auditors
        auditor_count = len(audit_col.distinct("intern_id", {"content_id": cid}))
        if auditor_count >= MAX_AUDITORS:
            continue
            
        eligible.append(cid)
    
    random.shuffle(eligible)
    return eligible

def assign_new_content(intern_id, candidate_queue, assign_col, db):
    """Assign new content to intern"""
    if not candidate_queue:
        st.session_state.eligible_id = None
        return

    cid = candidate_queue.pop(0)
    
    # Reserve the content
    assign_col.insert_one({
        "content_id": cid,
        "intern_id": intern_id,
        "assigned_at": datetime.now(timezone.utc)
    })

    st.session_state.eligible_id = cid
    st.session_state.deadline = time.time() + TIMER_SECONDS
    st.session_state.assigned_time = datetime.now(timezone.utc)

def fetch_content_qa(cid, content_col, qa_col):
    """Fetch content and QA documents"""
    content = content_col.find_one({"content_id": cid})
    qa_doc = qa_col.find_one({"content_id": cid})
    return content, qa_doc

def skip_invalid_content(intern_id, cid, skip_col, assign_col):
    """Skip invalid content"""
    skip_col.insert_one({
        "intern_id": intern_id,
        "content_id": cid,
        "status": "missing_or_invalid",
        "timestamp": datetime.now(timezone.utc)
    })
    assign_col.delete_many({"content_id": cid, "intern_id": intern_id})

def handle_timeout(intern_id, cid, skip_col, assign_col):
    """Handle timeout for content"""
    skip_col.insert_one({
        "intern_id": intern_id,
        "content_id": cid,
        "status": "timeout",
        "timestamp": datetime.now(timezone.utc)
    })
    assign_col.delete_many({"content_id": cid, "intern_id": intern_id})

def reset_session_state():
    """Reset session state for new content"""
    st.session_state.timer_expired = False
    st.session_state.eligible_id = None
    st.session_state.current_content_id = None
    st.session_state.submitted = False

def display_timer(remaining):
    """Display countdown timer"""
    components.html(
        f"""
        <div style="
            display: flex;
            justify-content: center;
            align-items: center;
            height: 80px;
            margin-bottom: 1rem;
        ">
        <div id="timer" style="
            font-family: monospace;
            font-size: 22px;
            font-weight: bold;
            color: white;
            background-color: #212121;
            padding: 10px 20px;
            border-radius: 8px;
            border: 2px solid #00bcd4;
        ">
            ⏱ Time Left: {remaining//60:02d}:{remaining%60:02d}
        </div>
        </div>
        <script>
        let total = {remaining};
        const el = document.getElementById("timer");
        const interval = setInterval(() => {{
            let m = Math.floor(total / 60),
                s = total % 60;
            el.textContent = `⏱ Time Left: ${{m.toString().padStart(2,"0")}}:${{s.toString().padStart(2,"0")}}`;
            total--;
            if (total < 0) clearInterval(interval);
        }}, 1000);
        </script>
        """,
        height=100,
    )

def handle_submit(judgments, intern_id, cid, audit_col, doubt_col, assign_col):
    """Handle submission of judgments"""
    if st.session_state.submitted:
        return

    st.session_state.submitted = True
    now = datetime.now(timezone.utc)
    time_taken = (now - st.session_state.assigned_time).total_seconds()

    with st.spinner("Saving your judgments…"):
        # Remove reservation
        assign_col.delete_many({"content_id": cid, "intern_id": intern_id})

        # Bulk insert
        audit_ops, doubt_ops = [], []
        for entry in judgments:
            doc = {
                "content_id": cid,
                "intern_id": intern_id,
                "qa_index": entry["qa_index"],
                "question": entry["question"],
                "answer": entry["answer"],
                "judgment": entry["judgment"],
                "timestamp": now,
                "assigned_at": st.session_state.assigned_time,
                "time_taken": time_taken,
                "length": "short",
            }
            (doubt_ops if entry["judgment"] == "Doubt" else audit_ops).append(InsertOne(doc))

        if audit_ops:
            try:
                audit_col.bulk_write(audit_ops, ordered=False)
            except BulkWriteError as bwe:
                pass
        if doubt_ops:
            try:
                doubt_col.bulk_write(doubt_ops, ordered=False)
            except BulkWriteError as bwe:
                pass

    st.success(f"✅ Judgments saved in {time_taken:.1f}s")

def handle_next(intern_id, cid, skip_col):
    """Handle next button click"""
    if not st.session_state.submitted:
        skip_col.insert_one({
            "intern_id": intern_id,
            "content_id": cid,
            "status": "manual_skip",
            "timestamp": datetime.now(timezone.utc)
        })

    # Clear session state
    for key in list(st.session_state.keys()):
        if key.startswith("j_"):
            del st.session_state[key]
    st.session_state.current_content_id = None
    st.session_state.submitted = False
    st.session_state.eligible_id = None
    st.rerun()
