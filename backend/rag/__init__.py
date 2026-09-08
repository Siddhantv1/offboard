"""RAG pipeline for the FAQ chatbot.

chunk -> embed (Model2Vec, local) -> persist (Chroma vector DB)
-> hybrid retrieve (semantic + BM25, fused with RRF) -> generate (Portkey).
"""
