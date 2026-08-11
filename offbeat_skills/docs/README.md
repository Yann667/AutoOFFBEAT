# Corpus documentaire RAG

Dépose ici les documents que l'assistant `offbeat_knowledge` doit pouvoir
consulter. Formats supportés : **`.md`, `.txt`, `.pdf`** (récursif).

## À ajouter (recommandé)
- Article OFFBEAT (Scolaro et al., *Nuclear Engineering and Design*).
- OFFBEAT theory / user manual (si disponible).
- OpenFOAM User Guide (chapitres pertinents).
- Article SCIANTIX (Pizzocri et al.) pour le relâchement des gaz de fission.
- Toute note interne sur les modèles matériaux, dictionnaires, etc.

## Amorce déjà présente
- `offbeat_case_structure.md` — structure d'un cas OFFBEAT.
- `offbeat_commands.md` — commandes d'exécution.
- `HOWTO_ajouter_une_erreur.md` — méthode self-healing.

## (Ré)indexer après ajout
Depuis la racine du projet, l'environnement OpenFOAM **non requis** :
```bash
source .venv/bin/activate
ollama pull nomic-embed-text        # une seule fois (modèle d'embeddings)
python -m tools.rag_retriever --ingest
```
L'index FAISS est écrit dans `offbeat_skills/.rag_index/` (non versionné).

> Astuce : relance l'ingestion à chaque fois que tu ajoutes/modifies des
> documents. L'index n'est pas mis à jour automatiquement.
