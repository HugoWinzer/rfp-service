# main.py
import os
import pickle
import numpy as np
import faiss
import openai
import flask
from concurrent.futures import ThreadPoolExecutor
from googleapiclient.discovery import build
import google.auth

app = flask.Flask(__name__)
openai.api_key = os.environ["OPENAI_API_KEY"]

# ——— Load FAISS index + document store at startup ———
with open("faiss_index/index.pkl", "rb") as f:
    documents = pickle.load(f)  # list[str] or similar

faiss_index = faiss.read_index("faiss_index/index.faiss")

# ——— Initialize Google Sheets API client ———
creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/spreadsheets"])
sheets_service = build("sheets", "v4", credentials=creds)

def get_column_letter(n: int) -> str:
    """Convert 1-based index to Excel column letter."""
    letter = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        letter = chr(65 + rem) + letter
    return letter

def enrich_and_generate(user_input: str) -> str:
    """Retrieve top-5 contexts via FAISS and call ChatCompletion."""
    # 1) Embed the user input
    embed_resp = openai.Embedding.create(
        model="text-embedding-ada-002",
        input=user_input
    )
    emb = np.array(embed_resp["data"][0]["embedding"], dtype="float32").reshape(1, -1)

    # 2) FAISS search
    distances, indices = faiss_index.search(emb, 5)
    contexts = [documents[i] for i in indices[0] if i < len(documents)]
    context_block = "\n---\n".join(contexts)

    # 3) Call ChatCompletion with retrieval context
    system_prompt = (
        "Use the following context to help answer the user’s question:\n\n"
        f"{context_block}"
    )
    chat = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_input},
        ],
        temperature=0.7,
    )
    return chat.choices[0].message.content.strip()

@app.route("/", methods=["GET"])
def ui():
    return flask.send_from_directory("static", "index.html")

@app.route("/health", methods=["GET"])
def health():
    return "OK", 200

@app.route("/start", methods=["POST"])
def start_handler():
    data = flask.request.get_json()
    sheet_id = data.get("sheet_id")
    if not sheet_id:
        return flask.jsonify({"error": "Missing sheet_id"}), 400

    # 1) Read header row to find (or create) “GPT Output” column
    hdr = sheets_service.spreadsheets().values().get(
        spreadsheetId=sheet_id,
        range="'Sheet1'!1:1"
    ).execute().get("values", [[]])[0]

    if "GPT Output" not in hdr:
        hdr.append("GPT Output")
        sheets_service.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range="'Sheet1'!1:1",
            valueInputOption="RAW",
            body={"values": [hdr]}
        ).execute()

    output_col_idx = hdr.index("GPT Output") + 1  # 1-based
    col_letter = get_column_letter(output_col_idx)

    # 2) Fetch all data rows (starting from row 2)
    rows_resp = sheets_service.spreadsheets().values().get(
        spreadsheetId=sheet_id,
        range="'Sheet1'!A2:A"
    ).execute()
    inputs = rows_resp.get("values", [])

    # 3) Process each row in parallel
    def worker(args):
        (row_vals, row_num) = args
        user_text = row_vals[0] if row_vals else ""
        try:
            out = enrich_and_generate(user_text)
            return (row_num, out)
        except Exception as e:
            return (row_num, f"fail: {e}")

    tasks = [ (inputs[i], i + 2) for i in range(len(inputs)) ]
    with ThreadPoolExecutor(max_workers=5) as ex:
        results = list(ex.map(worker, tasks))

    # 4) Batch-update all outputs back into the sheet
    data = []
    for row_num, text in results:
        data.append({
            "range": f"Sheet1!{col_letter}{row_num}",
            "majorDimension": "ROWS",
            "values": [[text]]
        })

    sheets_service.spreadsheets().values().batchUpdate(
        spreadsheetId=sheet_id,
        body={
            "valueInputOption": "RAW",
            "data": data
        }
    ).execute()

    success_count = sum(1 for _,t in results if not t.startswith("fail:"))
    fail_count    = len(results) - success_count
    return flask.jsonify({
        "total": len(results),
        "successes": success_count,
        "failures": fail_count
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
