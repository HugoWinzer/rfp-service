import os
import pickle
import traceback
import json

from flask import Flask, request, jsonify
from googleapiclient.discovery import build
import openai
import faiss

# Serve static/index.html at "/"
app = Flask(__name__, static_folder="static", static_url_path="")

@app.route("/", methods=["GET"])
def ui():
    return app.send_static_file("index.html")

@app.route("/health", methods=["GET"])
def health():
    return "OK", 200

# Load FAISS index once at startup
with open("faiss_index/index.pkl", "rb") as f:
    faiss_index = pickle.load(f)

# Configure OpenAI key
openai.api_key = os.getenv("OPENAI_API_KEY")

@app.route("/start", methods=["POST"])
def start():
    try:
        data     = request.get_json()
        sheet_id = data["sheet_id"]
        doc_id   = data["doc_id"]

        # 1) Auto-detect first sheet tab name
        sheets_svc = build("sheets", "v4").spreadsheets()
        meta = sheets_svc.get(
            spreadsheetId=sheet_id,
            fields="sheets(properties(title))"
        ).execute()
        first_tab   = meta["sheets"][0]["properties"]["title"]
        sheet_range = f"{first_tab}!A2:B"
        print("Using range:", sheet_range)

        # 2) Fetch requirements + functionality rows
        resp = sheets_svc.values().get(
            spreadsheetId=sheet_id,
            range=sheet_range
        ).execute()
        rows = resp.get("values", [])
        if not rows:
            return jsonify(error="No data in sheet!"), 400
        print(f"Fetched {len(rows)} rows")

        # 3) Enrich and append to the Doc
        docs_svc = build("docs", "v1").documents()
        for idx, row in enumerate(rows, start=2):
            requirement   = row[0]
            functionality = row[1] if len(row) > 1 else ""
            messages = [
                {"role": "system", "content": "You are Fever’s RFP AI assistant."},
                {"role": "user", "content":
                    f"Requirement: {requirement}\n"
                    f"Our functionality: {functionality}\n\n"
                    "Write a narrative-rich paragraph explaining how this functionality meets the requirement."
                }
            ]
            ai_resp = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=messages,
                max_tokens=300
            )
            enriched = ai_resp.choices[0].message.content.strip()

            docs_svc.batchUpdate(
                documentId=doc_id,
                body={"requests":[{"insertText":{
                    "endOfSegmentLocation":{}, "text": enriched + "\n\n"
                }}]}
            ).execute()
            print(f"Done row {idx}")

        return jsonify(status="complete", rows=len(rows)), 200

    except Exception as e:
        tb = traceback.format_exc()
        print(tb)
        return jsonify(error=str(e), traceback=tb), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

