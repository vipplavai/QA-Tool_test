
from pymongo import MongoClient
import streamlit as st

def setup_collections():
    """Setup new MongoDB collections for enhanced auditing"""
    
    client = MongoClient(st.secrets["mongo_uri"])
    db = client["Tel_QA"]
    
    # Create new collections
    collections_to_create = [
        "medium_long_audits",
        "edit_queue", 
        "notes",
        "completed_content"
    ]
    
    for collection_name in collections_to_create:
        if collection_name not in db.list_collection_names():
            db.create_collection(collection_name)
            print(f"Created collection: {collection_name}")
    
    # Create indexes for better performance
    
    # Medium/Long audits indexes
    db["medium_long_audits"].create_index([("content_id", 1), ("intern_id", 1)])
    db["medium_long_audits"].create_index([("content_id", 1)])
    db["medium_long_audits"].create_index([("timestamp", -1)])
    
    # Edit queue indexes
    db["edit_queue"].create_index([("content_id", 1)])
    db["edit_queue"].create_index([("status", 1)])
    db["edit_queue"].create_index([("timestamp", -1)])
    
    # Notes indexes
    db["notes"].create_index([("content_id", 1), ("item_type", 1)])
    db["notes"].create_index([("intern_id", 1)])
    db["notes"].create_index([("timestamp", -1)])
    db["notes"].create_index([("level", 1)])
    
    # Completed content indexes
    db["completed_content"].create_index([("content_id", 1)])
    
    print("Database setup completed successfully!")
    return True

if __name__ == "__main__":
    setup_collections()
