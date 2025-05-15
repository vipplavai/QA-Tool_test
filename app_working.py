import streamlit as st
from pymongo import MongoClient
from datetime import datetime, timezone, timedelta
import time
import re
from auth0_component import login_button
from streamlit import components
import streamlit.components.v1 as components
import random
from pymongo import ReturnDocument

# === APP STATE TRACKING ===
if "profile_step" not in st.session_state:
    st.session_state["profile_step"] = 1
if "prev_auth0_id" not in st.session_state:
    st.session_state["prev_auth0_id"] = None

# === CONFIG & STYLING ===
try:
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
except Exception as e:
    import traceback
    log_system_event(
        "unexpected_error",
        str(e),
        {"traceback": traceback.format_exc()}
    )
    st.error("🔴 An unexpected error occurred. Please reload or contact support.")
    raise

def log_system_event(event, message, details=None):
    try:
        temp_client = MongoClient(
            st.secrets.get("mongo_uri", ""),
            serverSelectionTimeoutMS=2000
        )
        temp_db = temp_client["Tel_QA"]
        temp_db["system_logs"].insert_one({
            "timestamp": datetime.now(timezone.utc),
            "event":     event,
            "message":   message,
            "details":   details or {}
        })
    except Exception:
        pass  # best‐effort only

# now your styling/config block can call log_system_event safely
timer_ph = st.empty()



# Helper: Show Login Screen Title & Description
def show_login_intro():
    st.title("🔐 Welcome to JNANA QA Auditing Tool")
    st.markdown("Please log in to audit short Q&A pairs.")

 # === SYSTEM / INFRASTRUCTURE LOGGING (standalone) ===
def log_system_event(event, message, details=None):
    """
    Logs directly to MongoDB using a fresh client so that
    it can be called before `db` is set up.
    """
    try:
        temp_client = MongoClient(
            st.secrets.get("mongo_uri", ""),
            serverSelectionTimeoutMS=2000
        )
        temp_db = temp_client["Tel_QA"]
        temp_db["system_logs"].insert_one({
            "timestamp": datetime.now(timezone.utc),
            "event":     event,
            "message":   message,
            "details":   details or {}
        })
    except Exception:
        # best‐effort only, swallow errors
        pass

TIMER_SECONDS = 60 * 7
MAX_AUDITORS  = 5

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
        # now log_system_event is already defined
        log_system_event("db_connect_error", str(e))
        st.error("🔴 Cannot connect to database. Please try again later.")
        st.stop()

client      = get_client()
db          = client["Tel_QA"]
users_col   = db["users"]
content_col = db["Content"]
qa_col      = db["QA_pairs"]
audit_col   = db["audit_logs"]
doubt_col   = db["doubt_logs"]
skip_col    = db["skipped_logs"]


# track “reserved” slots so we can block concurrent assignments
assign_col = db["assignment_placeholders"]


# 3) create your TTL index once
if "ttl_index_created" not in st.session_state:
    assign_col.create_index(
        "assigned_at",
        expireAfterSeconds=TIMER_SECONDS
    )
    st.session_state["ttl_index_created"] = True


# === USER ACTION LOGGING ===
user_logs = db["user_logs"]

 # === SYSTEM / INFRASTRUCTURE LOGGING ===
system_logs = db["system_logs"]

def log_user_action(intern_id, action, details=None):
    """
    Persist a timestamped action for audit.
    action: str, e.g. "assigned", "skipped", "timeout", "submitted", "next_clicked", "error"
    details: dict of any extra context (e.g. content_id, time_taken, error message)
    """
    user_logs.insert_one({
        "intern_id": intern_id,
        "date":      datetime.now(timezone.utc),
        "action":    action,
        "details":   details or {}
    })




# === AUTH0 LOGIN ===

# only call login_button() until we have user_info
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

    # store and immediately rerun to clear the login UI
    st.session_state.user_info = auth0_user_info
        # log successful login
    log_user_action(auth0_user_info["sub"], "login_success", {
        "email": auth0_user_info.get("email")
    })
    st.rerun()

# from here on down, we know user_info is set and login_button() won't be called again
user_info = st.session_state.user_info

# === EXTRACT USER INFO ===

auth0_id   = user_info.get("sub")
given_name = user_info.get("given_name", "")
email      = user_info.get("email", "")
picture    = user_info.get("picture", "")

# === INTERN‑ID GENERATOR & UNIQUE 5 OPTIONS ===

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

# === FIRST‑TIME SIGNUP FLOW ===

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
                st.session_state.last_name  = ln
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

# === POST‑SIGNUP UI ===

intern_id = existing_user["intern_id"] if existing_user else selected
first = existing_user["first_name"] if existing_user else st.session_state.first_name
last = existing_user["last_name"] if existing_user else st.session_state.last_name

st.title("🔍 JNANA – Short Q&A Auditing Tool")
st.markdown(f"Hello, **{first} {last}**! Your Intern ID: **{intern_id}**.")

# warn on page unload (logout/refresh) so they don’t lose progress
st.components.v1.html("""
<script>
  window.addEventListener("beforeunload", function (e) {
    e.preventDefault();
    e.returnValue = "";
    return "";
  });
</script>
""", height=0)

# === MANUAL LOGOUT BUTTON WITH CONFIRMATION ===
if "logout_requested" not in st.session_state:
    st.session_state.logout_requested = False

if st.session_state.logout_requested:
    st.warning("🚨 Are you sure you want to log out? Your current audit will be lost.")
    col_yes, col_no = st.columns(2)
    if col_yes.button("Yes, log me out"):
        # Clear only our custom session keys
        for k in list(st.session_state.keys()):
            if k not in ["global_config", "secrets"]:
                del st.session_state[k]
        domain    = st.secrets["AUTH0_DOMAIN"]
        client_id = st.secrets["AUTH0_CLIENT_ID"]
        st.components.v1.html(f"""
            <script>
              const domain = "{domain}";
              const clientId = "{client_id}";
              const returnTo = window.location.origin;
              alert("🎉 You have been logged out successfully. The app will now reload.");
              window.top.location.href = `https://${{domain}}/v2/logout?client_id=${{clientId}}&returnTo=${{returnTo}}`;
            </script>
        """, height=0)
        st.rerun()
    if col_no.button("Cancel"):
        st.session_state.logout_requested = False
else:
    if st.button("🔒 Logout"):
        st.session_state.logout_requested = True


# === Session State Init ===
for key in ["eligible_id", "deadline", "assigned_time", "judged",
            "auto_skip_triggered", "current_content_id",
            "eligible_content_ids", "timer_expired"]:
    if key not in st.session_state:
        st.session_state[key] = None if key in ["eligible_id", "current_content_id"] else False

# 1a) Track whether this content has been submitted
if "submitted" not in st.session_state:
    st.session_state["submitted"] = False


# 3a) Track whether we’re actively writing to the DB
if "is_submitting" not in st.session_state:
    st.session_state["is_submitting"] = False




# === Timer Expired Screen ===
if st.session_state.timer_expired:
    st.title("⏰ Time Expired")
    st.warning("This content ID has been skipped due to timeout.")
    if st.button("🔄 Fetch New Content"):
        # clear flags before getting a new cid
        st.session_state.timer_expired      = False
        st.session_state.eligible_id        = None
        st.session_state.current_content_id = None
        st.session_state.submitted          = False
        st.session_state.is_submitting      = False
        st.rerun()

# === ATOMIC ASSIGNMENT via placeholder collection ===
def assign_new_content():
    # CLEAN UP any placeholders older than our timeout
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=TIMER_SECONDS)
    assign_col.delete_many({"assigned_at": {"$lt": cutoff}})

    # 1) fetch all content IDs
    all_ids = qa_col.distinct("content_id")

    # 2) compute how many distinct interns have audited each content_id
    distinct_counts = {
        doc["_id"]: len(doc["interns"])
        for doc in audit_col.aggregate([
            {"$group": {
                "_id": "$content_id",
                "interns": {"$addToSet": "$intern_id"}
            }}
        ])
    }

    # 3) which IDs has this intern already done?
    seen = set(audit_col.distinct("content_id", {"intern_id": intern_id}))
    skipped = set(skip_col.distinct("content_id", {"intern_id": intern_id}))
    seen |= skipped    # and which IDs are already reserved for them
    reserved = set(assign_col.distinct("content_id", {"intern_id": intern_id}))

    # 4) build list of all under-5 items they haven’t seen/reserved
    candidates = [
        cid for cid in all_ids
        if distinct_counts.get(cid, 0) < MAX_AUDITORS
        and cid not in seen
        and cid not in reserved
    ]

    # 5) if empty, we’re done
    if not candidates:
        st.session_state.eligible_id = None
        return

    # 6) randomly pick one and reserve it
    cid = random.choice(candidates)
    assign_col.insert_one({
        "content_id":  cid,
        "intern_id":   intern_id,
        "assigned_at": datetime.now(timezone.utc)
    })
    log_user_action(intern_id, "assigned_content", {"content_id": cid})

    # 7) set state
    st.session_state.eligible_id   = cid
    st.session_state.deadline      = time.time() + TIMER_SECONDS
    st.session_state.assigned_time = datetime.now(timezone.utc)


# kick things off
if st.session_state.eligible_id is None:
    assign_new_content()
if st.session_state.eligible_id is None:
    st.success("✅ All content audited!")
    st.stop()


cid = st.session_state.eligible_id
remaining = int(st.session_state.deadline - time.time())

# === Fetch Content & QA ===
# @st.cache_data(ttl=300)
def fetch_content_qa(cid):
    try:
        start = time.time()
        content = content_col.find_one({"content_id": cid})
        qa_doc  = qa_col.find_one({"content_id": cid})
        duration = time.time() - start
        if duration > 1.0:
            log_system_event("slow_db_query",
                             f"fetch_content_qa took {duration:.2f}s",
                             {"content_id": cid})
        return content, qa_doc
    except Exception as e:
        log_system_event("db_query_error",
                         f"Error fetching content {cid}: {e}",
                         {"content_id": cid})
        raise

content, qa_doc = fetch_content_qa(cid)
qa_pairs = qa_doc.get("questions", {}).get("short", []) if qa_doc else []

# === Reset radios & flags if content changes ===
if st.session_state.current_content_id != cid:
    for i in range(len(qa_pairs)):
        st.session_state[f"j_{i}"] = None
    st.session_state.current_content_id = cid

    # ← reset our submission guards for the new content
    st.session_state.submitted      = False
    st.session_state.is_submitting  = False

    
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
    assign_col.delete_many({
        "content_id": cid,
        "intern_id":  intern_id
    })
    # log the skip
    log_user_action(intern_id, "skipped_invalid", {"content_id": cid})

    st.warning(f"⚠️ Skipping ID {cid} — content or valid short QA missing.")
    st.session_state.current_content_id = None
    assign_new_content()
    st.rerun()


# === Timeout Logic ===
if remaining <= 0 and not st.session_state.submitted:
    skip_col.insert_one({
        "intern_id": intern_id,
        "content_id": cid,
        "status": "timeout",
        "assigned_at": st.session_state.assigned_time,
        "timestamp": datetime.now(timezone.utc)
    })
    assign_col.delete_many({
        "content_id": cid,
        "intern_id":  intern_id
    })
    # log the timeout
    log_user_action(intern_id, "skipped_timeout", {"content_id": cid})
    st.session_state.timer_expired = True
    st.rerun()


# === Timer Display ===
remaining = int(st.session_state.deadline - time.time())

if not st.session_state.submitted:
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
        selected = st.radio(
            label="", 
            options=["Correct","Incorrect","Doubt"],
            key=f"j_{i}",
            label_visibility="collapsed",
            disabled=(
                st.session_state.submitted 
                or st.session_state.is_submitting
            )
        )
        
        judgments.append({
            "qa_index": i,
            "question": pair["question"],
            "answer": pair["answer"],
            "judgment": selected
        })
        st.markdown("---")

# === Buttons ===
all_answered = all(st.session_state.get(f"j_{i}") is not None for i in range(len(qa_pairs)))
submit = st.button(
    "✅ Submit",
    disabled=(
        st.session_state.submitted
        or st.session_state.is_submitting
        or not all_answered
    )
)
next_ = st.button("➡️ Next")

if submit and not st.session_state.submitted:
    st.session_state.is_submitting = True
    now        = datetime.now(timezone.utc)
    time_taken = (now - st.session_state.assigned_time).total_seconds()

    success = True
    with st.spinner("Saving your judgments…"):  # show a spinner while writing :contentReference[oaicite:1]{index=1}
        # remove placeholder reservation
        assign_col.delete_many({
            "content_id": cid,
            "intern_id":  intern_id
        })

        # log the submission
        log_user_action(intern_id, "submitted", {
            "content_id": cid,
            "time_taken": time_taken
        })

        # write each judgment
        for entry in judgments:
            entry.update({
                "content_id":   cid,
                "intern_id":    intern_id,
                "timestamp":    now,
                "assigned_at":  st.session_state.assigned_time,
                "time_taken":   time_taken,
                "length":       "short"
            })
            try:
                if entry["judgment"] == "Doubt":
                    res = doubt_col.insert_one(entry)
                else:
                    res = audit_col.insert_one(entry)
                if not res.acknowledged:
                    raise RuntimeError("Write not acknowledged")
            except Exception as e:
                log_system_event("db_write_error", str(e))
                st.error("🔴 Failed to save your judgments. Please try again.")
                success = False
                break

    if success:
        # disable the UI, clear timer, and confirm
        st.session_state.submitted    = True
        st.session_state.is_submitting = False
        timer_ph.empty()  # immediately stop showing the countdown
        st.success(f"✅ Judgments saved in {time_taken:.1f}s")
    else:
        # allow retry
        st.session_state.is_submitting = False                     




if next_:
    if not st.session_state.submitted:
        skip_col.insert_one({
            "intern_id": intern_id,
            "content_id": cid,
            "status": "manual_skip",
            "timestamp": datetime.now(timezone.utc)
        })
        log_user_action(intern_id, "next_clicked", {"previous_content_id": cid})
    else:
        # optional: log that they clicked Next _after_ submitting
        log_user_action(intern_id, "next_after_submit", {"content_id": cid})

    # reset everything and fetch new content
    for key in list(st.session_state.keys()):
        if key.startswith("j_"):
            del st.session_state[key]
    st.session_state.current_content_id = None
    st.session_state.submitted         = False
    st.session_state.is_submitting     = False
    assign_new_content()
    st.rerun()

