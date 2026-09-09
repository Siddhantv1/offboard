"""RAG pipeline for the FAQ chatbot.

chunk -> embed (Portkey -> Azure OpenAI) -> persist (Chroma vector DB)
-> hybrid retrieve (semantic + BM25, fused with RRF) -> generate (Portkey -> Bedrock).
"""
