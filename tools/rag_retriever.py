"""
rag_retriever.py : Assistant documentaire (RAG) pour AutoOFFBEAT.

Équivalent de l'assistant RAG d'AutoFLUKA, porté sur la doc OFFBEAT/OpenFOAM.
Ingère les documents de offbeat_skills/docs/ (Markdown, txt, PDF), les indexe
dans un magasin vectoriel FAISS local, et expose un outil `offbeat_knowledge`
que le superviseur peut interroger.

Stack 100 % locale (fidèle à la philosophie du projet) :
  - Embeddings : Ollama `nomic-embed-text` (EMBED_MODEL, à tirer une fois :
    `ollama pull nomic-embed-text`)
  - Magasin vectoriel : FAISS (léger, pas de serveur, persistance sur disque)

Usage :
  # (re)construire l'index après avoir déposé des documents :
  python -m tools.rag_retriever --ingest

  # dans le code (superviseur) :
  from tools.rag_retriever import get_knowledge_tool
  tool = get_knowledge_tool()
"""

import os
import argparse
from pathlib import Path

SKILLS_DIR = Path(__file__).parent.parent / "offbeat_skills"
DOCS_DIR = Path(os.getenv("RAG_DOCS_DIR", SKILLS_DIR / "docs"))
INDEX_DIR = Path(os.getenv("RAG_INDEX_DIR", SKILLS_DIR / ".rag_index"))
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

_SUFFIXES = {".md", ".txt", ".pdf"}


# --------------------------------------------------------------------------
# Ingestion
# --------------------------------------------------------------------------

from langchain_core.embeddings import Embeddings


class _PrefixedEmbeddings(Embeddings):
    """Enveloppe OllamaEmbeddings pour ajouter les préfixes de tâche exigés
    par nomic-embed-text (`search_document:` à l'indexation, `search_query:`
    à la requête). Sans ces préfixes, la pertinence de nomic chute nettement.
    Pour un modèle non-nomic, les préfixes sont neutres (désactivés)."""

    def __init__(self, base, use_prefix: bool):
        self._base = base
        self._use_prefix = use_prefix

    def embed_documents(self, texts):
        if self._use_prefix:
            texts = [f"search_document: {t}" for t in texts]
        return self._base.embed_documents(texts)

    def embed_query(self, text):
        if self._use_prefix:
            text = f"search_query: {text}"
        return self._base.embed_query(text)


def _get_embeddings():
    from langchain_ollama import OllamaEmbeddings  # noqa: PLC0415
    base = OllamaEmbeddings(model=EMBED_MODEL, base_url=OLLAMA_BASE_URL)
    return _PrefixedEmbeddings(base, use_prefix="nomic" in EMBED_MODEL.lower())


def _load_documents(docs_dir: Path) -> list:
    """Charge tous les documents supportés du dossier (récursif)."""
    from langchain_community.document_loaders import TextLoader, PyPDFLoader  # noqa: PLC0415

    docs = []
    for path in sorted(docs_dir.rglob("*")):
        if path.suffix.lower() not in _SUFFIXES or not path.is_file():
            continue
        try:
            if path.suffix.lower() == ".pdf":
                docs.extend(PyPDFLoader(str(path)).load())
            else:
                docs.extend(TextLoader(str(path), encoding="utf-8").load())
        except Exception as exc:  # un doc illisible ne doit pas tout bloquer
            print(f"[rag] doc ignoré ({path.name}) : {exc}")
    return docs


def build_index(docs_dir: Path = DOCS_DIR, index_dir: Path = INDEX_DIR) -> int:
    """Construit (ou reconstruit) l'index FAISS. Retourne le nombre de chunks."""
    from langchain_text_splitters import RecursiveCharacterTextSplitter  # noqa: PLC0415
    from langchain_community.vectorstores import FAISS  # noqa: PLC0415

    docs = _load_documents(docs_dir)
    if not docs:
        raise RuntimeError(
            f"Aucun document dans {docs_dir}. Dépose des .md/.txt/.pdf "
            "(doc OFFBEAT/OpenFOAM) puis relance l'ingestion."
        )
    chunks = RecursiveCharacterTextSplitter(
        chunk_size=1000, chunk_overlap=150
    ).split_documents(docs)

    store = FAISS.from_documents(chunks, _get_embeddings())
    index_dir.mkdir(parents=True, exist_ok=True)
    store.save_local(str(index_dir))
    print(f"[rag] index construit : {len(docs)} docs -> {len(chunks)} chunks "
          f"-> {index_dir}")
    return len(chunks)


# --------------------------------------------------------------------------
# Récupération / outil
# --------------------------------------------------------------------------

def get_retriever(k: int = 4):
    """Charge l'index FAISS (le construit s'il est absent) et renvoie un
    retriever. Lève une exception si l'index ne peut pas être obtenu."""
    from langchain_community.vectorstores import FAISS  # noqa: PLC0415

    if not (INDEX_DIR / "index.faiss").exists():
        build_index()  # tentative de construction automatique
    store = FAISS.load_local(
        str(INDEX_DIR), _get_embeddings(), allow_dangerous_deserialization=True
    )
    return store.as_retriever(search_kwargs={"k": k})


def get_knowledge_tool():
    """Retourne l'outil LangChain `offbeat_knowledge` (retriever tool),
    prêt à être ajouté à la liste d'outils du superviseur."""
    from langchain_core.tools import create_retriever_tool  # noqa: PLC0415

    return create_retriever_tool(
        get_retriever(),
        name="offbeat_knowledge",
        description=(
            "Recherche dans la documentation OFFBEAT/OpenFOAM (manuels, "
            "guides, articles). À utiliser pour toute question de fond sur la "
            "physique du combustible, les dictionnaires, les modèles ou "
            "l'utilisation du code. La documentation étant surtout en anglais, "
            "formuler la requête de recherche EN ANGLAIS pour de meilleurs "
            "résultats (même si la réponse finale à l'utilisateur est en français)."
        ),
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Ingestion RAG AutoOFFBEAT.")
    ap.add_argument("--ingest", action="store_true",
                    help="(Re)construire l'index FAISS depuis offbeat_skills/docs/.")
    args = ap.parse_args()
    if args.ingest:
        build_index()
    else:
        ap.print_help()
