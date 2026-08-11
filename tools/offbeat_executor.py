"""
offbeat_executor.py – Outil d'exécution OFFBEAT avec self-healing.

Équivalent du "Executor" d'AutoFLUKA : là où AutoFLUKA lance `rfluka`
puis relit les logs pour ajuster le .inp et relancer, ici on lance le
solveur `offbeat`, on parse `log.offbeat`, et on applique des
corrections déterministes sur les dictionnaires OpenFOAM avant de
relancer – jusqu'à MAX_RETRIES.

Stratégie à deux étages (fidèle aux FLUKA Skills) :
  1. Base de connaissance déterministe (ERROR_KB) : erreurs connues
     → correctif scripté (rapide, gratuit, fiable).
  2. Repli LLM : si l'erreur est inconnue, on envoie la fin du log au
     `debug_llm` (Claude/Gemini/Ollama selon .env) pour un diagnostic.
"""

import os
import re
import json
import shutil
import subprocess
from pathlib import Path
from typing import Type

from pydantic import BaseModel, Field
from langchain_core.tools import BaseTool

OFFBEAT_BIN = os.getenv("OFFBEAT_BIN", "offbeat")
BLOCKMESH_BIN = os.getenv("BLOCKMESH_BIN", "blockMesh")
MAX_RETRIES = int(os.getenv("SELF_HEALING_MAX_RETRIES", "3"))
LOG_TAIL_CHARS = 4000  # portion de log envoyée au LLM en repli


# --------------------------------------------------------------------------
# 1) Correctifs déterministes sur les dictionnaires OpenFOAM
# --------------------------------------------------------------------------

def _edit_dict_entry(case_dir: Path, rel_path: str, key: str, value: str) -> bool:
    """Remplace `key <ancienne valeur>;` par `key <value>;` dans un
    dictionnaire OpenFOAM. Édition par regex, sans dépendance à PyFoam,
    pour rester léger dans le conteneur Docker."""
    f = case_dir / rel_path
    if not f.exists():
        return False
    text = f.read_text()
    new_text, n = re.subn(
        rf"(^\s*{key}\s+)\S+(\s*;)", rf"\g<1>{value}\g<2>", text, flags=re.M
    )
    if n:
        f.write_text(new_text)
    return bool(n)


def _get_dict_entry(case_dir: Path, rel_path: str, key: str) -> str | None:
    f = case_dir / rel_path
    if not f.exists():
        return None
    m = re.search(rf"^\s*{key}\s+(\S+?)\s*;", f.read_text(), flags=re.M)
    return m.group(1) if m else None


def fix_reduce_timestep(case_dir: Path) -> str:
    """Divise deltaT par 10 et active le pas de temps adaptatif."""
    current = _get_dict_entry(case_dir, "system/controlDict", "deltaT")
    actions = []
    if current:
        try:
            new_dt = float(current) / 10.0
            if _edit_dict_entry(case_dir, "system/controlDict", "deltaT", f"{new_dt:g}"):
                actions.append(f"deltaT réduit : {current} -> {new_dt:g}")
        except ValueError:
            pass
    if _edit_dict_entry(case_dir, "system/controlDict", "adjustTimeStep", "yes"):
        actions.append("adjustTimeStep activé")
    return "; ".join(actions) or "controlDict introuvable ou clé absente"


def fix_relax_solver(case_dir: Path) -> str:
    """Assouplit la tolérance du solveur linéaire (divergence PCG/GAMG)."""
    ok = _edit_dict_entry(case_dir, "system/fvSolution", "tolerance", "1e-06")
    ok |= _edit_dict_entry(case_dir, "system/fvSolution", "relTol", "0.01")
    return "Tolérances fvSolution assouplies" if ok else "fvSolution non modifié"


def fix_increase_outer_iterations(case_dir: Path) -> str:
    """Augmente le nombre d'itérations externes du couplage
    thermo-mécanique d'OFFBEAT (non-convergence du point fixe)."""
    ok = _edit_dict_entry(case_dir, "system/fvSolution", "nOuterCorrectors", "200")
    return "nOuterCorrectors augmenté à 200" if ok else "fvSolution non modifié"


def _clean_timesteps(case_dir: Path) -> str:
    """Supprime les pas de temps écrits (sauf 0/) avec `foamListTimes -rm`.
    Indispensable avant une relance : OFFBEAT redémarre par défaut depuis
    latestTime (startFrom latestTime) et NE SAIT PAS relire le champ D qu'il
    a écrit (BC coolantPressureList -> 'attempt to read beyond EOF'). On
    repart donc proprement de 0/, ce qui est aussi la bonne sémantique quand
    on corrige deltaT/le solveur : on RECOMMENCE, on ne continue pas un état
    divergé. Repli manuel si foamListTimes indisponible."""
    try:
        subprocess.run(
            ["foamListTimes", "-rm", "-case", str(case_dir)],
            cwd=case_dir, capture_output=True, text=True, timeout=120,
        )
        return "pas de temps nettoyés (restart propre depuis 0/)"
    except Exception:  # repli : suppression manuelle des dossiers numériques
        removed = 0
        for d in case_dir.iterdir():
            if d.is_dir() and d.name not in ("0", "constant", "system"):
                try:
                    float(d.name)  # ne supprime que les dossiers de temps
                    shutil.rmtree(d, ignore_errors=True)
                    removed += 1
                except ValueError:
                    pass
        return f"pas de temps nettoyés manuellement ({removed} dossiers)"


def fix_clean_restart(case_dir: Path) -> str:
    """Correctif pour l'erreur de relecture au redémarrage. Pas d'édition de
    dictionnaire : le nettoyage des pas de temps (restart propre depuis 0/)
    est assuré par la boucle de self-healing avant chaque relance."""
    return "redémarrage propre requis"


def fix_reduce_endtime(case_dir: Path) -> str:
    """Réduit endTime de 1 % pour le ramener SOUS le dernier point tabulé
    de l'historique de puissance/profil axial. OFFBEAT plante (nextTimeMarker)
    quand endTime atteint exactement le dernier marqueur temporel : il n'y a
    alors plus de marqueur suivant pour piloter le pas de temps adaptatif."""
    current = _get_dict_entry(case_dir, "system/controlDict", "endTime")
    if current:
        try:
            new_end = float(current) * 0.99
            if _edit_dict_entry(case_dir, "system/controlDict", "endTime", f"{new_end:g}"):
                return f"endTime réduit : {current} -> {new_end:g} (sous le dernier point tabulé)"
        except ValueError:
            pass
    return "controlDict introuvable ou endTime absent"


# Registre nom -> fonction de correctif. Les FONCTIONS restent en Python
# (c'est du code), mais les MOTIFS/diagnostics/choix-de-fix viennent du JSON
# éditable offbeat_skills/error_kb.json (cf. CLAUDE.md §6 : volume éditable
# sans rebuild). fix_function: null dans le JSON => diagnostic seul, pas de
# correctif automatique.
FIX_REGISTRY = {
    "fix_reduce_timestep": fix_reduce_timestep,
    "fix_relax_solver": fix_relax_solver,
    "fix_increase_outer_iterations": fix_increase_outer_iterations,
    "fix_clean_restart": fix_clean_restart,
    "fix_reduce_endtime": fix_reduce_endtime,
}

# Repli minimal si error_kb.json est absent ou invalide : le self-healing
# doit continuer à fonctionner sur les cas les plus courants.
_FALLBACK_KB = [
    {
        "pattern": r"attempt to read beyond EOF.*coolantPressureList"
                   r"|coolantPressureList.*attempt to read beyond EOF",
        "diagnosis": "Champ de redémarrage illisible (BC coolantPressureList).",
        "fix": fix_clean_restart,
    },
    {
        "pattern": r"Floating point exception|sigFpe",
        "diagnosis": "Exception flottante (souvent divergence numérique).",
        "fix": fix_reduce_timestep,
    },
]

ERROR_KB_PATH = Path(os.getenv(
    "ERROR_KB_PATH",
    Path(__file__).parent.parent / "offbeat_skills" / "error_kb.json",
))


def _load_error_kb() -> list[dict]:
    """Charge la base d'erreurs depuis error_kb.json et résout chaque
    `fix_function` (nom) en fonction Python via FIX_REGISTRY. Tolérant aux
    pannes : en cas d'erreur on retombe sur _FALLBACK_KB."""
    try:
        raw = json.loads(ERROR_KB_PATH.read_text(encoding="utf-8"))
        kb = []
        for entry in raw:
            if "pattern" not in entry:
                continue
            fix_name = entry.get("fix_function")
            kb.append({
                "id": entry.get("id", ""),
                "pattern": entry["pattern"],
                "diagnosis": entry.get("diagnosis", ""),
                "fix": FIX_REGISTRY.get(fix_name),  # None si null/inconnu
                "fix_description": entry.get("fix_description", ""),
            })
        return kb or _FALLBACK_KB
    except Exception:  # JSON absent/invalide : on ne casse pas le self-healing
        return _FALLBACK_KB


ERROR_KB = _load_error_kb()


# --------------------------------------------------------------------------
# 2) L'outil LangChain
# --------------------------------------------------------------------------

class OffbeatExecutorInput(BaseModel):
    case_dir: str = Field(
        description="Chemin absolu du répertoire de cas OpenFOAM/OFFBEAT "
                    "(contenant 0/, constant/, system/), ex. /host/mon_cas"
    )
    self_healing: bool = Field(
        default=True,
        description="Si True, tente de corriger automatiquement le cas et "
                    "de relancer en cas de crash.",
    )


class OffbeatExecutorTool(BaseTool):
    """Lance le solveur offbeat, surveille log.offbeat, auto-répare."""

    name: str = "offbeat_executor"
    description: str = (
        "Exécute le solveur OFFBEAT sur un cas OpenFOAM. Lit log.offbeat, "
        "détecte les erreurs connues (Courant number, divergence, "
        "non-convergence du couplage) et applique des correctifs avant de "
        "relancer automatiquement. Retourne un rapport d'exécution."
    )
    args_schema: Type[BaseModel] = OffbeatExecutorInput

    def _build_mesh(self, case_dir: Path) -> tuple[int, str]:
        """Construit le maillage avant le solveur (équivalent du début de
        l'Allrun OFFBEAT) :
            python3 rodMaker.py   -> écrit blockMeshDict (à la racine du cas)
            mv blockMeshDict system/
            blockMesh             -> crée constant/polyMesh/
        Si rodMaker.py est absent, on suppose que system/blockMeshDict est
        déjà fourni et on lance directement blockMesh."""
        # 1) rodMaker.py : rodDict -> blockMeshDict
        if (case_dir / "rodMaker.py").exists():
            proc = subprocess.run(
                ["python3", "rodMaker.py"],
                cwd=case_dir, capture_output=True, text=True, timeout=300,
            )
            if proc.returncode != 0:
                return proc.returncode, f"rodMaker.py a échoué :\n{proc.stderr}"
            # rodMaker écrit blockMeshDict à la racine : on le déplace
            root_bmd = case_dir / "blockMeshDict"
            if root_bmd.exists():
                shutil.move(str(root_bmd), str(case_dir / "system" / "blockMeshDict"))

        # 2) blockMesh : blockMeshDict -> constant/polyMesh
        log_path = case_dir / "log.blockMesh"
        with open(log_path, "w") as log_file:
            proc = subprocess.run(
                [BLOCKMESH_BIN],
                stdout=log_file, stderr=subprocess.STDOUT,
                cwd=case_dir, timeout=600,
            )
        return proc.returncode, log_path.read_text(errors="replace")

    def _run_solver(self, case_dir: Path) -> tuple[int, str]:
        """Un run du solveur ; le log est écrit dans log.offbeat comme le
        veut la convention OpenFOAM."""
        log_path = case_dir / "log.offbeat"
        with open(log_path, "w") as log_file:
            proc = subprocess.run(
                [OFFBEAT_BIN, "-case", str(case_dir)],
                stdout=log_file,
                stderr=subprocess.STDOUT,
                cwd=case_dir,
                timeout=int(os.getenv("OFFBEAT_TIMEOUT_S", "7200")),
            )
        return proc.returncode, log_path.read_text(errors="replace")

    def _diagnose(self, log_text: str):
        """Cherche la première erreur connue dans le log.

        IMPORTANT : on ne matche les motifs de la base QUE dans le bloc
        d'erreur fatale réel. Scanner tout le log provoque des faux positifs
        (ex. la bannière de démarrage OpenFOAM « ...Floating point exception
        trapping enabled... » faisait diagnostiquer un FPE inexistant).
        On capture aussi bien 'FOAM FATAL ERROR' que 'FOAM FATAL IO ERROR'.
        Si aucun bloc fatal n'est trouvé, on renvoie None (→ repli LLM) avec
        la fin du log comme contexte, sans tenter de correctif hasardeux."""
        m = re.search(r"FOAM FATAL (?:IO )?ERROR.*", log_text, flags=re.S)
        if m is None:
            # Crash par SIGNAL (SIGFPE/SIGSEGV) : OFFBEAT ne produit alors PAS
            # de bloc 'FOAM FATAL ERROR' mais une trace de pile passant par le
            # gestionnaire de signal. On capture à partir de cette trace (ce
            # qui exclut la bannière bénigne 'trapFpe: ...trapping enabled').
            m = re.search(r"Foam::sig\w+::sigHandler.*", log_text, flags=re.S)
        if m is None:
            # Pas de bloc fatal identifiable : ne rien « réparer » à l'aveugle.
            return None, log_text[-LOG_TAIL_CHARS:]
        zone = m.group(0)
        for entry in ERROR_KB:
            # re.S : les motifs peuvent enjamber des sauts de ligne (le bloc
            # fatal OpenFOAM répartit message et fichier sur plusieurs lignes).
            if re.search(entry["pattern"], zone, flags=re.I | re.S):
                return entry, zone
        return None, zone

    def _rag_context(self, log_tail: str) -> str:
        """Récupère les passages de doc OFFBEAT/OpenFOAM pertinents pour
        l'erreur (via le RAG). Robuste : renvoie '' si le RAG est indisponible.
        La requête est en anglais (corpus majoritairement anglophone)."""
        try:
            from tools.rag_retriever import get_retriever
            # On cible la requête sur la dernière ligne d'erreur significative.
            query = "OFFBEAT OpenFOAM solver error: " + log_tail[-600:]
            docs = get_retriever(k=3).invoke(query)
            if not docs:
                return ""
            return "\n\n".join(d.page_content for d in docs)
        except Exception:  # RAG non prêt / indispo : on continue sans
            return ""

    def _llm_fallback(self, log_tail: str, case_dir: Path) -> str:
        """Erreur inconnue : on demande un diagnostic au LLM de debug,
        enrichi si possible par la doc récupérée via le RAG.
        (Pas d'application automatique – le superviseur décidera.)"""
        try:
            from config.llm_factory import get_debug_llm
            llm = get_debug_llm()
            doc_ctx = self._rag_context(log_tail)
            doc_block = (
                f"\n\nExtraits de documentation OFFBEAT/OpenFOAM pertinents :\n{doc_ctx}\n"
                if doc_ctx else ""
            )
            resp = llm.invoke(
                "Tu es un expert OpenFOAM/OFFBEAT. Voici la fin d'un "
                f"log.offbeat d'un cas situé dans {case_dir} :\n\n{log_tail}\n"
                f"{doc_block}\n"
                "Diagnostique l'erreur et propose le correctif minimal "
                "(fichier + clé de dictionnaire à modifier)."
            )
            tag = " (enrichi par le RAG)" if doc_ctx else ""
            return resp.content + tag
        except Exception as exc:  # le self-healing ne doit jamais crasher
            return f"(diagnostic LLM indisponible : {exc})"

    def _run(self, case_dir: str, self_healing: bool = True) -> str:
        case = Path(case_dir)
        if not (case / "system" / "controlDict").exists():
            return (f"ERREUR : {case_dir} n'est pas un cas OpenFOAM valide "
                    "(system/controlDict introuvable). Utilise d'abord "
                    "input_creator.")

        report = []

        # --- Maillage (rodMaker + blockMesh), une seule fois ---
        try:
            mesh_rc, mesh_log = self._build_mesh(case)
        except subprocess.TimeoutExpired:
            return "ERREUR : timeout pendant la génération du maillage."
        except FileNotFoundError:
            return (f"ERREUR : binaire '{BLOCKMESH_BIN}' introuvable. "
                    "OpenFOAM n'est pas sourcé dans l'environnement de l'app "
                    "(source .../etc/bashrc avant de lancer app.py).")
        if mesh_rc != 0:
            entry, zone = self._diagnose(mesh_log)
            diag = entry["diagnosis"] if entry else "voir log.blockMesh"
            return (f"ÉCHEC du maillage (blockMesh/rodMaker).\nDiagnostic : {diag}\n"
                    f"{zone[-LOG_TAIL_CHARS:]}")
        report.append("Maillage généré (rodMaker + blockMesh) : OK")

        attempts = MAX_RETRIES if self_healing else 1

        for attempt in range(1, attempts + 1):
            report.append(f"--- Tentative {attempt}/{attempts} ---")
            try:
                returncode, log_text = self._run_solver(case)
            except subprocess.TimeoutExpired:
                report.append("Timeout : simulation interrompue.")
                break
            except FileNotFoundError:
                return (f"ERREUR : binaire '{OFFBEAT_BIN}' introuvable. "
                        "Vérifie OFFBEAT_BIN et le sourcing d'OpenFOAM "
                        "(équivalent du RFLUKA_BIN manquant d'AutoFLUKA).")

            # NB : OFFBEAT renvoie un code retour != 0 même sur un run
            # réussi, et termine par un marqueur ' end ' (minuscule) sur sa
            # propre ligne — contrairement au ' End ' des solveurs OpenFOAM
            # standard. On se fie donc UNIQUEMENT à ce marqueur de fin.
            if re.search(r"(?im)^\s*end\s*$", log_text[-500:]):
                report.append(f"Simulation terminée avec succès "
                              f"(code retour offbeat={returncode}, ignoré).")
                report.append("Résultats prêts pour data_processor (pyvista).")
                return "\n".join(report)

            # --- Self-healing ---
            entry, zone = self._diagnose(log_text)
            if entry is None:
                report.append("Erreur inconnue de la base de connaissance.")
                report.append("Diagnostic LLM :\n" + self._llm_fallback(zone, case))
                break

            report.append(f"Diagnostic : {entry['diagnosis']}")
            # Entrée diagnostic-seul (fix_function: null dans le JSON) :
            # erreur identifiée mais sans correctif automatique sûr.
            if entry.get("fix") is None:
                hint = entry.get("fix_description", "")
                report.append("Pas de correctif automatique pour cette erreur."
                              + (f" Suggestion : {hint}" if hint else ""))
                break
            if not self_healing or attempt == attempts:
                report.append("Self-healing désactivé ou tentatives épuisées.")
                break
            action = entry["fix"](case)
            # On repart TOUJOURS d'un état propre : OFFBEAT ne sait pas
            # redémarrer depuis latestTime, et corriger deltaT/le solveur
            # implique de recommencer le run, pas de continuer un état divergé.
            cleaned = _clean_timesteps(case)
            report.append(f"Correctif appliqué : {action} ; {cleaned}. Relance...")

        report.append("ÉCHEC FINAL : intervention de l'utilisateur requise.")
        return "\n".join(report)
