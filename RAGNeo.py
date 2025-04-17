import os
import streamlit as st
import numpy as np
from neo4j import GraphDatabase
import openai

# ✅ OpenAI API-Key laden
openai_api_key = st.secrets["OPENAI_API_KEY"]
if not openai_api_key:
    raise ValueError("❌ OPENAI_API_KEY fehlt in den Streamlit Secrets!")
client = openai.OpenAI(api_key=openai_api_key)

# ✅ Neo4j-Datenbankverbindung
NEO4J_URI = st.secrets["NEO4J_URI"]
NEO4J_USER = st.secrets["NEO4J_USER"]
NEO4J_PASSWORD = st.secrets["NEO4J_PASSWORD"]
driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

def get_neo4j_context(limit=1000):
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
                    label = next(iter(node.labels), "Node")
                    name = (
                        node.get("name") or
                        node.get("key") or
                        node.get("title") or
                        node.get("id") or
                        "Unbekannt"
                    )
                    props = [f"{k}: {v}" for k, v in node.items() if k not in ["name", "key", "title", "id"]]
                    return f"{label} {name}" + (f" ({', '.join(props)})" if props else "")

                m_desc = describe_node(m)
                n_desc = describe_node(n)
                rel_type = r.type

                context_lines.append(f"{m_desc} steht in Beziehung ({rel_type}) zu {n_desc}.")

            return "\n".join(context_lines)

        except Exception as e:
            return f"Fehler beim Laden der Neo4j-Daten: {e}"

def generate_response(context, user_query):
    if not context or not user_query:
        return "❌ Kontext oder Nutzereingabe fehlt."

    messages = [
        {
            "role": "system",
            "content": (
                "Bitte beantworte die Nutzerfrage nur basierend auf den folgenden bereitgestellten Neo4j-Daten. "
                "Nutze keine anderen Quellen oder dein Vorwissen. "
                "Antworte mit 'Keine ausreichenden Daten gefunden', wenn keine Informationen vorhanden sind."
            )
        },
        {
            "role": "user",
            "content": f"Kontext: {context}\nNutzerfrage: {user_query}"
        }
    ]

    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=messages,
            max_tokens=700,
            temperature=0
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"❌ Fehler beim Generieren der Antwort: {str(e)}"

def main():
    st.markdown("### 📊 Neo4j-basiertes Frage-Antwort-System für Finanzkennzahlen")

    user_query = st.text_input("❓ Was möchtest du wissen?")
    send = st.button("Senden")

    if send and user_query:
        with st.spinner("🔍 Lade relevante Neo4j-Daten (nur FinancialMetric)..."):
            context = get_neo4j_context(limit=1000)
        with st.spinner("🤖 Generiere Antwort..."):
            answer = generate_response(context, user_query)
            st.markdown(f"### 📌 Antwort:\n{answer}")

if __name__ == "__main__":
    main()
