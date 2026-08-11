# Guide : provoquer un crash et l'ajouter au self-healing

Ce guide explique, pas à pas, comment **déclencher un crash OFFBEAT**, lire son
log, et **ajouter une entrée** dans la base de connaissance `error_kb.json` pour
que le self-healing sache le diagnostiquer (et éventuellement le réparer).

> Principe directeur (CLAUDE.md §6) : on n'invente JAMAIS un motif ou un
> correctif. On déclenche un vrai crash, on lit le **vrai** message, et on ancre
> l'entrée dessus.

---

## Prérequis

À faire une fois par terminal :
```bash
cd /home/ann/PRE/AutoOFFBEAT
source /usr/lib/openfoam/openfoam2506/etc/bashrc   # blockMesh / offbeat dans le PATH
source .venv/bin/activate
```

Architecture du self-healing (pour comprendre où on agit) :
- **`offbeat_skills/error_kb.json`** : la base ÉDITABLE (motif → diagnostic → nom du correctif). C'est ici qu'on ajoute des entrées.
- **`tools/offbeat_executor.py`** : contient les **fonctions** de correctif et le registre `FIX_REGISTRY`. On n'y touche QUE pour ajouter une nouvelle *sorte* de correctif.

---

## Étape 1 — Partir d'un cas qui marche

```bash
rm -rf /tmp/crash_demo
python run_sim.py --case-dir /tmp/crash_demo --no-run
```
`--no-run` crée le cas sans lancer le solveur. On va le casser volontairement.

---

## Étape 2 — Provoquer un crash (recettes)

Une modification = un type de crash. Exemples :

| Crash visé | Recette |
|------------|---------|
| **Divergence / SIGFPE** | puissance absurde : `--lhgr 5000000` à la création |
| **Maillage absent** | lancer `offbeat` sans `blockMesh` |
| **Dictionnaire manquant** | `mv constant/solverDict constant/solverDict.bak` |
| **Condition limite manquante** | supprimer un patch dans `0/T` |
| **Solveur linéaire** | dans `system/fvSolution` : `maxIter 1;` + `tolerance 1e-12;` |

**Exemple concret (divergence, validé)** :
```bash
rm -rf /tmp/crash_demo
python run_sim.py --case-dir /tmp/crash_demo --lhgr 5000000 --end-time 3600 --no-run
cd /tmp/crash_demo
python3 rodMaker.py && mv blockMeshDict system/ && blockMesh > log.blockMesh
offbeat > log.offbeat 2>&1     # va planter (core dumped)
```

---

## Étape 3 — Lire le log et IDENTIFIER la signature réelle

```bash
tail -40 log.offbeat
```

⚠️ **Leçon clé** : il existe **trois** familles de signatures, à ne pas confondre :

1. **Erreur fatale classique** → bloc commençant par `--> FOAM FATAL ERROR`
   (ex. condition limite manquante).
2. **Erreur d'entrée/sortie** → bloc `--> FOAM FATAL IO ERROR`
   (ex. dictionnaire manquant, champ de restart illisible).
3. **Crash par SIGNAL (SIGFPE/SIGSEGV)** → PAS de bloc « FATAL », mais une
   **trace de pile** :
   ```
   #1  Foam::sigFpe::sigHandler(int) in .../libOpenFOAM.so
   ...
   #5  Foam::LimbackCreepModel::correctCreep(...)   <-- la vraie cause
   ```

❌ **Piège** : la ligne de démarrage
`trapFpe: Floating point exception trapping enabled` est **bénigne** (elle dit
juste que le piégeage est actif). Ne JAMAIS baser un motif dessus — c'est
exactement ce qui causait un faux diagnostic « FPE ». Le `_diagnose` ancre
désormais la recherche sur le **bloc fatal** ou la **trace `sigHandler`**, ce
qui exclut cette bannière.

---

## Étape 4 — Extraire un motif regex stable

Règles pour un bon motif :
- Prendre une sous-chaîne **distinctive** du message ou de la trace.
- **Enlever** ce qui change d'un run à l'autre : numéro de patch
  (`patch=260127`), chemins absolus, numéros de ligne, adresses.
- Échapper les caractères spéciaux regex si besoin.
- Le matching se fait avec `re.I` (insensible à la casse) et `re.S` (le `.`
  traverse les sauts de ligne), **uniquement dans la zone fatale**.

Exemples de motifs validés :
| Crash | Motif |
|-------|-------|
| Divergence SIGFPE | `sigFpe::sigHandler` (ou `Floating point exception`) |
| Restart illisible | `attempt to read beyond EOF.*coolantPressureList` |
| solverDict manquant | `cannot find file .*constant/solverDict` |
| Maillage absent | `Cannot find file "points" in directory "polyMesh"` |

---

## Étape 5 — Choisir (ou écrire) le correctif

Trois cas de figure :

**(a) Un correctif existant convient** → utiliser son nom dans `fix_function`.
Correctifs disponibles (clés de `FIX_REGISTRY` dans `offbeat_executor.py`) :
- `fix_reduce_timestep` — divise `deltaT` par 10 + pas de temps adaptatif
- `fix_relax_solver` — assouplit tolérances de `fvSolution`
- `fix_increase_outer_iterations` — augmente `nOuterCorrectors`
- `fix_clean_restart` — nettoie les pas de temps (restart propre)

**(b) Aucun correctif automatique sûr** → `"fix_function": null`. L'erreur sera
**diagnostiquée** et une suggestion (`fix_description`) affichée, sans réparation
auto. À privilégier pour tout ce qui exige une intervention humaine.

**(c) Nouveau type de correctif** → ajouter une fonction dans
`offbeat_executor.py` puis l'inscrire dans `FIX_REGISTRY` :
```python
def fix_mon_correctif(case_dir: Path) -> str:
    # ... édition de dictionnaire via _edit_dict_entry(...) ...
    return "ce qui a été fait"

FIX_REGISTRY = {
    ...,
    "fix_mon_correctif": fix_mon_correctif,
}
```

> 🚫 **Garde-fou physique (CLAUDE.md §6)** : pour les erreurs de *physique
> combustible* (fermeture du gap, gonflement haut burnup, divergence du contact
> mécanique), NE PAS inventer de correctif. Les déclencher sur de vrais cas,
> capturer le vrai message, et au besoin laisser `fix_function: null`.

---

## Étape 6 — Ajouter l'entrée dans error_kb.json

Ouvrir `offbeat_skills/error_kb.json` et ajouter un objet. Mettre les motifs les
plus **spécifiques en premier** (la première correspondance gagne) :

```json
{
  "id": "mon_erreur",
  "validated": true,
  "pattern": "sous-chaine distinctive du log",
  "diagnosis": "Explication claire de la cause.",
  "fix_function": "fix_reduce_timestep",
  "fix_description": "Ce que fait le correctif (ou la marche à suivre manuelle).",
  "dict_patches": [],
  "references": ["Validé empiriquement le ..."]
}
```
- `validated: true` = motif confirmé sur un vrai log (par opposition à supposé).
- `fix_function: null` pour une entrée diagnostic-seul.

Aucun rebuild nécessaire : `offbeat_executor` recharge le JSON au démarrage.

---

## Étape 7 — Tester

**Test 1 — le motif matche le vrai log :**
```bash
python3 -c "
from tools.offbeat_executor import OffbeatExecutorTool
log = open('/tmp/crash_demo/log.offbeat').read()
e, _ = OffbeatExecutorTool()._diagnose(log)
print('Diagnostic :', e['id'] if e else 'INCONNU')
"
```
Doit afficher l'`id` de ta nouvelle entrée (et non `INCONNU`).

**Test 2 — self-healing de bout en bout** (si correctif automatique) :
```bash
python3 -c "
from tools.offbeat_executor import OffbeatExecutorTool
print(OffbeatExecutorTool()._run(case_dir='/tmp/crash_demo', self_healing=True))
"
```
Vérifier l'enchaînement : `Diagnostic … → Correctif appliqué … → Relance → succès`.

---

## Récapitulatif visuel

```
cas qui marche
   │  (Étape 2 : casser)
   ▼
crash  ──►  lire log.offbeat  ──►  identifier la zone fatale
   │            (Étape 3)              (FATAL ERROR / IO ERROR / sigHandler)
   │                                          │ (Étape 4 : motif regex stable)
   │                                          ▼
   │                                 choisir/écrire le fix (Étape 5)
   │                                          │
   │                                          ▼
   │                         ajouter l'entrée error_kb.json (Étape 6)
   │                                          │
   └──────────────────────────────►  tester _diagnose + _run (Étape 7)
```
