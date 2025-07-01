
import streamlit as st
from datetime import datetime, timezone
from pymongo import UpdateOne

def handle_edit_queue(intern_id, db):
    """Handle edit queue for reviewing and correcting submissions"""
    
    edit_queue_col = db["edit_queue"]
    notes_col = db["notes"]
    content_col = db["Content"]
    completed_content_col = db["completed_content"]
    qa_col = db["QA_pairs"]
    final_qa_col = db["Final_QA_pairs"]
    medium_long_audit_col = db["medium_long_audits"]

    st.subheader("✏️ Edit Queue")
    st.markdown("Review and edit incorrect submissions from the Medium & Long queue.")

    # Get pending edit items
    edit_items = list(edit_queue_col.find({"status": "pending"}).sort("timestamp", 1))
    
    if not edit_items:
        st.success("✅ No items in edit queue!")
        return

    # Select item to edit
    st.subheader("📋 Items to Edit")
    
    item_options = [f"Content ID {item['content_id']} ({len(item.get('items_to_edit', []))} items)" for item in edit_items]
    selected_idx = st.selectbox("Select content to edit:", range(len(item_options)), format_func=lambda x: item_options[x])
    
    if selected_idx is not None:
        selected_item = edit_items[selected_idx]
        content_id = selected_item["content_id"]
        items_to_edit = selected_item.get("items_to_edit", [])
        
        # Fetch content data
        content_data, qa_data = fetch_edit_content_qa(content_id, content_col, completed_content_col, qa_col, final_qa_col)
        
        if not content_data or not qa_data:
            st.error("❌ Content or QA data not found!")
            return
        
        # Display content
        st.subheader(f"📄 Content ID: {content_id}")
        content_text = content_data.get("content_text", "") or content_data.get("content", "")
        st.markdown(f"<div class='passage-box'>{content_text}</div>", unsafe_allow_html=True)
        
        # Get existing notes
        existing_notes = list(notes_col.find({"content_id": content_id}).sort("timestamp", 1))
        
        # Display items that need editing
        st.subheader("🔧 Items Requiring Edits")
        
        edited_items = {}
        
        for item in items_to_edit:
            st.markdown("---")
            
            if item == "metadata":
                display_edit_metadata(qa_data, existing_notes, content_id, intern_id, notes_col, edited_items)
            elif item.startswith("medium_"):
                idx = int(item.split("_")[1])
                medium_questions = qa_data.get("questions", {}).get("medium", [])
                if idx < len(medium_questions):
                    display_edit_qa_pair(medium_questions[idx], item, existing_notes, content_id, intern_id, notes_col, edited_items)
            elif item.startswith("long_"):
                idx = int(item.split("_")[1])
                long_questions = qa_data.get("questions", {}).get("long", [])
                if idx < len(long_questions):
                    display_edit_qa_pair(long_questions[idx], item, existing_notes, content_id, intern_id, notes_col, edited_items)
        
        # Submit edited items
        if st.button("✅ Submit Edits"):
            if not edited_items:
                st.warning("⚠️ Please review and edit at least one item.")
            else:
                save_edits(content_id, edited_items, intern_id, edit_queue_col, medium_long_audit_col)
                st.success("✅ Edits submitted! Items moved back to Medium & Long queue.")
                st.rerun()

def fetch_edit_content_qa(content_id, content_col, completed_content_col, qa_col, final_qa_col):
    """Fetch content and QA data for editing"""
    # Try both content collections
    content_data = content_col.find_one({"content_id": content_id})
    if not content_data:
        content_data = completed_content_col.find_one({"content_id": content_id})

    # Try both QA collections
    qa_data = qa_col.find_one({"content_id": content_id})
    if not qa_data:
        qa_data = final_qa_col.find_one({"content_id": content_id})

    return content_data, qa_data

def display_edit_metadata(qa_data, existing_notes, content_id, intern_id, notes_col, edited_items):
    """Display metadata for editing"""
    metadata = qa_data.get("metadata", {})
    
    st.markdown("### 🏷️ Metadata (Needs Review)")
    st.markdown("<div class='metadata-box'>", unsafe_allow_html=True)
    
    col1, col2 = st.columns([2, 1])
    
    with col1:
        st.write(f"**Current Topic:** {metadata.get('topic', 'N/A')}")
        new_topic = st.text_input("Corrected Topic:", value=metadata.get('topic', ''), key="edit_topic")
        
        st.write(f"**Current Genre:** {metadata.get('genre', 'N/A')}")
        new_genre = st.text_input("Corrected Genre:", value=metadata.get('genre', ''), key="edit_genre")
        
        st.write(f"**Current Tone:** {metadata.get('tone', 'N/A')}")
        new_tone = st.text_input("Corrected Tone:", value=metadata.get('tone', ''), key="edit_tone")
    
    with col2:
        if st.button("📝 Add L2 Note", key="edit_metadata_note"):
            add_edit_note_dialog(content_id, intern_id, notes_col, "metadata", "L2")
    
    st.markdown("</div>", unsafe_allow_html=True)
    
    # Display existing notes
    display_edit_notes(existing_notes, "metadata")
    
    # Store edits
    if new_topic != metadata.get('topic', '') or new_genre != metadata.get('genre', '') or new_tone != metadata.get('tone', ''):
        edited_items["metadata"] = {
            "topic": new_topic,
            "genre": new_genre,
            "tone": new_tone
        }

def display_edit_qa_pair(qa_pair, item_key, existing_notes, content_id, intern_id, notes_col, edited_items):
    """Display Q&A pair for editing"""
    question = qa_pair.get("question", "")
    answer = qa_pair.get("answer", "")
    
    st.markdown(f"### 📝 {item_key.replace('_', ' ').title()} (Needs Review)")
    
    col1, col2 = st.columns([3, 1])
    
    with col1:
        st.write(f"**Current Question:** {question}")
        new_question = st.text_area("Corrected Question:", value=question, key=f"edit_q_{item_key}")
        
        st.write(f"**Current Answer:** {answer}")
        new_answer = st.text_area("Corrected Answer:", value=answer, key=f"edit_a_{item_key}")
    
    with col2:
        if st.button("📝 Add L2 Note", key=f"edit_note_{item_key}"):
            add_edit_note_dialog(content_id, intern_id, notes_col, item_key, "L2")
    
    # Display existing notes
    display_edit_notes(existing_notes, item_key)
    
    # Store edits
    if new_question != question or new_answer != answer:
        edited_items[item_key] = {
            "question": new_question,
            "answer": new_answer
        }

def add_edit_note_dialog(content_id, intern_id, notes_col, item_type, level):
    """Add note dialog for edit queue"""
    note_key = f"edit_note_input_{item_type}"
    
    with st.form(f"edit_note_form_{item_type}"):
        note_text = st.text_area("Add your L2 note:", key=note_key)
        submit_note = st.form_submit_button("Save L2 Note")
        
        if submit_note and note_text.strip():
            notes_col.insert_one({
                "content_id": content_id,
                "intern_id": intern_id,
                "item_type": item_type,
                "note_text": note_text.strip(),
                "level": level,
                "timestamp": datetime.now(timezone.utc)
            })
            st.success("L2 Note saved!")
            st.rerun()

def display_edit_notes(notes, item_type):
    """Display existing notes for an item in edit queue"""
    item_notes = [n for n in notes if n.get("item_type") == item_type]
    
    if item_notes:
        st.markdown("<div class='notes-section'>", unsafe_allow_html=True)
        st.markdown("**📝 All Notes:**")
        for note in item_notes:
            level = note.get("level", "L1")
            timestamp = note.get("timestamp", datetime.now(timezone.utc))
            intern = note.get("intern_id", "Unknown")
            color = "#ffeb3b" if level == "L1" else "#ff9800"
            st.markdown(f"- **[{level}]** {note['note_text']} *(by {intern} on {timestamp.strftime('%Y-%m-%d %H:%M')})* ", unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

def save_edits(content_id, edited_items, intern_id, edit_queue_col, medium_long_audit_col):
    """Save edits and move content back to medium/long queue"""
    
    # Mark edit queue item as completed
    edit_queue_col.update_one(
        {"content_id": content_id},
        {
            "$set": {
                "status": "completed",
                "edited_by": intern_id,
                "edited_at": datetime.now(timezone.utc),
                "edits": edited_items
            }
        }
    )
    
    # Remove previous audits for this content to allow re-auditing
    medium_long_audit_col.delete_many({"content_id": content_id})
    
    # The content will now be available again in the medium/long queue
