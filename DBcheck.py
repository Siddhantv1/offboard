import chromadb
client = chromadb.PersistentClient(path="data/.chroma")

print(client.list_collections())              # -> [Collection(name=exit_docs)]
col = client.get_collection("exit_docs")
print(col.count())                             # 52

# peek at a few chunks (documents + metadata)
res = col.peek(3)                              # or col.get(limit=3, include=["documents","metadatas"])
for doc, meta in zip(res["documents"], res["metadatas"]):
    print(meta, "→", doc[:80])

# include the actual embedding vectors too
res = col.get(limit=2, include=["documents", "metadatas", "embeddings"])
print(len(res["embeddings"][0]), "dims")       # 256

# run a live similarity query
from backend.rag import embedder
hits = col.query(query_embeddings=[embedder.embed_one("final settlement")],
                n_results=3, include=["documents", "metadatas", "distances"])
print(hits["metadatas"][0], hits["distances"][0])