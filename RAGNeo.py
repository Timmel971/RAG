import streamlit as st
from neo4j import GraphDatabase
import openai

# 🔐 Secrets laden
openai_api_key = st.secrets["OPENAI_API_KEY"]
NEO4J_URI = st.secrets["NEO4J_URI"]
NEO4J_USER = st.secrets["NEO4J_USER"]
NEO4J_PASSWORD = st.secrets["NEO4J_PASSWORD"]

# ✅ OpenAI-Client
client = openai.OpenAI(api_key=openai_api_key)

# ✅ Neo4j-Verbindung
driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

def get_neo4j_context(limit=500):
    with driver.session() as session:
        context_lines = []
        result = session.run("""
            MATCH (a)-[r]->(b)
            RETURN a, r, b
            LIMIT $limit
        """, {"limit": limit})
        for record in result:
            a = record["a"]
            b = record["b"]
            r = record["r"]

            def describe_node(node):
                name = (
                    node.get("name") or
                    node.get("key") or
                    node.get("title") or
                    node.get("value") or
                    node.get("type") or
                    "Unbekanntes Objekt"
                )
                props = [f"{k}: {v}" for k, v in node.items() if k != "name"]
                return f"{name} ({', '.join(props)})" if props else name

            context_lines.append(
                f"{describe_node(a)} steht in Beziehung ({r.type}) zu {describe_node(b)}."
            )
        return "\n".join(context_lines)

def generate_response(context, user_query):
    messages = [
        {"role": "system", "content": "Antworte nur basierend auf dem bereitgestellten Neo4j-Datenkontext. Keine Vermutungen oder externes Wissen verwenden. Wenn du keine Informationen findest, gib 'Keine ausreichenden Daten gefunden' zurück."},
        {"role": "user", "content": f"Context:\n{context}\n\nFrage:\n{user_query}"}
    ]
    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=messages,
        max_tokens=800,
        temperature=0
    )
    return response.choices[0].message.content.strip()

# ✅ Streamlit-Frontend
def main():
    st.title("🔍 Neo4j-Wissensabfrage für Siemens-Daten")

    user_query = st.text_input("❓ Stelle eine Frage zum Unternehmen (z. B. Ergebnismarge Smart Infrastructure 2023):")
    if st.button("Antwort generieren") and user_query:
        with st.spinner("Hole Daten aus Neo4j..."):
            context = get_neo4j_context(limit=1000)
            st.text_area("📄 Kontext aus Neo4j:", value=context, height=300)
        with st.spinner("Formuliere Antwort mit GPT..."):
            answer = generate_response(context, user_query)
            st.markdown(f"### 📌 Antwort:\n{answer}")

if __name__ == "__main__":
    main()
