import os
import pickle
import numpy as np
import faiss
import openai
import flask
from googleapiclient.discovery import build
import google.auth

app = flask.Flask(__name__)
openai.api_key = os.environ["OPENAI_API_KEY"]

# ——— Load FAISS index + document store at startup ———
with open("faiss_index/index.pkl", "rb") as f:
    documents = pickle.load(f)

faiss_index = faiss.read_index("faiss_index/index.faiss")

# ——— Initialize Google Sheets API client ———
creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/spreadsheets"])
sheets_service = build("sheets", "v4", credentials=creds)

def get_column_letter(n: int) -> str:
    letter = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        letter = chr(65 + rem) + letter
    return letter

def enrich_and_generate(user_input: str, previous_answers: list) -> str:
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

    # 3) Previous answers block (limit to last 3 for token safety)
    prev_block = ""
    if previous_answers:
        prev_block = "\n\nPrevious answers (do NOT copy, avoid repeating):\n" + \
            "\n---\n".join(previous_answers[-3:])

    # 4) Improved system prompt
    system_prompt = (
        "You are an expert at writing responses for RFPs (Request for Proposals) for Fever, a ticketing and event platform. "
        "Your job is to answer requirements in a professional, factual, and concise way, avoiding excessive praise, exaggeration, or repetitive structure. "
        "Vary your phrasing and focus on clarity and specific value. "
        "Do not copy or repeat the same structure as earlier responses in this session."
        "\n\nContext:\n"
        f"{context_block}"
        f"{prev_block}"
    )

    chat = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_input},
        ],
        temperature=0.5,
        max_tokens=512,
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
    try:
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

        # 3) Process and update each row immediately
        results = []
        previous_outputs = []
        for i, row in enumerate(inputs):
            row_num = i + 2
            user_text = row[0] if row else ""
            try:
                out = enrich_and_generate(user_text, previous_outputs)
                # Write result back immediately!
                sheets_service.spreadsheets().values().update(
                    spreadsheetId=sheet_id,
                    range=f"Sheet1!{col_letter}{row_num}",
                    valueInputOption="RAW",
                    body={"values": [[out]]}
                ).execute()
                results.append({"row": row_num, "input": user_text, "output": out, "status": "success", "error": ""})
                previous_outputs.append(out)
            except Exception as e:
                error_msg = str(e)
                sheets_service.spreadsheets().values().update(
                    spreadsheetId=sheet_id,
                    range=f"Sheet1!{col_letter}{row_num}",
                    valueInputOption="RAW",
                    body={"values": [[f"ERROR: {error_msg}"]]}
                ).execute()
                results.append({"row": row_num, "input": user_text, "output": "", "status": "fail", "error": error_msg})
                previous_outputs.append("")

        success_count = sum(1 for r in results if r["status"] == "success")
        fail_count    = len(results) - success_count
        return flask.jsonify({
            "total": len(results),
            "successes": success_count,
            "failures": fail_count,
            "results": results  # detailed per-row log!
        })
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print("----- UNCAUGHT ERROR IN /start -----")
        print(tb)
        return flask.jsonify({"error": f"Internal server error: {str(e)}", "traceback": tb}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
