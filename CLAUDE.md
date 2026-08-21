# CLAUDE.md : AutoOFFBEAT (agent OFFBEAT de base)

> Fichier d'instructions projet pour un assistant de code (Claude Code / agent).
>
> **Périmètre : mis à jour.** Ce fichier décrivait initialement le seul agent de
> base, les volets « jumeau numérique » étant explicitement exclus. Ils ont été
> ouverts par la suite et sont désormais implémentés (§1). L'ordre de
> construction du §4 reste la trace de la progression suivie, et les
> avertissements du §8 valent toujours, à l'exception de celui sur le jumeau
> numérique.

---

## 1. Contexte du projet

AutoOFFBEAT est un agent LLM qui automatise les workflows du code de performance
combustible **OFFBEAT** (un solveur basé sur OpenFOAM). C'est un portage conceptuel
du projet open-source **AutoFLUKA** (SmartLabNuclear/AutoFLUKA) : on conserve son
architecture (superviseur LangChain + outils de domaine + boucle de self-healing)
en remplaçant les briques FLUKA par des briques OFFBEAT.

Objectif du stage selon l'encadrant : *« build an agent on nuclear fuel
performance code OFFBEAT »*. L'agent de base est le tronc commun et la priorité.

### Ce que fait l'agent de base (3 outils)
1. **Input Creator** : génère/modifie un cas OpenFOAM/OFFBEAT (répertoires
   `0/`, `constant/`, `system/`) depuis un template ou de zéro.
2. **Executor** : lance le solveur `offbeat` via `subprocess`, lit `log.offbeat`,
   et applique une boucle de **self-healing** en cas de crash.
3. **Data Processor** : post-traite les résultats via `pyvista`.

Le superviseur LangChain oriente ces outils selon la demande de l'utilisateur.

### Extensions ajoutées ensuite (jumeau numérique + évaluation)
4. **Safety Analyzer** (`tools/safety_analyzer.py`) : évalue les critères de
   sûreté de `offbeat_skills/safety_kb.json`, par zone de maillage.
5. **Surrogate** (`tools/surrogate.py`) : émulateur par processus gaussien
   (noyau de Matérn), prédiction + incertitude.
6. **Twin Monitor** (`tools/twin_monitor.py`) : surveillance, pronostic de
   franchissement de seuil, tableau de bord.
7. **RAG Retriever** (`tools/rag_retriever.py`) : assistant documentaire.
   L'embedding DOIT être multilingue : le corpus est bilingue.

`evaluation/` contient trois bancs d'essai réexécutables (auto-réparation,
sélection d'outils, qualité de la recherche documentaire) avec leurs résultats.

---

## 2. Stack technique (à respecter)

- **Python** 3.11+
- **LangChain 1.x** (pas la 0.3.x). Utiliser `create_agent`, PAS l'ancien couple
  `create_tool_calling_agent` + `AgentExecutor` (parti dans `langchain-classic`).
- **LLM agnostique** : le code ne doit jamais coder en dur un fournisseur. Tout
  passe par une fabrique pilotée par `.env` (`LLM_PROVIDER` = `anthropic` |
  `gemini` | `ollama`). Objectif : pouvoir tourner 100 % gratuit en local (Ollama).
- **OFFBEAT / OpenFOAM** : codes Linux natifs. Le binaire du solveur est désigné
  par la variable d'env `OFFBEAT_BIN`.
- **pyvista** pour le post-traitement (lecture VTK / `.foam`).
- **Interface** : à terme Dash ou Streamlit (comme AutoFLUKA). NE PAS commencer
  par l'interface, la garder pour la fin.

### Environnement de développement
- Développer **sous WSL2** (Ubuntu), pas Windows natif : OFFBEAT/OpenFOAM se
  compilent sous Linux, et les I/O de cas OpenFOAM sont dramatiquement lentes
  sur le système de fichiers Windows monté.
- Garder le projet ET les cas de simulation dans le home WSL (`~/...`), jamais
  sous `/mnt/c/...`.
- Travailler dans un `venv` Python. **Docker n'est PAS requis pour développer**,
  il viendra plus tard, seulement pour empaqueter un livrable reproductible.

---

## 3. Arborescence cible

```
AutoOFFBEAT/
├── .env                       # NE JAMAIS committer (mettre dans .gitignore)
├── .env.example               # template de clés, sans valeurs
├── requirements.txt
├── config/
│   ├── __init__.py
│   └── llm_factory.py         # fabrique LLM agnostique (déjà écrite)
├── agents/
│   ├── __init__.py
│   └── supervisor.py          # superviseur LangChain (à migrer en create_agent)
├── tools/
│   ├── __init__.py
│   ├── input_creator.py       # outil 1
│   ├── offbeat_executor.py    # outil 2 (déjà écrit)
│   └── data_processor.py      # outil 3
├── offbeat_skills/
│   ├── templates/             # cas OFFBEAT de référence
│   ├── examples/              # paires demande → cas généré
│   └── error_kb.json          # base de connaissance des erreurs (self-healing)
└── AutoOFFBEAT_logs/
```

Règle d'imports : toujours lancer depuis la racine (`python -m agents.supervisor`).
Chaque dossier de package a un `__init__.py`.

---

## 4. Ordre de construction (NE PAS dévier)

Construire **un seul morceau à la fois**, en le testant avant de passer au suivant.
Le piège à éviter absolument : tout construire en parallèle.

- [ ] **Étape 0 : Setup.** Créer l'arborescence, le `venv`, `requirements.txt`,
      les `__init__.py`, le `.env` depuis `.env.example`, et `.gitignore`
      (incluant `.env`).
- [ ] **Étape 1 : Executor seul.** Faire fonctionner `offbeat_executor.py` sur un
      vrai cas OFFBEAT, SANS superviseur ni interface. Un script de test qui
      instancie l'outil et appelle `_run("/chemin/cas")`. Vérifier que
      `subprocess` lance le solveur et que `log.offbeat` est bien parsé.
- [ ] **Étape 2 : Self-healing.** Provoquer un crash connu (ex. `deltaT` trop
      grand → dépassement du nombre de Courant) et vérifier que la boucle détecte,
      corrige le `controlDict`, et relance. Alimenter `error_kb.json` (voir §6).
- [ ] **Étape 3 : Superviseur.** Brancher `supervisor.py` (LangChain 1.x,
      `create_agent`) avec UN SEUL outil (l'executor). Tester en CLI.
- [ ] **Étape 4 : Input Creator.** Ajouter l'outil de génération de cas.
- [ ] **Étape 5 : Data Processor.** Ajouter le post-traitement pyvista.
- [ ] **Étape 6 : Interface.** Dash/Streamlit, seulement une fois le reste stable.

À chaque étape : ne pas avancer tant que l'étape courante ne tourne pas et n'a
pas de test minimal.

---

## 5. Conventions de code

- Tous les outils sont des `BaseTool` LangChain avec un `args_schema` Pydantic.
  (Note : le passage à `create_agent` en LangChain 1.x change l'état de l'agent,
  pas le schéma des outils ; les `BaseModel` d'`args_schema` restent valides.)
- La boucle de self-healing ne doit JAMAIS crasher elle-même : tout appel LLM ou
  édition de fichier dans cette boucle est dans un `try/except`.
- Éviter les dépendances lourdes inutiles (ex. pas besoin de PyFoam : l'édition
  des dictionnaires OpenFOAM se fait par regex pour rester léger en conteneur).
- Le code doit rester modulaire et importable indépendamment de l'interface.
- Docstrings en clair sur chaque outil : elles servent de description à l'agent.

---

## 6. Self-healing : base de connaissance des erreurs

Le cœur de la valeur de l'agent. Stratégie à deux étages, fidèle aux FLUKA Skills :
1. **Déterministe d'abord** : `error_kb.json` associe un motif (regex dans le log)
   à un diagnostic et à un correctif scripté. Rapide, gratuit, fiable.
2. **Repli LLM** : seulement si l'erreur est inconnue de la base, envoyer la fin
   du log au LLM de debug pour diagnostic (ne pas appliquer automatiquement).

`error_kb.json` doit être un volume éditable SANS rebuild (comme `fluka_skills`
chez AutoFLUKA). Format suggéré par entrée : `pattern` (regex), `diagnosis`
(texte), `fix` (identifiant d'une fonction de correction).

Erreurs OpenFOAM génériques déjà couvertes : nombre de Courant dépassé,
`Floating point exception`/`sigFpe`, non-convergence du solveur linéaire,
non-convergence des outer correctors.

> **À COMPLÉTER PAR L'UTILISATEUR (BLOQUANT pour la qualité du self-healing) :**
> les erreurs spécifiques à la *physique combustible* d'OFFBEAT que l'agent doit
> savoir réparer (ex. fermeture du gap pellet-gaine, gonflement/densification à
> haut burnup, divergence du contact mécanique). Ces erreurs se découvrent en
> lançant des cas à la main. Ne pas inventer de correctifs physiques : demander
> à l'utilisateur ses cas de crash réels avant de remplir ces entrées.

---

## 7. Sécurité et garde-fous

- Le solveur peut tourner longtemps : prévoir un `timeout` (`OFFBEAT_TIMEOUT_S`)
  sur le `subprocess`.
- Limiter le nombre de relances du self-healing (`SELF_HEALING_MAX_RETRIES`,
  défaut 3) pour éviter les boucles infinies.
- Garde-fou sur les itérations de l'agent (`max_iterations`) côté superviseur.
- Ne jamais committer `.env` ni aucune clé API.
- Valider qu'un répertoire est bien un cas OpenFOAM (`system/controlDict` présent)
  avant de lancer le solveur.

---

## 8. Ce qu'il NE faut PAS faire

- Ne pas coder en dur un fournisseur LLM ni une clé.
- Ne pas commencer par l'interface graphique.
- Ne pas utiliser LangChain 0.3.x ni les API parties dans `langchain-classic`.
- Ne pas inventer de correctifs pour la physique combustible sans données réelles
  de l'utilisateur.
- Ne pas tout construire d'un coup : respecter l'ordre du §4.

---

## 9. Questions ouvertes (à clarifier avec l'encadrant)

- L'agent OFFBEAT est-il le livrable final, ou doit-il alimenter les volets
  monitoring/prédiction des objectifs écrits du stage ?
- Existe-t-il un jeu de cas OFFBEAT de référence pour mesurer le succès ?
- Quel niveau d'autonomie attendu : assistant qui suggère, ou exécution de bout
  en bout sans humain ?
