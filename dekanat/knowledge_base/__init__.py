"""База знаний: разбор Положения и документов с регламентами, поиск (BM25 по основам слов с синонимами) и вывод обязательных полей.

Всё локально, без языковой модели и внешних API.
"""

from .documents import ALLOWED_SUFFIXES, BASE_ID, KnowledgeBaseError, doc_clauses, read_upload
from .index import ClauseIndex, load_synonym_groups
from .manager import KnowledgeBaseManager
from .parser import load_regulation, parse_regulation_text, read_regulation_file
from .requirements import RequirementModel

__all__ = [
    "ClauseIndex", "RequirementModel", "load_regulation", "load_synonym_groups", "parse_regulation_text", "read_regulation_file",
    "KnowledgeBaseManager", "KnowledgeBaseError", "ALLOWED_SUFFIXES", "BASE_ID", "doc_clauses", "read_upload",
]
