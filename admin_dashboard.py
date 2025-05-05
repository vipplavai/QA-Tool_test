# admin_dashboard.py - JNANA Admin Dashboard v3 (Enhanced)
import streamlit as st
from pymongo import MongoClient
import pandas as pd
import numpy as np
from collections import defaultdict, Counter
from statsmodels.stats.inter_rater import fleiss_kappa
import plotly.express as px
import json

# === CONFIG ===
st.set_page_config(page_title="JNANA Admin Dashboard", layout="wide")

st.markdown("""
    <style>
        .main-title { font-size: 2rem; font-weight: bold; margin-bottom: 1rem; }
        .stMetricValue { font-size: 1.4rem !important; }
    </style>
""", unsafe_allow_html=True)

st.markdown("<div class='main-title'>📊 JNANA Admin Dashboard v3</div>", unsafe_allow_html=True)

# === MongoDB Connection ===
client = MongoClient("mongodb+srv://prashanth01071995:pradsml%402025@cluster0.fsbic.mongodb.net/")
db = client["Tel_QA"]
qa_col = db["QA_pairs"]
audit_col = db["audit_logs"]
doubt_col = db["doubt_logs"]

# === Load Data ===
qa_data = list(qa_col.find())
audit_data = list(audit_col.find())
doubt_data = list(doubt_col.find())

# === Dataset Overview ===
content_ids = qa_col.distinct("content_id")
completed_ids = [cid for cid in content_ids if len(set(a["intern_id"] for a in audit_col.find({"content_id": cid}))) == 5]
unique_interns = list(set(a["intern_id"] for a in audit_data))

st.subheader("📦 Dataset Overview")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Total Content IDs", len(content_ids))
c2.metric("Completed", len(completed_ids))
c3.metric("Judgments", len(audit_data))
c4.metric("Active Interns", len(unique_interns))

st.markdown("### ⏳ Auditing Progress")
progress = len(completed_ids) / len(content_ids) if content_ids else 0
st.progress(progress)

# === Fleiss' Kappa Calculation ===
pairwise = defaultdict(lambda: defaultdict(list))
for a in audit_data:
    pairwise[a["content_id"]][a["qa_index"]].append(a["judgment"])

kappa_scores = []
majority_dict = defaultdict(lambda: defaultdict(str))

for cid, pairs in pairwise.items():
    for qidx, judgments in pairs.items():
        if len(judgments) == 5:
            if any(d["qa_index"] == qidx and d["content_id"] == cid for d in doubt_data):
                continue
            count = Counter(judgments)
            matrix = np.array([[count.get("Correct", 0), count.get("Incorrect", 0)]])
            with np.errstate(invalid="ignore"):
                kappa = fleiss_kappa(matrix)
            if np.isnan(kappa) and (matrix[0][0] == 5 or matrix[0][1] == 5):
                kappa = 1.0
            if not np.isnan(kappa):
                majority = "Correct" if matrix[0][0] > matrix[0][1] else "Incorrect"
                kappa_scores.append({"content_id": cid, "qa_index": qidx, "fleiss_kappa": round(kappa, 4), "count": 5})
                majority_dict[cid][qidx] = majority

kappa_df = pd.DataFrame(kappa_scores)
avg_kappa = round(kappa_df["fleiss_kappa"].mean(), 4) if not kappa_df.empty else None
low_agree = kappa_df[kappa_df["fleiss_kappa"] < 0.4] if not kappa_df.empty else pd.DataFrame()

# === Quality Overview ===
st.subheader("📉 Quality Overview")
q1, q2 = st.columns(2)
q1.metric("Avg. Fleiss’ Kappa", f"{avg_kappa:.4f}" if avg_kappa is not None else "—")
q2.metric("Low Agreement Pairs", len(low_agree))

# === Judgment Distribution
st.subheader("📊 Judgment Distribution")
all_confident = [a["judgment"] for a in audit_data if a["judgment"] in ["Correct", "Incorrect"]]
bias_count = Counter(all_confident)
bias_df = pd.DataFrame(bias_count.items(), columns=["Judgment", "Count"])
bias_df["Percentage"] = (bias_df["Count"] / bias_df["Count"].sum()) * 100
st.dataframe(bias_df)

# === Fleiss' Kappa Visual Insights
st.subheader("📊 Fleiss’ Kappa Distribution & Insights")

# 1. Histogram
fig_hist = px.histogram(kappa_df, x="fleiss_kappa", nbins=20, title="Fleiss’ Kappa Histogram")
st.plotly_chart(fig_hist, use_container_width=True)

# === Intern Leaderboard + Quality
st.subheader("🧑‍🎓 Intern Leaderboard + Quality")
intern_summary = []

for intern in unique_interns:
    judgments = [a for a in audit_data if a["intern_id"] == intern]
    doubts = [d for d in doubt_data if d["intern_id"] == intern]
    judged_pairs = len(judgments)
    content_count = len(set(j["content_id"] for j in judgments))
    correct_count = sum(1 for j in judgments if j["judgment"] == "Correct")
    incorrect_count = sum(1 for j in judgments if j["judgment"] == "Incorrect")

    match_count = 0
    for j in judgments:
        cid, idx, judge = j["content_id"], j["qa_index"], j["judgment"]
        if majority_dict[cid].get(idx) == judge:
            match_count += 1

    quality_pct = round((match_count / judged_pairs) * 100, 2) if judged_pairs else 0

    intern_summary.append({
        "Intern ID": intern,
        "Valid Pairs": judged_pairs,
        "Correct Given": correct_count,
        "Incorrect Given": incorrect_count,
        "Content Audited": content_count,
        "Doubts Raised": len(doubts),
        "Quality (%)": quality_pct
    })

intern_df = pd.DataFrame(intern_summary)
st.dataframe(intern_df.sort_values("Valid Pairs", ascending=False), use_container_width=True)

# === Final Dataset Export
# === Final Dataset Export
st.subheader("✅ Final Dataset Export")

# Join kappa_df with original QA pairs
final_df = kappa_df[kappa_df["fleiss_kappa"] >= 0.4] if not kappa_df.empty else pd.DataFrame()

qa_lookup = {doc["content_id"]: doc["questions"]["short"] for doc in qa_data}
final_entries = []

for row in final_df.to_dict(orient="records"):
    cid = row["content_id"]
    qidx = row["qa_index"]
    if cid in qa_lookup and qidx < len(qa_lookup[cid]):
        pair = qa_lookup[cid][qidx]
        final_entries.append({
            "content_id": cid,
            "qa_index": qidx,
            "question": pair["question"],
            "answer": pair["answer"],
            "fleiss_kappa": row["fleiss_kappa"]
        })

st.caption(f"Total Valid QA Pairs: {len(final_entries)}")

if final_entries:
    st.download_button(
        label="⬇️ Download Final Dataset as JSON",
        data=json.dumps(final_entries, indent=2, ensure_ascii=False),
        file_name="final_dataset.json",
        mime="application/json"
    )

    st.dataframe(pd.DataFrame(final_entries), use_container_width=True)


# === Doubt Panel
st.subheader("⚠️ Doubt Panel")
if doubt_data:
    st.caption("🔢 Doubt Count Per Intern")
    intern_doubts = Counter([d["intern_id"] for d in doubt_data])
    st.dataframe(pd.DataFrame(intern_doubts.items(), columns=["Intern ID", "Doubts Raised"]))
    st.caption("🔍 Doubtful QA Pairs")
    doubt_df = pd.DataFrame(doubt_data)
    st.dataframe(doubt_df[["content_id", "qa_index", "question", "answer", "intern_id", "timestamp"]], use_container_width=True)
else:
    st.info("No doubts raised yet.")

