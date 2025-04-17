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

# ✅ API-Key aus Streamlit Secrets laden
openai_api_key = st.secrets["OPENAI_API_KEY"]
if not openai_api_key:
    raise ValueError("❌ OPENAI_API_KEY ist nicht in den Streamlit Secrets gesetzt!")
client = openai.OpenAI(api_key=openai_api_key)

# ✅ Neo4j-Datenbankverbindung
NEO4J_URI = st.secrets["NEO4J_URI"]
NEO4J_USER = st.secrets["NEO4J_USER"]
NEO4J_PASSWORD = st.secrets["NEO4J_PASSWORD"]
driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

# ✅ Google Drive-Ordner-Link
GDRIVE_URL = st.secrets["GDRIVE_URL"]
DOWNLOAD_PATH = tempfile.mkdtemp()
embedding_cache = {}

def download_drive_folder(output_path):
    try:
        folder_id = GDRIVE_URL.split('/')[-1]
        gdown.download_folder(id=folder_id, output=output_path, quiet=False)
    except Exception as e:
        print(f"❌ Fehler beim Herunterladen: {e}")

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

def get_embedding(text, model="text-embedding-3-small"):
    text_hash = hashlib.md5(text.encode()).hexdigest()
    if text_hash in embedding_cache:
        return embedding_cache[text_hash]
    response = client.embeddings.create(model=model, input=[text])
    embedding = np.array(response.data[0].embedding)
    embedding_cache[text_hash] = embedding
    return embedding

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

def cosine_similarity(a, b):
    return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))

def retrieve_relevant_chunks(query, chunk_embeddings, top_n=3):
    query_emb = get_embedding(query)
    similarities = [(chunk, cosine_similarity(query_emb, emb)) for chunk, emb in chunk_embeddings]
    similarities.sort(key=lambda x: x[1], reverse=True)
    return "\n\n".join(chunk for chunk, _ in similarities[:top_n])

def get_financialmetric_neo4j_context(limit=1000):
    with driver.session() as session:
        try:
            context_lines = []
            result = session.run("""
                MATCH (m:FinancialMetric)-[r]-(n)
                RETURN m, r, n
                LIMIT $limit
            """, limit=limit)

            for record in result:
                m = record["m"]
                n = record["n"]
                r = record["r"]

                def describe_node(node):
                    label = list(node.labels)[0] if hasattr(node, "labels") and node.labels else "Node"
                    name = node.get("name") or node.get("key") or node.get("id") or label
                    props = ", ".join([f"{k}: {v}" for k, v in node.items() if k not in ["name", "key", "id"]])
                    return f"{label}({name}) [{props}]" if props else f"{label}({name})"

                m_desc = describe_node(m)
                n_desc = describe_node(n)
                rel_type = r.type
                context_lines.append(f"{m_desc} -[:{rel_type}]- {n_desc}")

            return "\n".join(context_lines)
        except Exception as e:
            return f"Fehler beim Laden der Neo4j-Daten: {e}"

def generate_response(context, user_query):
    messages = [
        {"role": "system", "content": "Antworte ausschließlich auf Basis der folgenden bereitgestellten Daten. Nutze keine anderen Quellen oder Vorwissen. Falls keine passende Information im Kontext vorhanden ist, antworte mit 'Keine ausreichenden Daten gefunden'."},
        {"role": "user", "content": f"Kontext: {context}\nBenutzerfrage: {user_query}"}
    ]
    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=messages,
        max_tokens=700,
        temperature=0
    )
    return response.choices[0].message.content.strip()

def main():
    st.markdown("### 🤖 Willkommen bei Neo – deinem Assistenten für Siemens-Konzern-Analysen!")

    if "documents" not in st.session_state:
        st.info("📥 Lade Geschäftsberichte herunter...")
        download_drive_folder(DOWNLOAD_PATH)
        st.session_state.documents = read_folder_data(DOWNLOAD_PATH)

    if "chunk_embeddings" not in st.session_state:
        with st.spinner("🔄 Erstelle Embeddings..."):
            st.session_state.chunk_embeddings = create_embeddings_parallel(st.session_state.documents, max_length=500)

    user_query = st.text_input("🔍 Deine Frage an Neo:")
    send = st.button("Antwort generieren")

    if send and user_query:
        with st.spinner("⏳ Generiere Antwort..."):
            graph_context = get_financialmetric_neo4j_context(limit=1000)
            document_context = retrieve_relevant_chunks(user_query, st.session_state.chunk_embeddings, top_n=3)
            combined_context = f"Neo4j-Finanzdaten:\n{graph_context}\n\nGeschäftsberichte:\n{document_context}"
            answer = generate_response(combined_context, user_query)
            st.markdown(f"### ✅ Antwort:\n{answer}")

if __name__ == "__main__":
    main()
