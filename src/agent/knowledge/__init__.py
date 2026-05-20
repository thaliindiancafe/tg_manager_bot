"""Knowledge base: Drive indexing, embeddings, RAG search."""

from src.agent.knowledge.index import sync_drive_knowledge_folder
from src.agent.knowledge.search import search_knowledge

__all__ = ["sync_drive_knowledge_folder", "search_knowledge"]
