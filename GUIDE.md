# Guide – AutoOFFBEAT

Ce guide est en **deux parties** :

- **Partie 1 — Faire tourner l'agent de base** (ci-dessous) : du test le plus
  simple (chat seul, sans solveur) jusqu'au mode complet (OFFBEAT dans Docker).
- **[Partie 2 — Développer le jumeau numérique](#partie-2--développer-le-jumeau-numérique-de-centrale-nucléaire)** :
  passer de l'agent *réactif* actuel à un jumeau *prédictif* qui détecte les
  situations de danger et s'adapte aux données d'exploitation.

> ⚠️ La Partie 2 est le volet « jumeau numérique », **hors périmètre par défaut**
> dans [CLAUDE.md](CLAUDE.md) (§1, §8). Elle ne se construit qu'**une fois
> l'agent de base stable** (Partie 1) et **après validation du périmètre avec
> l'encadrant** (question ouverte n°1 du CLAUDE.md §9). C'est une feuille de
> route de développement, pas un mode déjà livré.

---

# Partie 1 — Faire tourner l'agent de base

---

## Vue d'ensemble : 3 niveaux de fonctionnement

| Niveau | Ce qui marche | Prérequis |
|--------|---------------|-----------|
| **A. Chat seul** | GUI + agent LLM, génération de cas (input_creator) | Python + une clé LLM (ou Ollama) |
| **B. Local + OFFBEAT** | + exécution réelle du solveur, self-healing | A + OpenFOAM/OFFBEAT installés sur la machine |
| **C. Docker** | Tout, empaqueté et reproductible | Docker uniquement |

Commence par **A** pour valider que l'agent répond, puis monte en niveau.

---

## Niveau A — Faire parler l'agent (sans solveur)

### 1. Aller dans le dossier

```bash
cd /home/ann/PRE/AutoOFFBEAT
```

### 2. Créer un environnement Python isolé

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Installer les dépendances

```bash
pip install -r requirements.txt
```

> Si tu n'utilises qu'un seul fournisseur LLM, tu peux retirer les deux
> autres lignes `langchain-*` de `requirements.txt` pour aller plus vite.

### 4. Configurer le `.env`

```bash
cp .env.example .env
```

Puis ouvre `.env` et choisis **un** fournisseur :

**Option 1 — Ollama (100 % local, gratuit, recommandé pour tester)**
```ini
LLM_PROVIDER=ollama
OLLAMA_MODEL=qwen2.5-coder:14b
OLLAMA_BASE_URL=http://localhost:11434
```
Il faut alors installer Ollama et télécharger le modèle :
```bash
# https://ollama.com/download
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen2.5-coder:14b
ollama serve   # laisse tourner dans un terminal séparé
```

**Option 2 — Gemini (tier gratuit, juste une clé)**
```ini
LLM_PROVIDER=gemini
GOOGLE_API_KEY=ta_cle_ici
GEMINI_MODEL=gemini-1.5-flash
```
Clé : https://aistudio.google.com/app/apikey

**Option 3 — Anthropic / Claude**
```ini
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=ta_cle_ici
ANTHROPIC_MODEL=claude-sonnet-4-5
```

### 5. Vérifier que le LLM se charge (test rapide hors GUI)

```bash
python -c "from config.llm_factory import get_supervisor_llm; print(get_supervisor_llm().invoke('dis bonjour').content)"
```
Tu dois voir une réponse du modèle. Si erreur → vérifie la clé / Ollama.

### 6. Lancer le GUI

```bash
python app.py
```
Ouvre ensuite **http://localhost:8000** dans le navigateur.

Teste avec :
> « Crée un cas de barreau combustible dans /tmp/rod_test avec une
> puissance linéique de 25000 W/m »

L'agent doit appeler `input_creator` et écrire les dictionnaires
OpenFOAM. Vérifie :
```bash
find /tmp/rod_test
```

> **Note :** au niveau A, l'outil `offbeat_executor` répondra qu'il ne
> trouve pas le binaire `offbeat` — c'est normal, passe au niveau B ou C
> pour l'exécution réelle.

---

## Niveau B — Exécuter réellement OFFBEAT (sans Docker)

À faire **seulement** si OpenFOAM + OFFBEAT sont installés localement
(Linux/WSL).

### 1. Installer OpenFOAM (openfoam.com, v2406)
Suivre https://www.openfoam.com/download. Puis sourcer à chaque session :
```bash
source /usr/lib/openfoam/openfoam2406/etc/bashrc
```

### 2. Compiler OFFBEAT
```bash
git clone https://gitlab.com/offbeat-solver/offbeat.git
cd offbeat
./Allwmake -j$(nproc)
which offbeat   # doit renvoyer un chemin
```

### 3. Pointer le `.env` vers le binaire
```ini
OFFBEAT_BIN=offbeat          # s'il est dans le PATH
# ou chemin absolu :
# OFFBEAT_BIN=/home/ann/offbeat/platforms/linux64GccDPInt32Opt/bin/offbeat
OFFBEAT_TIMEOUT_S=7200
SELF_HEALING_MAX_RETRIES=3
```

### 4. Relancer le GUI dans un terminal où OpenFOAM est sourcé
```bash
source /usr/lib/openfoam/openfoam2406/etc/bashrc
source .venv/bin/activate
python app.py
```

Maintenant l'agent peut enchaîner : créer le cas → mailler (`blockMesh`)
→ lancer `offbeat` → auto-réparer en cas de crash → post-traiter.

> ⚠️ `input_creator` ne génère pas encore le maillage. Avant la première
> exécution réelle, ajoute un `constant/polyMesh/` (via `blockMesh`) ou
> pars d'un template complet dans `offbeat_skills/templates/`.

---

## Niveau C — Tout dans Docker (reproductible)

### 1. Construire l'image
```bash
cd /home/ann/PRE/AutoOFFBEAT
docker build -t autooffbeat:latest .
```
> La compilation d'OFFBEAT prend plusieurs minutes. Si l'URL du dépôt
> OFFBEAT change, ajuste `ARG OFFBEAT_REPO` dans le `Dockerfile`.

### 2. Lancer le conteneur
```bash
docker run -d --name autooffbeat \
  -p 8050:8000 \
  --env-file .env \
  -v "$PWD/offbeat_skills:/autooffbeat/offbeat_skills" \
  -v "$PWD/AutoOFFBEAT_logs:/autooffbeat/AutoOFFBEAT_logs" \
  -v "/chemin/vers/tes/simulations:/host" \
  autooffbeat:latest
```

> Avec Ollama sur la machine hôte, ajoute
> `-e OLLAMA_BASE_URL=http://host.docker.internal:11434`
> (et `--add-host=host.docker.internal:host-gateway` sous Linux).

### 3. Vérifier et ouvrir
```bash
docker ps
docker logs -f autooffbeat
```
Ouvre **http://localhost:8050**.

### 4. Arrêter / supprimer
```bash
docker stop autooffbeat && docker rm autooffbeat
```

---

## Activer les outils input_creator et data_processor

Par défaut, seul `offbeat_executor` est branché dans le superviseur.
Pour activer les deux autres, édite
[agents/supervisor.py](agents/supervisor.py) :

```python
from tools.input_creator import OffbeatInputCreatorTool
from tools.data_processor import OffbeatDataProcessorTool

tools = [
    OffbeatExecutorTool(),
    OffbeatInputCreatorTool(),
    OffbeatDataProcessorTool(),
]
```

(Décommente aussi les imports en haut du fichier.)

---

## Dépannage rapide

| Symptôme | Cause probable | Solution |
|----------|----------------|----------|
| `KeyError: 'ANTHROPIC_API_KEY'` | clé absente du `.env` | remplir la clé du provider choisi |
| `LLM_PROVIDER inconnu` | faute de frappe | `anthropic` / `gemini` / `ollama` |
| Connexion refusée Ollama | `ollama serve` pas lancé | démarrer Ollama |
| `binaire 'offbeat' introuvable` | niveau A/B sans OFFBEAT | passer en B (compiler) ou C (Docker) |
| `pyvista n'est pas installé` | data_processor sans dépendance | `pip install pyvista` |
| GUI inaccessible | mauvais port | A/B → `:8000`, Docker → `:8050` |
| `system/controlDict introuvable` | cas incomplet | créer le cas avec input_creator d'abord |

---

## Récapitulatif des chemins

```
AutoOFFBEAT/
├── .env                ← à créer depuis .env.example
├── app.py              ← lancé avec : python app.py
├── config/llm_factory.py
├── agents/supervisor.py
├── tools/{input_creator,offbeat_executor,data_processor}.py
├── offbeat_skills/
│   ├── templates/      ← y déposer tes cas de référence complets
│   ├── examples/
│   └── error_kb.json
├── AutoOFFBEAT_logs/   ← logs runtime (autooffbeat.log)
├── requirements.txt
└── Dockerfile
```

---
---

# Partie 2 — Développer le jumeau numérique de centrale nucléaire

> Cette partie décrit comment faire **évoluer** AutoOFFBEAT de l'agent *réactif*
> actuel vers un jumeau numérique *prédictif*. Chaque étape (D1 → D5) se
> construit et se teste isolément, comme l'ordre de construction du
> [CLAUDE.md](CLAUDE.md) §4.

> **✅ État d'implémentation (crayon seul).** D1 à D5 sont **codés et testés**
> pour un crayon combustible :
> - **D1** [tools/safety_analyzer.py](tools/safety_analyzer.py) + [offbeat_skills/safety_kb.json](offbeat_skills/safety_kb.json) — statut 🟢/🟡/🔴, branché dans l'agent.
> - **D2** `prognose()` dans le même module — extrapolation du franchissement.
> - **D3** [tools/twin_monitor.py](tools/twin_monitor.py) — boucle d'assimilation (validée bout en bout).
> - **D4** panneau de sûreté live + **simulateur interactif à curseurs** dans
>   [app.py](app.py) (barre latérale, jauges 🟢/🟡/🔴 mises à jour en ~ms).
> - **D5** [tools/surrogate.py](tools/surrogate.py) — émulateur **2D** (puissance
>   × durée, 16 runs). Modèle par défaut = **processus gaussien** (kriging) :
>   validation leave-one-out **R²≈0.999** (MAE ~9 K sur T), **incertitude ±**
>   fournie, prédiction ~ms. Outil `surrogate_predict` branché + relié aux
>   curseurs de l'interface. (`--model-type poly` disponible en repli.)
>
> Restent hors « crayon seul » : la **montée en échelle assemblage** (D5 bis) et
> la **validation des seuils** `safety_kb.json` (tous `validated:false`) avec
> l'encadrant.

## 2.0 Cadrage : ce qu'est (et n'est pas) ce jumeau

Un « jumeau numérique de centrale » complet couplerait **neutronique**
(distribution de puissance dans le cœur) + **thermohydraulique** (caloporteur) +
**performance combustible** + le reste de l'îlot. OFFBEAT ne fait que la
**dernière couche** : la thermomécanique d'un crayon combustible.

**Donc ce qu'on construit ici, honnêtement, c'est le jumeau numérique du
*crayon combustible* (extensible à l'assemblage).** C'est la brique la plus
proche de la sûreté (fusion, rupture de gaine) et c'est celle qu'OFFBEAT permet.
Les couches neutronique/thermohydraulique restent des **conditions limites
d'entrée** (historique de puissance, pression et température caloporteur), pas
des choses qu'on résout.

Trois différences entre l'agent actuel et le jumeau visé :

| | Agent actuel | Jumeau numérique |
|---|---|---|
| **Quand** | après une simu terminée | pendant / à chaque nouvelle donnée |
| **Quoi** | crée, exécute, post-traite | + **surveille des seuils de sûreté** |
| **Comportement** | réactif (répare un crash déjà arrivé) | **prédictif** (annonce un danger *avant* qu'il arrive) |

> **La nuance « temps réel ».** OFFBEAT est un solveur *batch* (minutes à heures)
> et il n'existe pas de capteur branché en direct sur un crayon dans le cœur.
> Un « temps réel » à la seconde n'a donc pas de sens ici. Ce qui est réaliste :
> un jumeau **« à la mise à jour »** — dès qu'un nouvel historique
> d'exploitation arrive, il re-simule et met à jour son pronostic. Le vrai temps
> réel viendrait d'un **modèle réduit (surrogate)** entraîné sur OFFBEAT (voir
> étape D5).

## 2.1 Architecture cible (organigramme)

```
   Données d'exploitation                     ┌───────────────────────────┐
   (historique de puissance,         ┌──────► │  Superviseur LLM (agent)  │
    P/T caloporteur, mesures)        │        │  orchestre + EXPLIQUE     │
            │                        │        └────────────┬──────────────┘
            ▼                        │                     │ pilote
   ┌──────────────────┐   met à jour │      ┌──────────────┴───────────────┐
   │ D3 Assimilation  │──────────────┘      ▼              ▼               ▼
   │  de données      │        ┌─────────────────┐ ┌──────────────┐ ┌────────────┐
   └────────┬─────────┘        │ input_creator   │ │ offbeat_     │ │ data_      │
            │ ré-écrit le cas  │ (BC, puissance) │ │ executor     │ │ processor  │
            ▼                  └─────────────────┘ │ (+self-heal) │ └─────┬──────┘
   ┌──────────────────┐                            └──────────────┘       │ champs
   │  Cas OFFBEAT     │◄───────────────────────────────────────────────────┘
   │  (ou surrogate)  │                                              (T, σ, gap…)
   └────────┬─────────┘                                                    │
            ▼                                                              ▼
   ┌──────────────────┐   franchit un seuil ?   ┌──────────────────────────────┐
   │ D1 Analyseur de  │◄────────────────────────│ safety_kb.json (seuils        │
   │  sûreté (🟢🟡🔴) │                          │ éditables, comme error_kb)    │
   └────────┬─────────┘                          └──────────────────────────────┘
            ▼
   ┌──────────────────┐  extrapole la tendance   →  « limite atteinte dans ~X »
   │ D2 Pronostic     │
   └────────┬─────────┘
            ▼
   ┌──────────────────┐
   │ D4 Tableau de    │  jauges live, feux tricolores, alertes
   │  bord (app.py)   │
   └──────────────────┘
```

Le principe directeur : **on réutilise tout l'existant** (les 3 outils + le
patron *déterministe-puis-LLM* du self-healing + le KB JSON éditable sans
rebuild) et on ajoute 3 briques neuves (D1, D2, D3) + une extension GUI (D4).

## 2.2 Les critères de sûreté — le cœur métier

C'est le contenu physique du jumeau. Ce sont des **critères de conception
publiés** (pas des correctifs inventés — cf. CLAUDE.md §6), mais leurs valeurs
exactes dépendent du crayon et du burnup : à **confirmer avec l'encadrant / la
littérature**, d'où `validated: false` par défaut.

| Critère de sûreté | Limite indicative | Champ OFFBEAT | Calculé par OFFBEAT ? |
|---|---|---|---|
| Fusion combustible (centre pastille) | T < ~3113 K (UO₂ frais ; **baisse** avec le burnup) | `T` (max) | ✅ oui |
| Température de gaine (PCT, critère LOCA) | < 1477 K (1204 °C, 10 CFR 50.46) | `T` dans la gaine | ✅ si transitoire modélisé |
| Déformation de gaine (PCMI) | déformation circonf. plastique < ~1 % | `epsilon`/`sigmaCyl` (θθ) | ✅ oui |
| Fermeture du gap (début du PCMI) | garder une marge > 0 | largeur de gap | ✅ (géométrie) |
| Pression interne (critère *no-liftoff*) | P_interne < P_caloporteur (~15,5 MPa PWR) | plénum / `gapGas` | ✅ si modèle plénum+FGR |
| Oxydation de gaine (ECR, LOCA) | ECR < 17 % | — | ⚠️ selon le modèle activé |
| DNBR (crise d'ébullition) | > 1,3 | — | ❌ **non** — thermohydraulique, c'est une **entrée** |

> Les deux dernières lignes montrent la frontière : le DNBR relève de la couche
> thermohydraulique *en amont* d'OFFBEAT. Le jumeau le **reçoit** comme donnée,
> il ne le calcule pas. Bien le dire dans le rapport = montre la maturité du
> périmètre.

## 2.3 Ordre de construction (D1 → D5, un morceau à la fois)

### Étape D1 — Analyseur de sûreté (la première brique, la plus rentable)

**But :** lire une simu terminée et dire, critère par critère, 🟢 sûr / 🟡
proche du seuil / 🔴 franchi. Réutilise `data_processor` et **calque exactement**
le patron du self-healing (KB JSON déterministe d'abord, LLM en repli).

**Fichier neuf n°1 — `offbeat_skills/safety_kb.json`** (éditable SANS rebuild,
monté en volume comme `error_kb.json`) :

```json
[
  {
    "id": "fuel_centerline_melt",
    "validated": false,
    "field": "T",
    "component": null,
    "reduction": "max",
    "limit": 3113.0,
    "unit": "K",
    "direction": "below",
    "warning_fraction": 0.90,
    "criterion": "Température à cœur pastille sous la fusion UO2.",
    "diagnosis": "Marge à la fusion du combustible.",
    "note": "3113 K = fusion UO2 fraiche ; la limite BAISSE avec le burnup. A valider avec l'encadrant/litterature.",
    "references": ["IAEA fuel design criteria (a confirmer)"]
  },
  {
    "id": "cladding_pct_loca",
    "validated": false,
    "field": "T", "component": null, "reduction": "max",
    "limit": 1477.0, "unit": "K", "direction": "below", "warning_fraction": 0.90,
    "criterion": "Temperature de gaine sous 1204 C (10 CFR 50.46).",
    "diagnosis": "Marge PCT (critere LOCA).",
    "note": "S'applique en transitoire LOCA ; en fonctionnement nominal la gaine est bien plus froide.",
    "references": ["10 CFR 50.46 (a confirmer)"]
  }
]
```

**Fichier neuf n°2 — `tools/safety_analyzer.py`** (squelette, mêmes conventions
que les autres outils : `BaseTool` + `args_schema` Pydantic) :

```python
"""safety_analyzer.py – Analyse de sûreté d'un cas OFFBEAT terminé.
Compare les pics des champs (T, contraintes, gap) aux seuils de
offbeat_skills/safety_kb.json et attribue un statut 🟢/🟡/🔴 par critère.
Patron identique au self-healing : déterministe (KB) d'abord, LLM en repli."""

import json
from pathlib import Path
from typing import Type
from pydantic import BaseModel, Field
from langchain_core.tools import BaseTool

from tools.data_processor import _load_foam, _peak_value  # helpers réutilisés

SAFETY_KB_PATH = Path(__file__).parent.parent / "offbeat_skills" / "safety_kb.json"


def _load_safety_kb() -> list:
    try:
        return json.loads(SAFETY_KB_PATH.read_text(encoding="utf-8"))
    except Exception:            # jamais crasher : KB absente => aucune règle
        return []


def _evaluate(dataset, rule: dict) -> dict:
    """Calcule la valeur du champ et la compare au seuil de la règle."""
    value = _peak_value(dataset, rule["field"], component=rule.get("component"),
                        use_abs=True)
    if value is None:
        return {"id": rule["id"], "status": "N/A", "value": None}
    limit = float(rule["limit"])
    frac = float(rule.get("warning_fraction", 0.9))
    # 'below' : on doit RESTER sous la limite (fusion, PCT…)
    ratio = value / limit if rule.get("direction", "below") == "below" \
        else limit / value
    status = "🔴" if ratio >= 1.0 else ("🟡" if ratio >= frac else "🟢")
    return {"id": rule["id"], "status": status, "value": value,
            "limit": limit, "ratio": round(ratio, 3),
            "criterion": rule.get("criterion", "")}


class SafetyAnalyzerInput(BaseModel):
    case_dir: str = Field(description="Répertoire du cas OFFBEAT terminé.")
    time_step: str = Field(default="latestTime")


class OffbeatSafetyAnalyzerTool(BaseTool):
    name: str = "safety_analyzer"
    description: str = ("Évalue les marges de sûreté d'un cas OFFBEAT terminé "
                        "(fusion combustible, PCT, PCMI, gap, pression interne) "
                        "et retourne un statut 🟢/🟡/🔴 par critère.")
    args_schema: Type[BaseModel] = SafetyAnalyzerInput

    def _run(self, case_dir: str, time_step: str = "latestTime") -> str:
        try:
            dataset = _load_foam(Path(case_dir), time_step)
        except Exception as exc:
            return f"ERREUR lecture cas : {exc}"
        results = [_evaluate(dataset, r) for r in _load_safety_kb()]
        if not results:
            return "Aucune règle dans safety_kb.json."
        lines = [f"{r['status']} {r['id']}: valeur={r['value']} / "
                 f"limite={r.get('limit')} (ratio {r.get('ratio')})"
                 for r in results]
        # Repli LLM : si un 🔴, on peut demander une interprétation (get_debug_llm)
        return "\n".join(lines)
```

**Test minimal D1 :** lancer sur ton run long déjà post-traité :
```bash
python -c "from tools.safety_analyzer import OffbeatSafetyAnalyzerTool as T; \
print(T()._run('/chemin/vers/ton/cas'))"
```
Tu dois voir une ligne 🟢/🟡/🔴 par critère. **Ne pas passer à D2 tant que ça ne
tourne pas.** Ensuite, brancher l'outil dans `agents/supervisor.py` (l'ajouter à
la liste `tools`, comme les autres).

### Étape D2 — Pronostic (la « prédiction »)

**But :** ne pas seulement dire « on est à 85 % de la limite », mais **« au
rythme actuel, la limite sera atteinte dans ~X »**. On exploite le fait qu'un run
OFFBEAT contient **plusieurs pas de temps** : on extrait le pic d'un champ à
chaque pas, on ajuste une tendance, on extrapole le franchissement.

```python
import numpy as np
from tools.data_processor import _load_foam, _peak_value

def prognose(case_dir, field="T", limit=3113.0):
    """Extrapole quand `field` (pic) atteindra `limit`, d'après sa tendance."""
    import pyvista as pv
    foam = next(Path(case_dir).glob("*.foam"))
    reader = pv.OpenFOAMReader(str(foam))
    times, peaks = [], []
    for t in reader.time_values:
        reader.set_active_time_value(t)
        peaks.append(_peak_value(reader.read(), field))
        times.append(t)
    # ajustement linéaire pic = a·t + b ; résoudre a·t*+b = limit
    a, b = np.polyfit(times, peaks, 1)
    if a <= 0:
        return "Tendance non croissante : pas de franchissement prévu."
    t_cross = (limit - b) / a
    return f"{field} atteindrait {limit} vers t = {t_cross:.3g} s."
```

**Test minimal D2 :** sur un run où T monte, vérifier que `t_cross` est cohérent
(au-delà du dernier pas de temps). Affiner ensuite (tendance non linéaire, barre
d'incertitude) — mais garder le squelette simple d'abord.

### Étape D3 — Assimilation de données (l'« adaptation »)

**But :** quand de nouvelles données d'exploitation arrivent (un historique de
puissance mis à jour, une nouvelle pression caloporteur), le jumeau **ré-écrit le
cas et re-simule**, puis relance D1/D2. C'est le vrai « s'adapter aux données ».

On réutilise **`input_creator`** pour patcher les entrées (il sait déjà éditer
`lhgr`/puissance et les conditions limites), puis **`offbeat_executor`** pour
relancer, puis **`safety_analyzer`**. Squelette d'un `tools/twin_monitor.py` qui
surveille un fichier de données et boucle :

```python
"""twin_monitor.py – boucle d'assimilation 'a la mise a jour'.
Surveille un CSV d'exploitation ; a chaque changement : patch du cas,
re-simulation, ré-analyse de sûreté. Ce N'EST PAS du temps réel a la
seconde (OFFBEAT est batch) mais un jumeau qui se met a jour a chaque donnée."""

import time, hashlib
from pathlib import Path
from tools.input_creator import OffbeatInputCreatorTool
from tools.offbeat_executor import OffbeatExecutorTool
from tools.safety_analyzer import OffbeatSafetyAnalyzerTool

def watch(case_dir, data_csv, poll_s=30):
    last = None
    creator, runner, safety = (OffbeatInputCreatorTool(),
                               OffbeatExecutorTool(),
                               OffbeatSafetyAnalyzerTool())
    while True:
        digest = hashlib.md5(Path(data_csv).read_bytes()).hexdigest()
        if digest != last:                      # nouvelle donnée détectée
            last = digest
            # 1) traduire le CSV en params (puissance, P/T caloporteur…)
            params = _csv_to_params(data_csv)   # à écrire selon ton format
            creator._run(case_dir=case_dir, params=params)
            # 2) re-simuler (self-healing inclus)
            runner._run(case_dir=case_dir)
            # 3) ré-évaluer la sûreté + pronostic
            print(safety._run(case_dir=case_dir))
        time.sleep(poll_s)
```

**Test minimal D3 :** modifier à la main le CSV et vérifier que la boucle
détecte le changement, relance, et ré-affiche les statuts. Commencer par un
`poll_s` large (pas besoin de réactivité, la simu domine le temps).

> ⚠️ Garde-fous à reprendre du CLAUDE.md §7 : `timeout` sur le solveur,
> limite de relances, et **ne pas empiler** les simulations (attendre la fin
> d'un run avant d'en lancer un autre).

### Étape D4 — Tableau de bord live (seulement à la fin)

Étendre `app.py` (déjà en Dash) avec un panneau d'état : des **jauges** par
critère (couleur 🟢/🟡/🔴), la courbe de marge dans le temps, et le pronostic D2.
Un `dcc.Interval` qui rafraîchit le panneau en appelant `safety_analyzer` sur le
cas courant suffit. **Ne pas commencer par là** (CLAUDE.md §4, §8 : l'interface
en dernier).

### Étape D5 — Montée en échelle & vrai temps réel

- **Crayon → assemblage.** Passer d'un crayon 1D à un assemblage demande un
  maillage plus lourd et beaucoup de calcul → c'est là qu'un **GPU / HPC**
  devient utile (l'argument vitesse déjà identifié, [[autooffbeat-env-and-latency]]).
- **Surrogate (le vrai temps réel).** Entraîner un **modèle réduit** (émulateur
  ML : surface de réponse / réseau) sur un jeu de runs OFFBEAT. Le surrogate
  répond en millisecondes → il permet un jumeau *réellement* temps réel, OFFBEAT
  servant de « vérité terrain » périodique. C'est une belle piste de recherche
  pour le rapport, mais **après** que D1–D4 tournent.

## 2.4 Contraintes techniques (à dire honnêtement dans le rapport)

- **Batch, pas temps réel** : OFFBEAT met des minutes à des heures → jumeau « à
  la mise à jour », le temps réel exige un surrogate (D5).
- **Périmètre = crayon combustible**, pas la centrale entière : neutronique et
  thermohydraulique sont des **entrées**, pas des couches résolues.
- **Coût de calcul** : la montée en échelle (assemblage/cœur) impose GPU/HPC.
- **Données d'exploitation** : il faut une **source** (mesures réelles ? données
  synthétiques ? sortie d'un code amont ?) — à clarifier (voir §2.5).

## 2.5 À valider avec l'encadrant avant de coder la Partie 2

1. **Périmètre géométrique** : crayon seul (démontrable vite) vs assemblage vs
   cœur (recherche lourde) ?
2. **Source des « données temps réel »** : historique de puissance mesuré,
   données synthétiques, ou sortie d'un simulateur amont ?
3. **Nature du livrable** : une démo de *détection de danger* sur un run (D1+D2),
   ou un système d'assimilation *continu* (D3+D4) ?
4. **Rappel de la question ouverte n°1** (CLAUDE.md §9) : le jumeau est-il le
   livrable final, ou alimente-t-il les objectifs monitoring/prédiction du stage ?

## 2.6 Ce que la Partie 2 apporte au rapport

- **Contribution scientifique nette** : une couche d'**analyse de sûreté +
  pronostic** au-dessus d'un code de performance combustible, pilotée par LLM.
  On passe d'« automatiser OFFBEAT » à « **anticiper la défaillance du
  combustible** ».
- **Résultats montrables** : courbes de marge (fusion, PCMI) dans le temps,
  instant prédit de franchissement, réaction du jumeau à un changement de
  puissance.
- **L'organigramme du §2.1** sert directement de figure d'architecture (le PDF
  ENSTA recommande un organigramme plutôt que du code en annexe).
- **La discussion des limites** (§2.4 : batch vs temps réel, surrogate,
  périmètre crayon) démontre la maturité scientifique attendue.

### Récapitulatif des fichiers neufs de la Partie 2

```
AutoOFFBEAT/
├── offbeat_skills/
│   ├── safety_kb.json          ← D1 : seuils de sûreté (éditable sans rebuild)
│   └── .surrogate/             ← D5 : dataset.csv + model.joblib (générés)
├── tools/
│   ├── safety_analyzer.py      ← D1 : analyze() 🟢/🟡/🔴 + D2 prognose() + repli LLM
│   ├── twin_monitor.py         ← D3 : boucle d'assimilation « à la mise à jour »
│   └── surrogate.py            ← D5 : émulateur temps réel (build/train/predict + outil)
├── agents/supervisor.py        ← safety_analyzer + surrogate_predict branchés
└── app.py                      ← D4 : barre latérale de jauges de sûreté (live)
```

Commandes utiles :
```bash
# D1/D2 — analyser la sûreté d'un cas simulé
python -m tools.safety_analyzer /chemin/vers/cas

# D3 — un cycle d'assimilation à partir d'un CSV d'exploitation
python -m tools.twin_monitor --case-dir /chemin/cas --data-csv ops.csv --once

# D5 — dataset 2D (grille puissance × durée), entraînement, prédiction instantanée
python -m tools.surrogate build --lhgr 12000 20000 28000 36000 --end-time 2000 3500 5000 6500
python -m tools.surrogate train
python -m tools.surrogate predict --lhgr 28000 --end-time 4000
```

Le **simulateur interactif** (curseurs puissance + durée → jauges 🟢/🟡/🔴 en
~ms) est dans la barre latérale de l'interface : `python app.py` → onglet de
droite. Il n'utilise ni le solveur ni le LLM, donc il est instantané.
