FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code + FAISS index + static UI
COPY main.py .
COPY faiss_index/ ./faiss_index/
COPY static/ ./static/

ENV PORT 8080
EXPOSE 8080

CMD ["gunicorn", "main:app", "--bind", "0.0.0.0:8080"]
