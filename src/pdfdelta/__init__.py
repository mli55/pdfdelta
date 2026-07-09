"""pdfdelta — visual diff for born-digital PDFs."""

from .annotate import apply_annotations
from .compare import compare_documents
from .extract import extract_document

__all__ = ["extract_document", "compare_documents", "apply_annotations"]
__version__ = "0.1.2"
