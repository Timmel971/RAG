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

# ✅ OpenAI API-Schlüssel sicher setzen
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    st.error("❌ Kein OpenAI API Key gefunden! Bitte setze `OPENAI_API_KEY` als GitHub Secret.")
else:
    client = openai.OpenAI(api_key=OPENAI_API_KEY)

# ✅ Neo4j-Datenbankverbindung
NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "timmelpanthers"
driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

# ✅ Google Drive-Ordner-Link
GDRIVE_URL = "https://drive.google.com/drive/folders/1mln0vjJv3u4hL1mJwJWX_YT6DSP_5cHC?usp=drive_link"
DOWNLOAD_PATH = tempfile.mkdtemp()

# ✅ Caching für Embeddings
embedding_cache = {}

# ✅ Google Drive Dateien herunterladen
def download_drive_folder(output_path):
    try:
        folder_id = GDRIVE_URL.split('/')[-1]
        gdown.download_folder(id=folder_id, output=output_path, quiet=False)
        print(f"✅ Google Drive-Daten erfolgreich nach {output_path} geladen!")
    except Exception as e:
        print(f"❌ Fehler beim Herunterladen: {e}\n🔹 Falls `gdown` fehlt: pip install gdown")

# ✅ PDFs auslesen
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

# ✅ Text in kleinere Chunks unterteilen
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

# ✅ OpenAI Embeddings mit neuer API nutzen
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

# ✅ Parallele Embedding-Erzeugung (Multithreading)
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

# ✅ Kosinus-Ähnlichkeit berechnen
def cosine_similarity(a, b):
    return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))

# ✅ Relevante Chunks für die Frage abrufen
def retrieve_relevant_chunks(query, chunk_embeddings, top_n=3):
    query_emb = get_embedding(query)
    similarities = [(chunk, cosine_similarity(query_emb, emb)) for chunk, emb in chunk_embeddings]
    
    similarities.sort(key=lambda x: x[1], reverse=True)
    top_chunks = [chunk for chunk, sim in similarities[:top_n]]
    
    return "\n\n".join(top_chunks)

# ✅ OpenAI GPT-3.5-Turbo zur Antwortgenerierung
def generate_response(context, user_query):
    messages = [
        {"role": "system", "content": "Antwort basierend auf Geschäftsberichten & Neo4j-Daten."},
        {"role": "user", "content": f"Context: {context}\nUser Question: {user_query}"}
    ]
    
    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=messages,
        max_tokens=700
    )
    
    return response.choices[0].message.content.strip()

# ✅ Haupt-Streamlit-UI
def main():
    st.markdown("### 📌 Fragen Sie zu Siemens & Geschäftsberichten!")

    # 📌 Siemens-Datenbank mit Neo4j synchronisieren
    if st.button("🔄 Siemens-Daten aus Wikidata abrufen"):
        sparql = SPARQLWrapper("https://query.wikidata.org/sparql")
        sparql.setQuery("""
            SELECT ?tochter ?tochterLabel WHERE {
              ?tochter wdt:P749 wd:Q46856.
              SERVICE wikibase:label { bd:serviceParam wikibase:language "[AUTO_LANGUAGE],de". }
            }
        """)
        sparql.setReturnFormat(JSON)
        results = sparql.query().convert()
        subsidiaries = [result["tochterLabel"]["value"] for result in results["results"]["bindings"]]
        
        with driver.session() as session:
            for name in subsidiaries:
                session.run("""
                    MERGE (t:Unternehmen {name: $name})
                    MERGE (s:Unternehmen {name: 'Siemens AG'})
                    MERGE (t)-[:IST_TOCHTER_VON]->(s)
                """, name=name)

        st.success(f"{len(subsidiaries)} Tochtergesellschaften wurden in Neo4j gespeichert!")

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
