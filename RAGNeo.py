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
NEO4J_URI = "neo4j://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "timmelpanthers"

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
        
# Funktion zum Einlesen von PDFs
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

# Funktion zum Erstellen von Text-Chunks (kleinere Chunks = schnellere Embeddings)
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

# OpenAI Embedding-Funktion
def get_embedding(text, model="text-embedding-3-small"):
    text_hash = hashlib.md5(text.encode()).hexdigest()
    if text_hash in embedding_cache:
        return embedding_cache[text_hash]
    
    response = client.embeddings.create(
        model=model,
        input=[text]
    )
    
    embedding = np.array(response.data[0].embedding)
    embedding_cache[text_hash] = embedding
    return embedding

# Multithreading für Embeddings
def create_embeddings_parallel(documents, max_length=500):
    chunk_embeddings = []
    with concurrent.futures.ThreadPoolExecutor() as executor:
        future_to_chunk = {executor.submit(get_embedding, chunk): chunk 
                           for doc in documents for chunk in split_text(doc, max_length)}
        
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

# Kosinus-Ähnlichkeit berechnen
def cosine_similarity(a, b):
    return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))

# Relevante Chunks anhand von Embeddings abrufen
def retrieve_relevant_chunks(query, chunk_embeddings, top_n=3):
    query_emb = get_embedding(query)
    similarities = [(chunk, cosine_similarity(query_emb, emb)) for chunk, emb in chunk_embeddings]
    similarities.sort(key=lambda x: x[1], reverse=True)
    top_chunks = [chunk for chunk, sim in similarities[:top_n]]
    return "\n\n".join(top_chunks)

# Antwort generieren – ausschließlich basierend auf dem übergebenen Kontext
def generate_response(context, user_query):
    messages = [
        {"role": "system", "content": "Bitte antworte ausschließlich basierend auf den folgenden bereitgestellten Daten. Nutze keine anderen Quellen oder dein Vorwissen. Falls keine passenden Informationen im Kontext vorhanden sind, antworte mit 'Keine ausreichenden Daten gefunden'."},
        {"role": "user", "content": f"Context: {context}\nUser Question: {user_query}"}
    ]
    
    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=messages,
        max_tokens=700,
        temperature=0  # Niedrige Temperatur reduziert Halluzinationen
    )
    
    return response.choices[0].message.content.strip()

# Funktion, um Kontext aus der Neo4j-Datenbank abzurufen
def get_neo4j_context():
    with driver.session() as session:
        result = session.run("""
            MATCH (s:Unternehmen)<-[r:IST_TOCHTER_VON]-(t:Unternehmen)
            RETURN s.name AS Mutter, t.name AS Tochter
            LIMIT 25
        """)
        context_lines = []
        for record in result:
            context_lines.append(f"{record['Tochter']} ist ein Tochterunternehmen von {record['Mutter']}.")
        return "\n".join(context_lines)

# Haupt-Streamlit-UI
def main():
    st.markdown("### 📌 Hallo, hier ist Neo – Ihr persönlicher Assistent rund um das Unternehmen der Siemens AG!")

    # Lade Google Drive-Dokumente
    if "documents" not in st.session_state:
        try:
            st.info("📂 Lade Geschäftsberichte aus Google Drive...")
            download_drive_folder(DOWNLOAD_PATH)
            st.session_state.documents = read_folder_data(DOWNLOAD_PATH)
        except Exception as e:
            st.error(f"❌ Fehler beim Laden der Daten: {e}")

    # Erstelle Embeddings für die Dokumente
    if "chunk_embeddings" not in st.session_state:
        with st.spinner("🔍 Erzeuge Embeddings für Dokumente..."):
            st.session_state.chunk_embeddings = create_embeddings_parallel(st.session_state.documents, max_length=500)

    # Chat-Funktion
    user_query = st.text_input("❓ Ihre Frage:")
    send = st.button("Senden")

    if send and user_query:
        with st.spinner("🔍 Generiere Antwort..."):
            # Abruf des Kontextes aus der Neo4j-Datenbank
            neo4j_context = get_neo4j_context()
            # Abruf des relevanten Kontextes aus den Dokumenten
            document_context = retrieve_relevant_chunks(user_query, st.session_state.chunk_embeddings, top_n=3)
            # Kombiniere beide Kontexte
            full_context = f"Neo4j-Daten:\n{neo4j_context}\n\nDokument-Daten:\n{document_context}"
            answer = generate_response(full_context, user_query)
            st.markdown(f"### 📌 Antwort:\n{answer}")

if __name__ == "__main__":
    main()
