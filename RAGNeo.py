import streamlit as st
from neo4j import GraphDatabase
import openai

# 🔑 API & DB-Zugangsdaten
openai_api_key = st.secrets["OPENAI_API_KEY"]
NEO4J_URI = st.secrets["NEO4J_URI"]
NEO4J_USER = st.secrets["NEO4J_USER"]
NEO4J_PASSWORD = st.secrets["NEO4J_PASSWORD"]

client = openai.OpenAI(api_key=openai_api_key)
driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

# 🧠 Neo4j-Graphdaten abrufen
def get_neo4j_context():
    with driver.session() as session:
        context_lines = []
        result = session.run("""
            MATCH (a)-[r]->(b)
            RETURN a, r, b
            LIMIT 100
        """)
        for record in result:
            a = record["a"]
            b = record["b"]
            r = record["r"]

            def describe_node(node):
                name = (
                    node.get("name") or
                    node.get("key") or
                    node.get("title") or
                    node.get("wert") or
                    node.get("art") or
                    node.get("type") or
                    "Unbekanntes Objekt"
                )
                props = [f"{k}: {v}" for k, v in node.items() if k != "name"]
                return f"{name} ({', '.join(props)})" if props else name

            a_desc = describe_node(a)
            b_desc = describe_node(b)
            rel_type = r.type

            context_lines.append(f"{a_desc} steht in Beziehung ({rel_type}) zu {b_desc}.")
        return "\n".join(context_lines)

# 🤖 Antwort generieren
def generate_response(context, user_query):
    messages = [
        {
            "role": "system",
            "content": "Beantworte ausschließlich auf Basis der bereitgestellten Daten aus der Neo4j-Datenbank. Nutze keine eigenen Annahmen."
        },
        {
            "role": "user",
            "content": f"Context:\n{context}\n\nFrage:\n{user_query}"
        }
    ]
    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=messages,
        max_tokens=600,
        temperature=0
    )
    return response.choices[0].message.content.strip()

# 🖥️ Streamlit-UI
def main():
    st.title("Neo4j Question Answering")

    user_query = st.text_input("❓ Stelle deine Frage zu Siemens-Daten (Neo4j):")
    if st.button("Antwort generieren") and user_query:
        with st.spinner("Hole Kontext aus Neo4j..."):
            context = get_neo4j_context()

        with st.spinner("Generiere Antwort..."):
            response = generate_response(context, user_query)
            st.markdown(f"### 📌 Antwort:\n{response}")

if __name__ == "__main__":
    main()
