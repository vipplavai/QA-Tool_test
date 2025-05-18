import streamlit as st
import json
from pymongo import MongoClient
from datetime import datetime

# --- MongoDB setup using Streamlit Secrets ---
MONGO_URI = st.secrets["mongo_uri"]
client = MongoClient(MONGO_URI)
db = client["Tel_QA"]
collection = db["QA_pairs"]

# --- Streamlit App UI ---
st.set_page_config(page_title="Q&A Uploader", layout="centered")
st.title("💬 Q&A Uploader")

input_json = st.text_area("Paste the full Q&A JSON", height=400, help="Ensure it includes 'content_id', 'metadata', and 'questions'.")
overwrite = st.checkbox("🔁 Overwrite if Content ID already exists", value=False)

if st.button("Upload Q&A"):
    try:
        data = json.loads(input_json)

        # Validate required keys
        required_keys = {"content_id", "metadata", "questions"}
        if not required_keys.issubset(data.keys()):
            st.error("❌ JSON must include: 'content_id', 'metadata', and 'questions'.")
        else:
            # Normalize content_id
            content_id = data["content_id"]
            if isinstance(content_id, str) and content_id.isdigit():
                content_id = int(content_id)
            data["content_id"] = content_id
            data["uploaded_at"] = datetime.utcnow()

            if overwrite:
                result = collection.update_one(
                    {"content_id": content_id},
                    {"$set": data},
                    upsert=True
                )
                if result.matched_count:
                    st.success(f"♻️ Overwritten existing Q&A for Content ID {content_id}")
                else:
                    st.success(f"✅ New Q&A inserted for Content ID {content_id}")
            else:
                if collection.find_one({"content_id": content_id}):
                    st.warning(f"⚠️ Q&A for Content ID {content_id} already exists.")
                else:
                    collection.insert_one(data)
                    st.success(f"✅ Q&A for Content ID {content_id} uploaded successfully!")
    except json.JSONDecodeError:
        st.error("❌ Invalid JSON! Please check your formatting.")
    except Exception as e:
        st.error(f"❌ Error: {e}")
