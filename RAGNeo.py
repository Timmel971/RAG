import os
import pandas as pd
import PyPDF2
import openai
import streamlit as st
import gdown
import tempfile
import requests
import numpy as np
import time
import concurrent.futures
import hashlib
from io import BytesIO
from neo4j import GraphDatabase
from SPARQLWrapper import SPARQLWrapper, JSON
from openai import OpenAI 

# API-Key aus Streamlit Secrets laden
openai_api_key = st.secrets["OPENAI_API_KEY"]

# Prüfe, ob der API-Key korrekt geladen wurde
if not openai_api_key:
    raise ValueError("❌ OPENAI_API_KEY ist nicht in den Streamlit Secrets gesetzt!")

# OpenAI-Client mit dem Secret-API-Key erstellen
client = openai.OpenAI(api_key=openai_api_key)

# ✅ Neo4j-Datenbankverbindung
NEO4J_URI = st.secrets["NEO4J_URI"]
NEO4J_USER = st.secrets["NEO4J_USER"]
NEO4J_PASSWORD = st.secrets["NEO4J_PASSWORD"]

driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

# ✅ Google Drive-Ordner-Link
GDRIVE_URL = st.secrets["GDRIVE_URL"]
DOWNLOAD_PATH = tempfile.mkdtemp()

# ✅ Caching für Embeddings (verhindert doppelte API-Calls)
embedding_cache = {}

# ✅ Funktion zum Herunterladen von Google Drive-Dateien
def download_drive_folder(output_path):
    try:
        folder_id = GDRIVE_URL.split('/')[-1]
        gdown.download_folder(id=folder_id, output=output_path, quiet=False)
        print(f"✅ Google Drive-Daten wurden erfolgreich nach {output_path} heruntergeladen!")
    except Exception as e:
        print(f"❌ Fehler beim Herunterladen: {e}")
        print("🔹 Falls gdown fehlt, installiere es mit: pip install gdown")

# ✅ Funktion zum Einlesen von PDFs
def read_folder_data(folder_path):
    files_data = []
    for file_name in os.listdir(folder_path):
        file_path = os.path.join(folder_path, file_name)
        if file_name.endswith(".pdf"):
            pdf_text = []
            with open(file_path, "rb") as pdf_file:
                pdf_reader = PyPDF2.PdfReader(pdf_file)
                for page in pdf_reader.pages:
                    pdf_text.append(page.extract_text() or "")
            files_data.append(" ".join(pdf_text))
    return files_data

# ✅ Funktion zum Erstellen von Text-Chunks (Kleinere Größe = Schnellere Embeddings)
def split_text(text, max_length=500):
    words = text.split()
    chunks = []
    current_chunk = []
    current_length = 0
    for word in words:
        if current_length + len(word) + 1 > max_length and current_chunk:
            chunks.append(" ".join(current_chunk))
            current_chunk = []
            current_length = 0
        current_chunk.append(word)
        current_length += len(word) + 1
    if current_chunk:
        chunks.append(" ".join(current_chunk))
    return chunks

# ✅ OpenAI Embedding-Funktion mit neuer API-Syntax
def get_embedding(text, model="text-embedding-3-small"):
    text_hash = hashlib.md5(text.encode()).hexdigest()
    if text_hash in embedding_cache:
        return embedding_cache[text_hash]
    
    response = client.embeddings.create(
        model=model,
        input=[text]
    )
    
    embedding = np.array(response.data[0].embedding)  # ✅ NEUE SYNTAX
    embedding_cache[text_hash] = embedding
    return embedding

# ✅ Multithreading für Embeddings (Schneller als normale Schleife)
def create_embeddings_parallel(documents, max_length=500):
    chunk_embeddings = []
    with concurrent.futures.ThreadPoolExecutor() as executor:
        future_to_chunk = {executor.submit(get_embedding, chunk): chunk for doc in documents for chunk in split_text(doc, max_length)}
        
        progress_bar = st.progress(0)
        total_chunks = len(future_to_chunk)
        completed = 0

        for future in concurrent.futures.as_completed(future_to_chunk):
            try:
                chunk_embeddings.append((future_to_chunk[future], future.result()))
            except Exception as e:
                st.error(f"Fehler bei Embeddings: {e}")
            completed += 1
            progress_bar.progress(completed / total_chunks)

    return chunk_embeddings

# ✅ Relevante Chunks anhand von Embeddings abrufen
def retrieve_relevant_chunks(query, chunk_embeddings, top_n=3):
    query_emb = get_embedding(query)
    similarities = [(chunk, cosine_similarity(query_emb, emb)) for chunk, emb in chunk_embeddings]
    
    similarities.sort(key=lambda x: x[1], reverse=True)
    top_chunks = [chunk for chunk, sim in similarities[:top_n]]
    
    return "\n\n".join(top_chunks)

# ✅ Kosinus-Ähnlichkeit berechnen
def cosine_similarity(a, b):
    return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))

# ✅ OpenAI GPT-3.5-Turbo für Antworten nutzen
def generate_response(context, user_query):
    messages = [
        {"role": "system", "content": "Antwort basierend auf Geschäftsberichten & Neo4j-Daten."},
        {"role": "user", "content": f"Context: {context}\nUser Question: {user_query}"}
    ]
    
    response = client.chat.completions.create(  # ✅ NEUE SYNTAX
        model="gpt-3.5-turbo",
        messages=messages,
        max_tokens=700
    )
    
    return response.choices[0].message.content.strip()  # ✅ NEUE SYNTAX

# ✅ Haupt-Streamlit-UI
def main():
    st.markdown("### 📌 Hallo, hier ist Neo und ich bin Ihr persönlicher Assistent rund um das Unternehmen der Siemens AG!")

    # 📌 Lade Google Drive-Dokumente
    if "documents" not in st.session_state:
        try:
            st.info("📂 Lade Geschäftsberichte aus Google Drive...")
            download_drive_folder(DOWNLOAD_PATH)
            st.session_state.documents = read_folder_data(DOWNLOAD_PATH)
        except Exception as e:
            st.error(f"❌ Fehler beim Laden der Daten: {e}")

    # 📌 Erstelle Embeddings mit Fortschrittsanzeige
    if "chunk_embeddings" not in st.session_state:
        with st.spinner("🔍 Erzeuge Embeddings für Dokumente..."):
            st.session_state.chunk_embeddings = create_embeddings_parallel(st.session_state.documents, max_length=500)

    # 📌 Chat-Funktion
    user_query = st.text_input("❓ Ihre Frage:")
    send = st.button("Senden")

    if send and user_query:
        with st.spinner("🔍 Generiere Antwort..."):
            response = retrieve_relevant_chunks(user_query, st.session_state.chunk_embeddings, top_n=3)
            st.markdown(f"### 📌 Antwort:\n{generate_response(response, user_query)}")

# ✅ Streamlit starten
if __name__ == "__main__":
    main()
