# AutoOFFBEAT : explication complète du code

> **Couverture.** Ce document décrit l'agent de base : fabrique de modèles,
> `input_creator`, `offbeat_executor`, `data_processor`, `rag_retriever`,
> superviseur, interface et `run_sim.py`. Les outils du jumeau numérique
> (`safety_analyzer.py`, `surrogate.py`, `twin_monitor.py`) et les bancs
> d'essai d'`evaluation/` sont décrits dans `GUIDE.md` et, en détail, dans le
> rapport (`rapport/rapport_PRe_EN.pdf`, chapitres 3 et 4).

> Document pédagogique : chaque fichier et chaque fonction est expliqué en
> langage simple, sans supposer d'expérience en programmation. À lire de haut
> en bas la première fois.

---

## 0. Notions de base (à lire une fois)

Quelques mots de vocabulaire qui reviennent partout :

- **Fonction** : un petit bloc de code avec un nom, qui prend des *entrées*
  (les « arguments », entre parenthèses) et renvoie un *résultat*. Comme une
  recette : ingrédients → plat.
- **Classe** : un modèle qui regroupe des données et des fonctions liées. Les
  fonctions d'une classe s'appellent des **méthodes**. Par convention ici, une
  méthode dont le nom commence par `_` (ex. `_run`) est « interne ».
- **Variable d'environnement** (`.env`) : un réglage lu au démarrage
  (ex. quel modèle d'IA utiliser). On les change sans toucher au code.
- **`subprocess`** : la façon dont Python lance un *autre programme* (ici le
  solveur `offbeat` ou `blockMesh`) comme si on tapait la commande au terminal.
- **regex** (expression régulière) : un motif pour *chercher du texte*
  (ex. « trouve la ligne qui contient FOAM FATAL ERROR »).
- **LLM** : le modèle d'IA (ici qwen2.5:7b via Ollama) qui comprend le langage.
- **Agent / outils** : l'agent est le « cerveau » (le LLM) ; les *outils* sont
  les actions concrètes qu'il peut déclencher (créer un cas, lancer le solveur…).

**Le principe d'AutoOFFBEAT en une phrase** : tu écris une demande en français,
un agent IA choisit les bons *outils* pour créer un cas de simulation, lancer le
solveur OFFBEAT, se réparer en cas d'erreur, et te montrer les résultats.

---

## 1. `.env` et `.env.example` : les réglages

`.env.example` est un *modèle* (sans secret) ; tu le copies en `.env` et tu
remplis. Les réglages clés :

- `LLM_PROVIDER` : quel fournisseur d'IA (`ollama` = local, `gemini`, `anthropic`).
- `OLLAMA_MODEL` : le modèle local utilisé (`qwen2.5:7b`).
- `OFFBEAT_BIN` : où se trouve le programme solveur.
- `OFFBEAT_TIMEOUT_S` : temps max autorisé pour une simulation (garde-fou).
- `SELF_HEALING_MAX_RETRIES` : combien de fois réessayer après un crash (défaut 3).

`.env` ne doit **jamais** être partagé (il peut contenir des clés).

---

## 2. `requirements.txt` : la liste des bibliothèques

Simple liste des « boîtes à outils » Python à installer
(`pip install -r requirements.txt`) : l'interface (dash), l'IA (langchain,
langgraph, les connecteurs ollama/gemini/anthropic), le post-traitement
(pyvista, numpy, matplotlib) et le RAG (faiss-cpu, pypdf).

---

## 3. `config/llm_factory.py` : la « fabrique » d'IA

**Rôle** : fournir le bon modèle d'IA selon le `.env`, sans que le reste du code
ait à savoir lequel. Comme un adaptateur universel.

- **`get_llm(temperature, provider)`** : renvoie une instance du modèle d'IA.
  Regarde `LLM_PROVIDER` et construit le bon objet (`ChatOllama`,
  `ChatGoogleGenerativeAI` ou `ChatAnthropic`). `temperature=0` = réponses
  stables/déterministes. Si le fournisseur est inconnu, lève une erreur claire.
- **`get_supervisor_llm()`** : raccourci = le modèle du superviseur (le cerveau
  principal). Appelle `get_llm(temperature=0)`.
- **`get_debug_llm()`** : le modèle pour analyser les crashs. Peut être un
  fournisseur différent (`DEBUG_LLM_PROVIDER`), utile pour mettre un gros
  modèle sur le debug et un petit sur le reste.

---

## 4. `tools/input_creator.py` : l'outil qui CRÉE un cas de simulation

**Rôle** : fabriquer un dossier de cas OFFBEAT complet, en copiant un *template*
validé puis en remplaçant quelques paramètres demandés (puissance, géométrie…).

Fonctions d'aide (les « _ » = usage interne) :

- **`_patch_foam_entry(case, fichier, clé, valeur)`** : dans un dictionnaire
  OpenFOAM (syntaxe `clé  valeur;`), remplace l'ancienne valeur par la nouvelle.
  Utilise une regex. Renvoie `True` si ça a modifié quelque chose.
- **`_patch_foam_lhgr(case, valeur)`** : cas particulier pour la *puissance
  linéique* (`lhgr`), qui est une liste de valeurs dans le temps. Remplace les
  valeurs non nulles par la puissance voulue, en gardant le 0 initial (la rampe).
- **`_patch_roddict(case, clé, valeur)`** : `rodDict` (la géométrie) est en
  syntaxe **Python** (`'clé': valeur,`), pas OpenFOAM. Cette fonction sait
  modifier ce format-là (scalaires et listes).
- **`PARAM_MAP`** : un tableau de correspondance « nom simple → où écrire ».
  Ex. `linear_heat_rate` → puissance ; `fuel_outer_radius` → rayon dans rodDict.
  C'est ce qui traduit tes mots en modifications de fichiers.
- **`_apply_params(case, params)`** : parcourt les paramètres que tu as fournis,
  et applique chacun via la bonne fonction ci-dessus. Renvoie ce qui a été
  appliqué et ce qui a été ignoré (paramètre inconnu).

La classe **`OffbeatInputCreatorTool`** (l'outil vu par l'agent) :
- `name`, `description` : ce que l'agent lit pour savoir quand l'utiliser.
- `args_schema` : la liste des arguments attendus (chemin, template, paramètres).
- **`_run(case_dir, template_name, params)`** : la méthode principale. Elle :
  1. vérifie que le template existe,
  2. lit `params` (fourni en texte JSON),
  3. copie le template dans `case_dir`,
  4. applique les paramètres,
  5. renvoie un compte-rendu (ce qui a été créé/modifié).
  Elle **ne construit pas le maillage** : c'est le rôle de l'executor.

---

## 5. `tools/offbeat_executor.py` : l'outil qui LANCE le solveur (+ self-healing)

**Rôle** : mailler le cas, lancer `offbeat`, lire le log, et se réparer tout seul
en cas de crash. C'est le cœur « intelligent » du projet.

### Les correctifs (fonctions `fix_*`)
Chacune modifie un réglage pour tenter de corriger un type d'erreur :
- **`fix_reduce_timestep`** : divise le pas de temps `deltaT` par 10 (contre les
  divergences numériques).
- **`fix_relax_solver`** : assouplit les tolérances du solveur linéaire.
- **`fix_increase_outer_iterations`** : augmente le nombre d'itérations du
  couplage thermo-mécanique.
- **`fix_clean_restart`** : signale qu'il faut repartir proprement (le nettoyage
  réel est fait par la boucle, voir plus bas).
- **`fix_reduce_endtime`** : réduit `endTime` de 1 % pour rester sous le dernier
  point de l'historique de puissance (sinon OFFBEAT ne sait plus quoi faire).

Fonctions d'aide :
- **`_edit_dict_entry` / `_get_dict_entry`** : écrire / lire une valeur dans un
  dictionnaire OpenFOAM (par regex).
- **`_clean_timesteps(case)`** : efface les résultats déjà écrits (sauf `0/`)
  pour que le solveur reparte de zéro. **Important** : OFFBEAT ne sait pas
  reprendre un calcul à partir de ses propres résultats écrits, donc on nettoie
  avant chaque relance.

### La base de connaissance des erreurs
- **`FIX_REGISTRY`** : dictionnaire « nom de correctif → la vraie fonction ».
  Permet au fichier JSON de désigner un correctif par son *nom*.
- **`_FALLBACK_KB`** : mini-base de secours si le fichier JSON est absent/cassé
  (le self-healing ne doit jamais tomber en panne).
- **`_load_error_kb()`** : lit `offbeat_skills/error_kb.json`, et pour chaque
  entrée relie le motif d'erreur à sa fonction correctif. Renvoie la base prête.
- **`ERROR_KB`** : la base chargée au démarrage (résultat de la fonction ci-dessus).

### La classe `OffbeatExecutorTool`
- **`_build_mesh(case)`** : construit le maillage = lance `rodMaker.py`
  (géométrie → `blockMeshDict`) puis `blockMesh`. Renvoie un code (0 = OK) et le log.
- **`_run_solver(case)`** : lance le programme `offbeat` et enregistre sa sortie
  dans `log.offbeat`. Renvoie code + log.
- **`_diagnose(log)`** : cherche dans le log le *bloc d'erreur fatale* (ou une
  trace de crash « sigHandler »), puis compare ce bloc aux motifs de la base.
  Renvoie l'entrée qui correspond (ou rien). Astuce clé : on cherche **seulement
  dans le bloc d'erreur**, pour éviter les faux positifs (ex. un message anodin
  de démarrage).
- **`_rag_context(log)`** : interroge le RAG pour récupérer des passages de doc
  pertinents sur l'erreur. Robuste : renvoie rien si le RAG n'est pas prêt.
- **`_llm_fallback(log, case)`** : si l'erreur est inconnue, demande un
  diagnostic au LLM de debug, **enrichi** par la doc du RAG. Ne modifie rien
  automatiquement (c'est un avis).
- **`_run(case_dir, self_healing)`** : la méthode principale. Le déroulé :
  1. vérifie que le cas est valide,
  2. construit le maillage,
  3. lance le solveur ; si le mot de fin `end` est là → **succès**,
  4. sinon, diagnostique ; si erreur connue → applique le correctif, nettoie,
     relance (jusqu'à `MAX_RETRIES`) ; si inconnue → avis du LLM+RAG et on
     s'arrête. Renvoie un rapport texte de tout ce qui s'est passé.

---

## 6. `tools/data_processor.py` : l'outil qui LIT les résultats et fait les figures

**Rôle** : ouvrir les résultats de la simulation et en extraire des profils
(température, contrainte…), plus générer des images PNG.

- **`_load_foam(case, time_step)`** : ouvre le cas avec la bibliothèque
  `pyvista` (qui comprend le format OpenFOAM). Par défaut prend le dernier pas
  de temps calculé.
- **`_component(valeurs, i)`** : les contraintes sont des « tenseurs » (6
  nombres) ; cette fonction extrait la composante voulue (ex. la contrainte de
  cerclage).
- **`_valid_mask(échantillon)`** : quand on sonde une ligne dans le maillage,
  certains points tombent *hors* de la matière (gap, plénum) et renverraient un
  faux 0. Ce masque ne garde que les points *réellement* dans le maillage.
- **`_axial_profile(...)`** : échantillonne un champ le long de l'axe vertical du
  barreau (profil en hauteur). Renvoie une liste de positions et de valeurs.
- **`_radial_profile_at_midplane(...)`** : idem mais du centre vers l'extérieur,
  à mi-hauteur (profil radial).
- **`_peak_value(...)`** : la valeur maximale d'un champ sur tout le domaine
  (ex. température de gaine maximale « PCT »).
- **`_save_plot(x, y, ...)`** : trace une courbe et l'enregistre en PNG (mode
  « sans écran », donc marche même sur un serveur sans affichage).
- La classe **`OffbeatDataProcessorTool`** et sa méthode **`_run(...)`** :
  selon l'analyse demandée (`axial_T`, `radial_T`, `axial_stress`, `peak_T`, ou
  `summary` = tout), extrait les profils, enregistre les PNG dans
  `<cas>/figures/`, sauvegarde un JSON, et renvoie un résumé texte + la liste des
  figures.

---

## 7. `tools/rag_retriever.py` : l'assistant documentaire (RAG)

**Rôle** : permettre à l'agent de *chercher dans la documentation* OFFBEAT.
« RAG » = on récupère des passages pertinents et on les donne au LLM.

- **`_PrefixedEmbeddings`** : petit adaptateur qui ajoute les préfixes
  (`search_query:` / `search_document:`) exigés par le modèle `nomic-embed-text`
  pour bien fonctionner.
- **`_get_embeddings()`** : renvoie le « traducteur texte → vecteurs » (le modèle
  d'embeddings). Les vecteurs permettent de mesurer la similarité entre textes.
- **`_load_documents(dossier)`** : charge tous les fichiers `.md/.txt/.pdf` du
  dossier `offbeat_skills/docs/`.
- **`build_index(...)`** : découpe les documents en morceaux (« chunks »), les
  transforme en vecteurs, et les range dans une base **FAISS** sur le disque.
  C'est l'étape « ingestion » (à relancer quand on ajoute des documents).
- **`get_retriever(k)`** : ouvre la base FAISS et renvoie un « chercheur » qui,
  pour une question, ramène les `k` passages les plus proches.
- **`get_knowledge_tool()`** : emballe le chercheur en *outil* `offbeat_knowledge`
  que l'agent peut appeler.
- Le bloc `if __name__ == "__main__"` : permet de lancer l'ingestion au terminal
  avec `python -m tools.rag_retriever --ingest`.

---

## 8. `agents/supervisor.py` : le « chef d'orchestre »

**Rôle** : assembler le LLM + les 4 outils en un agent qui décide quoi faire.

- **`SYSTEM_PROMPT`** : le texte d'instructions permanent donné au LLM (« tu es
  AutoOFFBEAT, voici tes outils, voici les règles… »). C'est ce qui oriente son
  comportement.
- **`build_supervisor()`** :
  1. récupère le LLM (`get_supervisor_llm`),
  2. crée la liste des outils (input_creator, executor, data_processor),
  3. ajoute l'outil RAG **s'il est prêt** (sinon l'agent démarre quand même),
  4. appelle `create_agent(...)` avec une *mémoire* (`InMemorySaver`) pour que
     l'agent se souvienne de la conversation.
  Renvoie l'agent prêt à l'emploi.
- Le bloc `if __name__ == "__main__"` : un petit mode « chat au terminal » pour
  tester l'agent sans interface.

**Comment on parle à l'agent** : on lui envoie
`{"messages": [{"role":"user","content": "ta demande"}]}` avec un `thread_id`
(l'identifiant de la conversation), et il renvoie des messages ; le dernier est
sa réponse.

---

## 9. `app.py` : l'interface web (Dash)

**Rôle** : la page web de chat sur `http://localhost:8000`.

- En haut du fichier : création de l'agent (`build_supervisor()`) et d'un
  `SESSION_ID` (identifiant de conversation).
- **`_extract_figure_paths(texte)`** : cherche dans la réponse de l'agent les
  chemins de PNG qui existent réellement.
- **`_encode_image(chemin)`** : transforme un PNG en texte « base64 » pour
  l'afficher directement dans la page (sans serveur d'images).
- Les *callbacks* (fonctions déclenchées par une action de l'utilisateur) :
  - **`handle_send(...)`** : quand tu cliques « Envoyer », envoie ta demande à
    l'agent, récupère la réponse, repère les figures, et met à jour l'historique.
  - **`render_chat(historique)`** : dessine les bulles de conversation, et
    insère les images sous la réponse de l'agent.
  - **`clear_chat(...)`** : le bouton « Effacer » démarre une nouvelle
    conversation (nouvelle mémoire).
- Le bloc final `app.run(...)` : démarre le serveur web.

---

## 10. `run_sim.py` : lancer une simulation SANS l'IA

**Rôle** : un script en ligne de commande qui enchaîne les 3 outils directement,
sans passer par le LLM. Utile pour tester le « moteur » de façon fiable et rapide.

- **`main(...)`** : lit les options de la ligne de commande (`--case-dir`,
  `--lhgr`, `--end-time`, etc.), puis appelle successivement `input_creator`,
  `offbeat_executor`, `data_processor`, en affichant chaque étape. Des options
  (`--no-run`, `--no-post`) permettent de s'arrêter avant certaines étapes.

Exemple :
```bash
python run_sim.py --case-dir /tmp/demo --lhgr 20000 --end-time 3600
```

---

## 11. `offbeat_skills/` : le savoir-faire du projet

- **`templates/fuel_rod_1D_pwr/`** : un cas OFFBEAT de référence complet
  (géométrie `rodDict`, dictionnaires, conditions initiales). `input_creator`
  part de là.
- **`error_kb.json`** : la base des erreurs. Chaque entrée a : `id`, `pattern`
  (le motif texte à repérer dans le log), `diagnosis` (explication),
  `fix_function` (nom du correctif, ou `null` = diagnostic seul), et un champ
  `validated` (true = confirmé sur un vrai crash). **Éditable sans toucher au
  code.**
- **`docs/`** : la documentation que le RAG indexe (à enrichir avec les articles
  OFFBEAT).
- **`HOWTO_ajouter_une_erreur.md`** : la méthode pour documenter un nouveau crash.

---

## 12. `Dockerfile` : l'emballage reproductible

**Rôle** : construire une image contenant OpenFOAM + OFFBEAT + le code Python,
pour lancer AutoOFFBEAT n'importe où sans réinstaller. C'est pour la
distribution, pas pour le développement quotidien.

---

## 13. Le flux complet (comment tout s'enchaîne)

```
Tu écris une demande dans le navigateur (app.py)
        │
        ▼
Le superviseur (supervisor.py) = LLM (llm_factory.py) + mémoire
        │  choisit les outils selon la demande
        ├──► input_creator ─► crée le dossier de cas (depuis un template)
        ├──► offbeat_executor ─► maille + lance offbeat
        │        │ en cas de crash : _diagnose ↔ error_kb.json
        │        │   erreur connue  → correctif + relance (self-healing)
        │        │   erreur inconnue→ _llm_fallback enrichi par le RAG
        ├──► data_processor ─► profils + figures PNG
        └──► offbeat_knowledge (RAG) ─► répond aux questions de fond
        │
        ▼
La réponse (texte + figures) s'affiche dans le chat (app.py)
```

En résumé : **`app.py`** est la vitrine, **`supervisor.py`** le cerveau,
**`tools/`** les mains, **`config/`** l'adaptateur d'IA, et **`offbeat_skills/`**
la mémoire métier (templates, erreurs, docs).
