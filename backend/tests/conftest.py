"""Stubs heavy/optional ML dependencies that aren't needed to exercise
this test suite's actual logic. app/embeddings/bge.py imports
sentence_transformers at module import time (just to reference the
SentenceTransformer class), which pulls in torch -- a multi-hundred-MB
install that has nothing to do with what these tests actually check.
Every test that touches embedding output mocks chunking.get_embedder()
directly rather than relying on a real model, so a lightweight stand-in
class is enough to satisfy the import.
"""
import sys
import types


def _install_stub() -> None:
    stub = types.ModuleType("sentence_transformers")

    class _StubSentenceTransformer:
        def __init__(self, *args, **kwargs):
            raise RuntimeError(
                "sentence-transformers is stubbed out for tests -- mock "
                "get_embedder() instead of constructing a real model."
            )

    stub.SentenceTransformer = _StubSentenceTransformer
    sys.modules["sentence_transformers"] = stub


if "sentence_transformers" not in sys.modules:
    try:
        import sentence_transformers  # noqa: F401
    except ImportError:
        _install_stub()


def _install_docling_stub() -> None:
    """docling (used only by parsers/pdf_parser.py) is a multi-GB install
    with its own model-weight downloads (see backend/Dockerfile's PDF
    layer) -- entirely unrelated to what these tests check, but
    parsers/router.py imports every parser module eagerly (including
    PDFParser) just to build its extension->category dispatch table, so
    anything that imports app.parsers.router or app.agents.domain_managers
    pulls docling in transitively. Only the handful of names pdf_parser.py
    actually references at import time need to exist -- their real
    behavior is never exercised unless a test actually parses a PDF."""
    base_models = types.ModuleType("docling.datamodel.base_models")
    base_models.InputFormat = types.SimpleNamespace(PDF="pdf")

    pipeline_options = types.ModuleType("docling.datamodel.pipeline_options")
    pipeline_options.PdfPipelineOptions = type("PdfPipelineOptions", (), {})
    pipeline_options.RapidOcrOptions = type("RapidOcrOptions", (), {})

    document_converter = types.ModuleType("docling.document_converter")
    document_converter.DocumentConverter = type("DocumentConverter", (), {})
    document_converter.PdfFormatOption = type("PdfFormatOption", (), {})

    datamodel = types.ModuleType("docling.datamodel")
    docling = types.ModuleType("docling")
    docling.datamodel = datamodel
    docling.document_converter = document_converter

    sys.modules["docling"] = docling
    sys.modules["docling.datamodel"] = datamodel
    sys.modules["docling.datamodel.base_models"] = base_models
    sys.modules["docling.datamodel.pipeline_options"] = pipeline_options
    sys.modules["docling.document_converter"] = document_converter


if "docling" not in sys.modules:
    try:
        import docling  # noqa: F401
    except ImportError:
        _install_docling_stub()
