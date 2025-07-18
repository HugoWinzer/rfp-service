import os, pickle, traceback, json
from flask import Flask, request, jsonify
from googleapiclient.discovery import build
import openai, faiss

app = Flask(__name__)

@app.route("/", methods=["GET"])
def health():
    return "OK", 200

# Load FAISS index
with open("faiss_index/index.pkl","rb") as f:
    faiss_index = pickle.load(f)

# Read the OpenAI key
openai.api_key = os.getenv("OPENAI_API_KEY")

@app.route("/start", methods=["POST"])
def start():
    try:
        data     = request.get_json()
        sheet_id = data["sheet_id"]
        doc_id   = data["doc_id"]

        # Auto-detect first sheet tab
        sheets_svc = build("sheets","v4").spreadsheets()
        meta = sheets_svc.get(
            spreadsheetId=sheet_id,
            fields="sheets(properties(title))"
        ).execute()
        first_tab   = meta["sheets"][0]["properties"]["title"]
        sheet_range = f"{first_tab}!A2:B"
        print("Using range:", sheet_range)

        # Fetch rows
        resp = sheets_svc.values().get(
            spreadsheetId=sheet_id, range=sheet_range
        ).execute()
        rows = resp.get("values", [])
        if not rows:
            return jsonify(error="No data in sheet!"), 400
        print(f"Fetched {len(rows)} rows")

        # Enrich and append to Google Doc
        docs_svc = build("docs","v1").documents()
        for idx, row in enumerate(rows, start=2):
            req = row[0]
            fnc = row[1] if len(row)>1 else ""
            prompt = (
                f"You are Fever’s RFP AI assistant.\n"
                f"Requirement: {req}\nFunctionality: {fnc}\n"
                "Write a narrative-rich paragraph explaining how this functionality meets the requirement.\n"
            )
            ai_resp = openai.Completion.create(
                model="text-davinci-003",
                prompt=prompt,
                max_tokens=300
            )
            enriched = ai_resp.choices[0].text.strip()
            docs_svc.batchUpdate(
                documentId=doc_id,
                body={"requests":[{"insertText":{
                    "endOfSegmentLocation":{}, "text": enriched+"\n\n"
                }}]}
            ).execute()
            print(f"Done row {idx}")

        return jsonify(status="complete", rows=len(rows)), 200

    except Exception:
        traceback.print_exc()
        return jsonify(error="Internal error"), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",8080)))
