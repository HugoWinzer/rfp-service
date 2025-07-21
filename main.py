import os
import pickle
import traceback
import unicodedata
from flask import Flask, request, jsonify, send_from_directory
from googleapiclient.discovery import build
from concurrent.futures import ThreadPoolExecutor
import openai
import faiss

app = Flask(__name__, static_folder="static", static_url_path="")

# Utility: Normalize Unicode characters to ASCII
def normalize_text(text):
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")

# 1) Serve the UI at "/"
@app.route("/", methods=["GET"])
def ui():
    return send_from_directory("static", "index.html")

# 2) Health check
@app.route("/health", methods=["GET"])
def health():
    return "OK", 200

# 3) Load FAISS index once at startup
with open("faiss_index/index.pkl", "rb") as f:
    faiss_index = pickle.load(f)

# 4) Configure OpenAI key from env
openai.api_key = os.getenv("OPENAI_API_KEY")

# 5) Main /start endpoint
@app.route("/start", methods=["POST"])
def start():
    try:
        data = request.get_json()
        sheet_id = data["sheet_id"]
        doc_id = data["doc_id"]

        # Get first sheet tab
        sheets_svc = build("sheets", "v4").spreadsheets()
        meta = sheets_svc.get(
            spreadsheetId=sheet_id,
            fields="sheets(properties(title))"
        ).execute()
        first_tab = meta["sheets"][0]["properties"]["title"]
        sheet_range = f"{first_tab}!A2:B"

        # Fetch rows
        resp = sheets_svc.values().get(
            spreadsheetId=sheet_id,
            range=sheet_range
        ).execute()
        rows = resp.get("values", [])
        if not rows:
            return jsonify(error="No data in sheet!"), 400

        docs_svc = build("docs", "v1").documents()

        def process_row(row, docs_svc, doc_id):
            try:
                requirement = normalize_text(row[0])
                functionality = normalize_text(row[1]) if len(row) > 1 else ""

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
                    body={"requests": [
                        {
                            "insertText": {
                                "endOfSegmentLocation": {},
                                "text": enriched + "\n\n"
                            }
                        }
                    ]}
                ).execute()
            except Exception as ex:
                print(f"[ERROR] Row failed: {row} → {ex}")

        # Threaded execution (5 concurrent workers)
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(process_row, row, docs_svc, doc_id) for row in rows]
            for f in futures:
                f.result()  # force exceptions to be raised

        return jsonify(status="complete", rows=len(rows)), 200

    except Exception as e:
        tb = traceback.format_exc()
        return jsonify(error=str(e), traceback=tb), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

