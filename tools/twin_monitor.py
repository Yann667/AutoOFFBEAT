"""
twin_monitor.py : Boucle d'assimilation de donnees du jumeau numerique (D3).

Brique 3 du jumeau numerique (cf. GUIDE.md Partie 2). Fait le lien entre des
donnees d'exploitation (historique de puissance, conditions caloporteur...) et
le crayon simule : des qu'une nouvelle donnee arrive, le jumeau RE-SIMULE le
crayon avec ces conditions puis RE-EVALUE sa surete.

⚠️ Ce N'EST PAS du temps reel a la seconde : OFFBEAT est un solveur batch
(minutes a heures) et il n'y a pas de capteur branche en direct sur un crayon.
C'est un jumeau « a la mise a jour » : il se resynchronise a CHAQUE nouvelle
donnee d'exploitation. Le vrai temps reel demanderait un modele reduit
(surrogate), cf. GUIDE.md etape D5.

Format d'entree (CSV) : une ligne d'en-tete avec des noms de parametres compris
par input_creator (ex. linear_heat_rate, end_time, write_interval), puis une
ligne par point d'exploitation. La DERNIERE ligne = l'etat courant du crayon.

    linear_heat_rate,end_time
    20000,31500000
    25000,63000000      <- point d'exploitation courant

Chaine reutilisee (aucune logique dupliquee) :
    input_creator (_apply_params)  ->  patch des conditions du cas
    offbeat_executor               ->  re-simulation + self-healing
    safety_analyzer                ->  statut 🟢/🟡/🔴 + pronostic

Usage :
    # un seul cycle (pour tester) :
    python -m tools.twin_monitor --case-dir /chemin/cas --data-csv ops.csv --once
    # surveillance continue :
    python -m tools.twin_monitor --case-dir /chemin/cas --data-csv ops.csv
"""

import csv
import time
import hashlib
import argparse
from pathlib import Path

from tools.input_creator import _apply_params, PARAM_MAP
from tools.offbeat_executor import OffbeatExecutorTool, _clean_timesteps
from tools.safety_analyzer import OffbeatSafetyAnalyzerTool


def _csv_to_params(data_csv: Path) -> dict:
    """Lit le CSV d'exploitation et retourne les parametres du DERNIER point
    (l'etat courant). Ne garde que les colonnes reconnues par input_creator
    (PARAM_MAP) et converties en nombre quand c'est possible."""
    rows = list(csv.DictReader(data_csv.open(encoding="utf-8")))
    if not rows:
        return {}
    last = rows[-1]
    params = {}
    for key, raw in last.items():
        if key not in PARAM_MAP or raw is None or raw.strip() == "":
            continue
        try:
            params[key] = float(raw)
        except ValueError:
            params[key] = raw
    return params


def _digest(path: Path) -> str:
    """Empreinte du fichier de donnees : sert a detecter un changement."""
    return hashlib.md5(path.read_bytes()).hexdigest()


def assimilate_once(case_dir: Path, data_csv: Path,
                    prognosis: bool = True) -> str:
    """Un cycle complet d'assimilation : patch des conditions -> re-simulation
    -> analyse de surete. Retourne le rapport de surete (texte)."""
    params = _csv_to_params(data_csv)
    creator_report = ""
    if params:
        applied, ignored = _apply_params(case_dir, params)
        creator_report = (f"Conditions mises a jour : {applied or 'aucune'}"
                          + (f" (ignorees : {ignored})" if ignored else ""))
    else:
        creator_report = "Aucun parametre exploitable dans le CSV."

    # On repart d'un etat propre : OFFBEAT ne sait pas redemarrer depuis
    # latestTime, et une nouvelle histoire de puissance impose de RE-simuler
    # depuis le debut (la performance combustible est dependante du chemin).
    clean_report = _clean_timesteps(case_dir)
    run_report = OffbeatExecutorTool()._run(case_dir=str(case_dir))
    safety_report = OffbeatSafetyAnalyzerTool()._run(
        case_dir=str(case_dir), prognosis=prognosis)

    return ("\n".join([
        "=== Cycle d'assimilation ===",
        creator_report,
        clean_report,
        "--- Execution ---",
        run_report,
        "--- Surete ---",
        safety_report,
    ]))


def watch(case_dir: Path, data_csv: Path, poll_s: int = 30,
          max_cycles: int | None = None, prognosis: bool = True):
    """Surveille `data_csv` et lance un cycle d'assimilation a chaque
    changement. Boucle synchrone : un run doit finir avant d'en lancer un
    autre (pas d'empilement de simulations). `max_cycles` borne le nombre de
    cycles (None = illimite). Ctrl-C pour arreter."""
    if not (case_dir / "system" / "controlDict").exists():
        print(f"ERREUR : {case_dir} n'est pas un cas OFFBEAT valide.")
        return
    if not data_csv.exists():
        print(f"ERREUR : fichier de donnees '{data_csv}' introuvable.")
        return

    last_digest, cycles = None, 0
    print(f"[twin] surveillance de {data_csv} (poll {poll_s}s). Ctrl-C pour arreter.")
    while max_cycles is None or cycles < max_cycles:
        try:
            digest = _digest(data_csv)
            if digest != last_digest:                 # nouvelle donnee detectee
                last_digest = digest
                cycles += 1
                print(f"\n[twin] nouvelle donnee -> cycle {cycles}")
                print(assimilate_once(case_dir, data_csv, prognosis=prognosis))
            time.sleep(poll_s)
        except KeyboardInterrupt:
            print("\n[twin] arret demande.")
            break
        except Exception as exc:  # noqa: BLE001 : la boucle ne doit pas mourir
            print(f"[twin] erreur cycle (ignoree) : {exc}")
            time.sleep(poll_s)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Boucle d'assimilation du jumeau numerique (crayon).")
    ap.add_argument("--case-dir", required=True, help="Cas OFFBEAT a piloter.")
    ap.add_argument("--data-csv", required=True, help="CSV de donnees d'exploitation.")
    ap.add_argument("--poll-s", type=int, default=30, help="Periode de scrutation (s).")
    ap.add_argument("--once", action="store_true", help="Un seul cycle puis sortie.")
    ap.add_argument("--max-cycles", type=int, default=None, help="Nombre max de cycles.")
    ap.add_argument("--no-prognosis", action="store_true")
    a = ap.parse_args()

    case, data = Path(a.case_dir), Path(a.data_csv)
    if a.once:
        print(assimilate_once(case, data, prognosis=not a.no_prognosis))
    else:
        watch(case, data, poll_s=a.poll_s, max_cycles=a.max_cycles,
              prognosis=not a.no_prognosis)
