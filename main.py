import os
import pickle
import traceback

from flask import Flask, request, jsonify, send_from_directory
from googleapiclient.discovery import build
import openai
import faiss

app = Flask(__name__, static_folder="static", static_url_path="")

# 1) UI at "/"
@app.route("/", methods=["GET"])
def ui():
    return send_from_directory("static", "index.html")

# 2) Health at "/health"
@app.route("/health", methods=["GET"])
def health():
    return "OK", 200

# 3) Load FAISS index
with open("faiss_index/index.pkl", "rb") as f:
    faiss_index = pickle.load(f)

# 4) OpenAI key
openai.api_key = os.getenv("OPENAI_API_KEY")

# 5) RFP generator
@app.route("/start", methods=["POST"])
def start():
    try:
        data     = request.get_json()
        sheet_id = data["sheet_id"]
        doc_id   = data["doc_id"]

        sheets_svc = build("sheets", "v4").spreadsheets()
        meta = sheets_svc.get(
            spreadsheetId=sheet_id,
            fields="sheets(properties(title))"
        ).execute()
        first_tab = meta["sheets"][0]["properties"]["title"]
        sheet_range = f"{first_tab}!A2:B"

        resp = sheets_svc.values().get(
            spreadsheetId=sheet_id,
            range=sheet_range
        ).execute()
        rows = resp.get("values", [])
        if not rows:
            return jsonify(error="No data in sheet!"), 400

        docs_svc = build("docs", "v1").documents()
        for idx, row in enumerate(rows, start=2):
            req = row[0]
            fnc = row[1] if len(row)>1 else ""
            messages = [
                {"role":"system","content":"You are Fever’s RFP AI assistant."},
                {"role":"user","content":
                    f"Requirement: {req}\n"
                    f"Our functionality: {fnc}\n\n"
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
                body={"requests":[
                    {"insertText":{
                        "endOfSegmentLocation":{},
                        "text": enriched + "\n\n"
                    }}
                ]}
            ).execute()

        return jsonify(status="complete", rows=len(rows)), 200

    except Exception as e:
        tb = traceback.format_exc()
        return jsonify(error=str(e), traceback=tb), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",8080)))
