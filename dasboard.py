# intern_dashboard.py - JNANA Intern Milestone Tracker (Gamified)
import streamlit as st
from pymongo import MongoClient
import pandas as pd
import numpy as np
from collections import Counter
from statsmodels.stats.inter_rater import fleiss_kappa

# === CONFIG ===
st.set_page_config(page_title="JNANA Milestone Dashboard", layout="wide")
st.title("🎯 JNANA Intern Milestone Tracker")
st.caption("Track our collective progress toward building India’s benchmark QA dataset.")

# === MongoDB Setup ===
client = MongoClient(st.secrets["mongo_uri"])
db = client["Tel_QA"]
qa_col = db["QA_pairs"]
audit_col = db["audit_logs"]
doubt_col = db["doubt_logs"]

# === Load Data ===
qa_data = list(qa_col.find())
audit_data = list(audit_col.find())
doubt_data = list(doubt_col.find())

# === Stats ===
content_ids = qa_col.distinct("content_id")
total_ids = len(content_ids)
unique_interns = list(set(a["intern_id"] for a in audit_data))
total_judgments = len(audit_data)

# === Completed IDs (5 interns judged)
completed_ids = [cid for cid in content_ids if len(set(a["intern_id"] for a in audit_col.find({"content_id": cid}))) == 5]

# === Global Stats ===
st.subheader("📦 Dataset Overview")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Total Content IDs", total_ids)
c2.metric("Completed", len(completed_ids))
c3.metric("Judgments", total_judgments)
c4.metric("Active Interns", len(unique_interns))

# === Fleiss' Kappa Calculation ===
from collections import defaultdict
pairwise = defaultdict(lambda: defaultdict(list))
for a in audit_data:
    pairwise[a["content_id"]][a["qa_index"]].append(a["judgment"])

kappa_scores = []
for cid, pairs in pairwise.items():
    for qidx, judgments in pairs.items():
        if len(judgments) == 5:
            count = Counter(judgments)
            matrix = np.array([[count.get("Correct", 0), count.get("Incorrect", 0)]])
            kappa = fleiss_kappa(matrix)
            if np.isnan(kappa) and (matrix[0][0] == 5 or matrix[0][1] == 5):
                kappa = 1.0
            if not np.isnan(kappa):
                kappa_scores.append(kappa)

valid_pairs = sum(1 for score in kappa_scores if score >= 0.4)

# === Milestone Progress ===
st.subheader("🎖️ Milestone Progress")
milestones = [5000, 10000, 20000, 50000, 100000]
current = valid_pairs
next_milestone = next((m for m in milestones if m > current), milestones[-1])
progress_ratio = current / next_milestone

st.progress(progress_ratio)
st.caption(f"{current} / {next_milestone} valid QA pairs completed")

unlocked = [m for m in milestones if current >= m]
if unlocked:
    st.success(f"🏆 Milestones Unlocked: {', '.join(map(str, unlocked))}")
else:
    st.info("🚧 No milestones unlocked yet. Let's start building!")

# === Optional: Daily Judgment Trend ===
st.subheader("📅 Daily Judgment Trend")
df = pd.DataFrame(audit_data)
if not df.empty:
    df["date"] = pd.to_datetime(df["timestamp"]).dt.date
    trend = df.groupby("date").size()
    st.line_chart(trend)
else:
    st.info("No audit data yet.")
