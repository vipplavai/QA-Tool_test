
import streamlit as st
import streamlit.components.v1 as components
from pymongo import MongoClient, InsertOne, ReturnDocument
from pymongo.errors import DuplicateKeyError, BulkWriteError
from datetime import datetime, timezone
import time, re, random
from auth0_component import login_button

TIMER_SECONDS = 60 * 7
MAX_AUDITORS = 3

def log_system_event(event, message, details=None):
    try:
        temp_client = MongoClient(
            st.secrets.get("mongo_uri", ""),
            serverSelectionTimeoutMS=2000
        )
        temp_db = temp_client["Tel_QA"]
        temp_db["system_logs"].insert_one({
            "timestamp": datetime.now(timezone.utc),
            "event": event,
            "message": message,
            "details": details or {}
        })
    except Exception:
        pass

# === CONFIG & STYLING ===
try:
    st.set_page_config(page_title="JNANA Enhanced Auditing", layout="wide", initial_sidebar_state="collapsed")
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
        .metadata-box {
            background-color: #f0f8ff;
            border: 1px solid #bbb;
            border-radius: 8px;
            padding: 1rem;
            margin: 0.5rem 0;
        }
        .notes-section {
            background-color: #fff9c4;
            border: 1px solid #ddd;
            border-radius: 5px;
            padding: 0.5rem;
            margin: 0.2rem 0;
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
        .queue-selector {
            background-color: #e8f5e8;
            padding: 1rem;
            border-radius: 10px;
            margin: 1rem 0;
        }
        div.stButton > button {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%) !important;
            color: white !important;
            border: none !important;
            padding: 1.5rem 1rem !important;
            border-radius: 15px !important;
            font-weight: bold !important;
            font-size: 1.1rem !important;
            margin: 0.5rem 0 !important;
            box-shadow: 0 4px 15px rgba(0,0,0,0.2) !important;
            transition: all 0.3s ease !important;
            height: 120px !important;
            display: flex !important;
            align-items: center !important;
            justify-content: center !important;
        }
        div.stButton > button:hover {
            transform: translateY(-2px) !important;
            box-shadow: 0 6px 20px rgba(0,0,0,0.3) !important;
            background: linear-gradient(135deg, #764ba2 0%, #667eea 100%) !important;
        }
        /* Specific button styles */
        div[data-testid="column"]:nth-child(1) button {
            background: linear-gradient(135deg, #ff6b6b 0%, #ee5a24 100%) !important;
        }
        div[data-testid="column"]:nth-child(1) button:hover {
            background: linear-gradient(135deg, #ee5a24 0%, #ff6b6b 100%) !important;
        }
        div[data-testid="column"]:nth-child(2) button {
            background: linear-gradient(135deg, #4ecdc4 0%, #44a08d 100%) !important;
        }
        div[data-testid="column"]:nth-child(2) button:hover {
            background: linear-gradient(135deg, #44a08d 0%, #4ecdc4 100%) !important;
        }
        div[data-testid="column"]:nth-child(3) button {
            background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%) !important;
        }
        div[data-testid="column"]:nth-child(3) button:hover {
            background: linear-gradient(135deg, #f5576c 0%, #f093fb 100%) !important;
        }
        </style>
    """, unsafe_allow_html=True)
except Exception as e:
    import traceback
    log_system_event("unexpected_error", str(e), {"traceback": traceback.format_exc()})
    st.error("🔴 An unexpected error occurred. Please reload or contact support.")
    raise

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
        log_system_event("db_connect_error", str(e))
        st.error("🔴 Cannot connect to database. Please try again later.")
        st.stop()

client = get_client()
db = client["Tel_QA"]
users_col = db["users"]
content_col = db["Content"]
completed_content_col = db["completed_content"]
qa_col = db["QA_pairs"]
final_qa_col = db["Final_QA_pairs"]
audit_col = db["audit_logs"]
doubt_col = db["doubt_logs"]
skip_col = db["skipped_logs"]

# New collections for medium/long auditing
medium_long_audit_col = db["medium_long_audits"]
edit_queue_col = db["edit_queue"]
notes_col = db["notes"]

def show_login_intro():
    st.title("🔐 Welcome to JNANA Enhanced QA Auditing Tool")
    st.markdown("Please log in to access the auditing queues.")

def main():
    # === APP STATE TRACKING ===
    if "profile_step" not in st.session_state:
        st.session_state["profile_step"] = 1
    if "prev_auth0_id" not in st.session_state:
        st.session_state["prev_auth0_id"] = None

    # === AUTH0 LOGIN ===
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

        st.session_state.user_info = auth0_user_info
        st.rerun()

    user_info = st.session_state.user_info
    auth0_id = user_info.get("sub")
    given_name = user_info.get("given_name", "")
    email = user_info.get("email", "")
    picture = user_info.get("picture", "")

    # === INTERN-ID GENERATOR & UNIQUE OPTIONS ===
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

    # === FIRST-TIME SIGNUP FLOW ===
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
                    st.session_state.last_name = ln
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
                    "auth0_id": auth0_id,
                    "first_name": st.session_state.first_name,
                    "last_name": st.session_state.last_name,
                    "phone": st.session_state.phone_number,
                    "email": email,
                    "picture": picture,
                    "intern_id": selected,
                    "created_at": datetime.now(timezone.utc)
                })
                st.success("✅ Profile saved! Reloading…")
                st.session_state.profile_step = 1
                st.rerun()
            st.stop()

    # === POST-SIGNUP UI ===
    intern_id = existing_user["intern_id"] if existing_user else selected
    first = existing_user["first_name"] if existing_user else st.session_state.first_name
    last = existing_user["last_name"] if existing_user else st.session_state.last_name

    st.title("🔍 JNANA – Enhanced QA Auditing Tool")
    st.markdown(f"Hello, **{first} {last}**! Your Intern ID: **{intern_id}**.")

    # Initialize session state for page navigation
    if "current_page" not in st.session_state:
        st.session_state.current_page = "queue_selection"

    # Check if we should show a specific queue page
    if st.session_state.current_page != "queue_selection":
        # Show back button
        if st.button("⬅️ Back to Queue Selection"):
            st.session_state.current_page = "queue_selection"
            st.rerun()
        
        # Route to the appropriate queue handler
        if st.session_state.current_page == "short_queue":
            from short_queue import handle_short_queue
            handle_short_queue(intern_id, db)
            return
        elif st.session_state.current_page == "medium_long_queue":
            from medium_long_queue import handle_medium_long_queue
            handle_medium_long_queue(intern_id, db)
            return
        elif st.session_state.current_page == "edit_queue":
            from edit_queue import handle_edit_queue
            handle_edit_queue(intern_id, db)
            return

    # === QUEUE SELECTION PAGE ===
    st.markdown("<div class='queue-selector'>", unsafe_allow_html=True)
    st.subheader("📋 Select Auditing Queue")
    
    # Create three columns for the buttons
    col1, col2, col3 = st.columns(3)
    
    with col1:
        if st.button("📝 Short Queue", key="short_btn", help="Audit short question-answer pairs for correctness", use_container_width=True):
            st.session_state.current_page = "short_queue"
            st.rerun()
    
    with col2:
        if st.button("📚 Medium & Long Queue", key="medium_long_btn", help="Audit medium/long Q&A pairs with metadata", use_container_width=True):
            st.session_state.current_page = "medium_long_queue"
            st.rerun()
    
    with col3:
        if st.button("✏️ Edit Queue", key="edit_btn", help="Review and edit incorrect submissions", use_container_width=True):
            st.session_state.current_page = "edit_queue"
            st.rerun()
    
    st.markdown("</div>", unsafe_allow_html=True)

    # Show queue descriptions
    st.markdown("---")
    st.markdown("### Queue Descriptions:")
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        st.markdown("**📝 Short Queue**")
        st.markdown("Audit short question-answer pairs for correctness. Quick review of basic Q&A content.")
    
    with col2:
        st.markdown("**📚 Medium & Long Queue**")
        st.markdown("Audit medium and long Q&A pairs along with their metadata. More detailed content review.")
    
    with col3:
        st.markdown("**✏️ Edit Queue**")
        st.markdown("Review and edit incorrect submissions. Fix content that needs corrections.")

    # === LOGOUT BUTTON ===
    st.markdown("---")
    if st.button("🔒 Logout"):
        for k in list(st.session_state.keys()):
            if k not in ["global_config", "secrets"]:
                del st.session_state[k]
        st.success("🎉 You have been logged out.")
        st.rerun()

if __name__ == "__main__":
    main()
