
import streamlit as st
import streamlit.components.v1 as components
from datetime import datetime, timezone
import time
import random
from pymongo import InsertOne, UpdateOne
from pymongo.errors import BulkWriteError

TIMER_SECONDS = 60 * 10  # Longer timer for medium/long
MAX_AUDITORS = 3

def handle_medium_long_queue(intern_id, db):
    """Handle medium and long Q&A auditing with metadata"""
    
    content_col = db["Content"] 
    completed_content_col = db["completed_content"]
    qa_col = db["QA_pairs"]
    final_qa_col = db["Final_QA_pairs"]
    medium_long_audit_col = db["medium_long_audits"]
    edit_queue_col = db["edit_queue"]
    notes_col = db["notes"]

    st.subheader("📚 Medium & Long Q&A Auditing")
    st.markdown("Audit medium and long question-answer pairs along with their metadata.")
    
    # Back button
    if st.button("⬅️ Back to Queue Selection", key="back_to_main_ml"):
        st.session_state.current_page = "queue_selection"
        st.rerun()

    # Initialize session state
    for key in ["ml_content_id", "ml_deadline", "ml_assigned_time", "ml_submitted"]:
        if key not in st.session_state:
            st.session_state[key] = None if key == "ml_content_id" else False

    # Build candidate queue for medium/long content
    if "ml_candidate_queue" not in st.session_state:
        st.session_state.ml_candidate_queue = build_ml_candidate_queue(intern_id, qa_col, final_qa_col, medium_long_audit_col)

    # Check if queue is empty
    if not st.session_state.ml_candidate_queue:
        st.success("✅ All medium/long content audited!")
        return

    # Assign new content if needed
    if st.session_state.ml_content_id is None:
        assign_new_ml_content(intern_id, st.session_state.ml_candidate_queue)

    if st.session_state.ml_content_id is None:
        st.success("✅ All medium/long content audited!")
        return

    cid = st.session_state.ml_content_id
    remaining = int(st.session_state.ml_deadline - time.time()) if st.session_state.ml_deadline else TIMER_SECONDS

    # Handle timeout
    if remaining <= 0 and not st.session_state.ml_submitted:
        st.warning("⏰ Time expired for this content. Moving to next...")
        reset_ml_session_state()
        st.rerun()

    # Display timer
    display_ml_timer(remaining)

    # Fetch content and Q&A data
    content_data, qa_data = fetch_ml_content_qa(cid, content_col, completed_content_col, qa_col, final_qa_col)
    
    if not content_data or not qa_data:
        st.warning(f"⚠️ Skipping ID {cid} — content or QA data missing.")
        reset_ml_session_state()
        st.rerun()

    # Get existing notes
    existing_notes = get_existing_notes(cid, notes_col)

    # UI Layout
    display_ml_content(cid, content_data, qa_data, existing_notes, intern_id, notes_col)

    # Handle submission
    if not st.session_state.ml_submitted:
        handle_ml_submission(cid, qa_data, intern_id, medium_long_audit_col, edit_queue_col)

    # Next button
    if st.button("➡️ Next Content"):
        reset_ml_session_state()
        st.rerun()

def build_ml_candidate_queue(intern_id, qa_col, final_qa_col, medium_long_audit_col):
    """Build queue of content IDs with medium/long Q&A"""
    # Get content IDs that have medium or long questions
    ml_content_ids = set()
    
    # From QA_pairs
    qa_docs = qa_col.find({"$or": [
        {"questions.medium": {"$exists": True, "$ne": []}},
        {"questions.long": {"$exists": True, "$ne": []}}
    ]})
    for doc in qa_docs:
        ml_content_ids.add(doc["content_id"])
    
    # From Final_QA_pairs  
    final_docs = final_qa_col.find({"$or": [
        {"questions.medium": {"$exists": True, "$ne": []}},
        {"questions.long": {"$exists": True, "$ne": []}}
    ]})
    for doc in final_docs:
        ml_content_ids.add(doc["content_id"])

    # Filter out already audited by this intern
    audited = set(medium_long_audit_col.distinct("content_id", {"intern_id": intern_id}))
    
    # Filter out those with max auditors
    eligible = []
    for cid in ml_content_ids:
        if cid in audited:
            continue
        auditor_count = len(medium_long_audit_col.distinct("intern_id", {"content_id": cid}))
        if auditor_count >= MAX_AUDITORS:
            continue
        eligible.append(cid)
    
    random.shuffle(eligible)
    return eligible

def assign_new_ml_content(intern_id, candidate_queue):
    """Assign new medium/long content"""
    if not candidate_queue:
        st.session_state.ml_content_id = None
        return

    cid = candidate_queue.pop(0)
    st.session_state.ml_content_id = cid
    st.session_state.ml_deadline = time.time() + TIMER_SECONDS
    st.session_state.ml_assigned_time = datetime.now(timezone.utc)

def fetch_ml_content_qa(cid, content_col, completed_content_col, qa_col, final_qa_col):
    """Fetch content and medium/long Q&A data"""
    # Try both content collections
    content_data = content_col.find_one({"content_id": cid})
    if not content_data:
        content_data = completed_content_col.find_one({"content_id": cid})

    # Try both QA collections  
    qa_data = qa_col.find_one({"content_id": cid})
    if not qa_data:
        qa_data = final_qa_col.find_one({"content_id": cid})

    return content_data, qa_data

def get_existing_notes(content_id, notes_col):
    """Get existing notes for content"""
    notes = list(notes_col.find({"content_id": content_id}).sort("timestamp", 1))
    return notes

def display_ml_timer(remaining):
    """Display timer for medium/long queue"""
    components.html(
        f"""
        <div style="text-align: center; margin-bottom: 1rem;">
            <div style="
                font-family: monospace;
                font-size: 20px;
                font-weight: bold;
                color: white;
                background-color: #1976d2;
                padding: 10px 20px;
                border-radius: 8px;
                display: inline-block;
            ">
                ⏱ Time Left: {remaining//60:02d}:{remaining%60:02d}
            </div>
        </div>
        """,
        height=80
    )

def display_ml_content(content_id, content_data, qa_data, existing_notes, intern_id, notes_col):
    """Display content and Q&A for medium/long auditing"""
    
    # Content section
    st.subheader(f"📄 Content ID: {content_id}")
    content_text = content_data.get("content_text", "") or content_data.get("content", "")
    st.markdown(f"<div class='passage-box'>{content_text}</div>", unsafe_allow_html=True)

    # Metadata section
    metadata = qa_data.get("metadata", {})
    if metadata:
        st.subheader("🏷️ Metadata")
        st.markdown("<div class='metadata-box'>", unsafe_allow_html=True)
        
        col1, col2, col3 = st.columns([2, 1, 1])
        with col1:
            st.write(f"**Topic:** {metadata.get('topic', 'N/A')}")
            st.write(f"**Genre:** {metadata.get('genre', 'N/A')}")
            st.write(f"**Tone:** {metadata.get('tone', 'N/A')}")
        
        with col2:
            metadata_correct = st.button("✅ Metadata Correct", key="metadata_correct")
            metadata_incorrect = st.button("❌ Metadata Incorrect", key="metadata_incorrect")
        
        with col3:
            if st.button("📝 Add Metadata Note", key="metadata_note_btn"):
                add_note_dialog(content_id, intern_id, notes_col, "metadata", "L1")
        
        st.markdown("</div>", unsafe_allow_html=True)

        # Display existing metadata notes
        display_notes(existing_notes, "metadata")

    # Q&A sections
    questions = qa_data.get("questions", {})
    
    # Medium questions
    medium_questions = questions.get("medium", [])
    if medium_questions:
        st.subheader("📖 Medium Questions")
        for i, qa_pair in enumerate(medium_questions):
            display_qa_pair(qa_pair, f"medium_{i}", content_id, intern_id, notes_col, existing_notes, "L1")

    # Long questions  
    long_questions = questions.get("long", [])
    if long_questions:
        st.subheader("📚 Long Questions")
        for i, qa_pair in enumerate(long_questions):
            display_qa_pair(qa_pair, f"long_{i}", content_id, intern_id, notes_col, existing_notes, "L1")

def display_qa_pair(qa_pair, key_prefix, content_id, intern_id, notes_col, existing_notes, level):
    """Display individual Q&A pair with voting buttons and notes"""
    
    question = qa_pair.get("question", "")
    answer = qa_pair.get("answer", "")
    
    st.markdown("---")
    st.markdown(f"**Question:** {question}")
    st.markdown(f"**Answer:** {answer}")
    
    col1, col2, col3 = st.columns([1, 1, 1])
    
    with col1:
        correct_btn = st.button("✅ Correct", key=f"{key_prefix}_correct")
    with col2:
        incorrect_btn = st.button("❌ Incorrect", key=f"{key_prefix}_incorrect")
    with col3:
        if st.button("📝 Add Note", key=f"{key_prefix}_note"):
            add_note_dialog(content_id, intern_id, notes_col, key_prefix, level)

    # Store judgments in session state
    if correct_btn:
        st.session_state[f"{key_prefix}_judgment"] = "Correct"
        st.success("Marked as Correct")
    elif incorrect_btn:
        st.session_state[f"{key_prefix}_judgment"] = "Incorrect"
        st.error("Marked as Incorrect")

    # Display existing notes for this Q&A
    display_notes(existing_notes, key_prefix)

def add_note_dialog(content_id, intern_id, notes_col, item_type, level):
    """Add note dialog"""
    note_key = f"note_input_{item_type}"
    
    with st.form(f"note_form_{item_type}"):
        note_text = st.text_area("Add your note:", key=note_key)
        submit_note = st.form_submit_button("Save Note")
        
        if submit_note and note_text.strip():
            notes_col.insert_one({
                "content_id": content_id,
                "intern_id": intern_id,
                "item_type": item_type,
                "note_text": note_text.strip(),
                "level": level,
                "timestamp": datetime.now(timezone.utc)
            })
            st.success("Note saved!")
            st.rerun()

def display_notes(notes, item_type):
    """Display existing notes for an item"""
    item_notes = [n for n in notes if n.get("item_type") == item_type]
    
    if item_notes:
        st.markdown("<div class='notes-section'>", unsafe_allow_html=True)
        st.markdown("**📝 Notes:**")
        for note in item_notes:
            level = note.get("level", "L1")
            timestamp = note.get("timestamp", datetime.now(timezone.utc))
            intern = note.get("intern_id", "Unknown")
            st.markdown(f"- **[{level}]** {note['note_text']} *(by {intern} on {timestamp.strftime('%Y-%m-%d %H:%M')})*")
        st.markdown("</div>", unsafe_allow_html=True)

def handle_ml_submission(content_id, qa_data, intern_id, medium_long_audit_col, edit_queue_col):
    """Handle submission of medium/long audits"""
    
    if st.button("✅ Submit All Judgments"):
        # Collect all judgments from session state
        judgments = {}
        
        # Check metadata judgment
        if st.session_state.get("metadata_correct"):
            judgments["metadata"] = "Correct"
        elif st.session_state.get("metadata_incorrect"):
            judgments["metadata"] = "Incorrect"

        # Check medium questions
        questions = qa_data.get("questions", {})
        medium_questions = questions.get("medium", [])
        for i in range(len(medium_questions)):
            key = f"medium_{i}_judgment"
            if key in st.session_state:
                judgments[f"medium_{i}"] = st.session_state[key]

        # Check long questions
        long_questions = questions.get("long", [])
        for i in range(len(long_questions)):
            key = f"long_{i}_judgment"
            if key in st.session_state:
                judgments[f"long_{i}"] = st.session_state[key]

        if not judgments:
            st.warning("⚠️ Please make at least one judgment before submitting.")
            return

        # Save to database
        audit_doc = {
            "content_id": content_id,
            "intern_id": intern_id,
            "judgments": judgments,
            "timestamp": datetime.now(timezone.utc),
            "assigned_at": st.session_state.ml_assigned_time
        }
        
        medium_long_audit_col.insert_one(audit_doc)
        
        # Check if this content should go to edit queue
        check_and_move_to_edit_queue(content_id, medium_long_audit_col, edit_queue_col)
        
        st.session_state.ml_submitted = True
        st.success("✅ Judgments submitted successfully!")
        
        # Clear judgment session state
        for key in list(st.session_state.keys()):
            if "_judgment" in key or key.startswith("metadata_"):
                del st.session_state[key]

def check_and_move_to_edit_queue(content_id, medium_long_audit_col, edit_queue_col):
    """Check if content should be moved to edit queue based on judgments"""
    
    # Get all audits for this content
    audits = list(medium_long_audit_col.find({"content_id": content_id}))
    
    if len(audits) >= MAX_AUDITORS:
        # Analyze judgments to determine what goes to edit queue
        judgment_counts = {}
        
        for audit in audits:
            for item, judgment in audit.get("judgments", {}).items():
                if item not in judgment_counts:
                    judgment_counts[item] = {"Correct": 0, "Incorrect": 0}
                judgment_counts[item][judgment] += 1
        
        # Items that need editing (majority incorrect)
        items_to_edit = []
        for item, counts in judgment_counts.items():
            if counts["Incorrect"] > counts["Correct"]:
                items_to_edit.append(item)
        
        if items_to_edit:
            # Add to edit queue
            edit_queue_col.update_one(
                {"content_id": content_id},
                {
                    "$set": {
                        "content_id": content_id,
                        "items_to_edit": items_to_edit,
                        "timestamp": datetime.now(timezone.utc),
                        "status": "pending"
                    }
                },
                upsert=True
            )

def reset_ml_session_state():
    """Reset session state for medium/long queue"""
    st.session_state.ml_content_id = None
    st.session_state.ml_submitted = False
    st.session_state.ml_deadline = None
    
    # Clear judgment states
    for key in list(st.session_state.keys()):
        if "_judgment" in key or key.startswith("metadata_"):
            del st.session_state[key]
