"""
bench_selfhealing.py — Banc d'essai quantitatif de l'auto-reparation.

But : mesurer, PAR CATEGORIE DE CAUSE RACINE, ce que la boucle de self-healing
sait reellement resoudre. Les trois cas ponctuels documentes precedemment
suggeraient que la reparation par motif traite bien les erreurs numeriques mais
echoue sur les erreurs de coherence physique ; ce banc transforme cette
intuition en mesure.

Principe : on part d'un cas SAIN genere par input_creator, puis on y injecte
une faute controlee. On lance ensuite l'executeur avec self_healing=True et on
classe l'issue selon deux axes independants :

  - DETECTE  : un motif de error_kb.json a-t-il reconnu l'erreur ?
  - REPARE   : le cas a-t-il fini par tourner ?

La case interessante est « detecte mais non repare » : elle materialise le fait
qu'un motif de log identifie un SYMPTOME, pas une CAUSE.

Usage (depuis la racine du depot, OpenFOAM sourcE) :
    python -m evaluation.bench_selfhealing
    python -m evaluation.bench_selfhealing --workdir /tmp/bench --json out.json
"""

import argparse
import json
import re
import shutil
import time
from collections import defaultdict
from pathlib import Path

from tools.input_creator import OffbeatInputCreatorTool, _patch_roddict
from tools.offbeat_executor import OffbeatExecutorTool

TEMPLATE = "fuel_rod_1D_pwr"
# Duree courte : on mesure la capacite de diagnostic, pas la physique. Chaque
# tentative de reparation relance un run complet, d'ou l'interet d'un cas bref.
BASE_PARAMS = {"linear_heat_rate": 25000, "end_time": 2000}


# --------------------------------------------------------------------------
# Injecteurs de fautes. Chacun corrompt un cas DEJA cree et valide.
# --------------------------------------------------------------------------

def _rm(case: Path, rel: str):
    p = case / rel
    if p.is_dir():
        shutil.rmtree(p)
    elif p.exists():
        p.unlink()


def _set_foam(case: Path, rel: str, key: str, value: str):
    """Ecrit `key value;` dans un dictionnaire OpenFOAM, sans validation."""
    f = case / rel
    txt = f.read_text()
    new, n = re.subn(rf"(^\s*{re.escape(key)}\s+)\S[^;]*?(\s*;)",
                     rf"\g<1>{value}\g<2>", txt, flags=re.M)
    if not n:
        raise RuntimeError(f"cle '{key}' introuvable dans {rel}")
    f.write_text(new)


def _set_lhgr(case: Path, value: float):
    """Force la puissance lineique, y compris a des valeurs absurdes (ce que
    input_creator accepte : aucune borne physique n'est verifiee)."""
    f = case / "constant" / "solverDict"
    txt = f.read_text()

    def repl(m):
        nums = re.findall(r"[-+0-9.eE]+", m.group(1))
        new = [n if float(n) == 0.0 else f"{value:g}" for n in nums]
        return "lhgr" + m.group(0)[len("lhgr"):].replace(
            m.group(1), "    " + "    ".join(new) + " ")

    f.write_text(re.subn(r"lhgr\s*\(([^)]*)\)", repl, txt)[0])


# --------------------------------------------------------------------------
# Catalogue de fautes, groupees par CATEGORIE DE CAUSE RACINE
# --------------------------------------------------------------------------
# Deux etiquettes a priori, volontairement DISTINCTES — c'est tout l'enjeu :
#   correctif_kb    : error_kb.json propose-t-il un correctif pour ce motif ?
#   cause_traitable : ce correctif peut-il, par nature, traiter la CAUSE RACINE ?
# Un cas (correctif_kb=True, cause_traitable=False) est le coeur du probleme :
# la base croit savoir reparer, mais le remede ne s'attaque qu'au symptome.
# Exemple : une puissance 200x nominale produit une exception flottante ; le
# motif matche et declenche « reduire le pas de temps », ce qui ne peut rien
# contre une donnee d'entree physiquement absurde.
# None = sans objet (cas de controle, aucune faute injectee).

FAULTS = [
    # --- Controle : cas sains, doivent passer du premier coup ---------------
    dict(id="ctrl_nominal_25kw", categorie="Controle (cas sain)",
         desc="Cas nominal 25 kW/m", correctif_kb=None, cause_traitable=None,
         inject=lambda c: None),
    dict(id="ctrl_nominal_18kw", categorie="Controle (cas sain)",
         desc="Cas nominal 18 kW/m", correctif_kb=None, cause_traitable=None,
         inject=lambda c: _set_lhgr(c, 18000)),

    # --- Numerique : divergence du solveur ---------------------------------
    dict(id="num_lhgr_x200", categorie="Numerique (divergence)",
         desc="Puissance 200x nominale (5e6 W/m)", correctif_kb=True, cause_traitable=False,
         inject=lambda c: _set_lhgr(c, 5.0e6)),
    dict(id="num_lhgr_x100", categorie="Numerique (divergence)",
         desc="Puissance 100x nominale (2.5e6 W/m)", correctif_kb=True, cause_traitable=False,
         inject=lambda c: _set_lhgr(c, 2.5e6)),
    dict(id="num_lhgr_negatif", categorie="Numerique (divergence)",
         desc="Puissance negative (-5e6 W/m)", correctif_kb=True, cause_traitable=False,
         inject=lambda c: _set_lhgr(c, -5.0e6)),

    # --- Coherence physique : geometrie impossible -------------------------
    # Injectees APRES creation pour contourner la validation geometrique
    # ajoutee dans input_creator : on teste ici ce que le self-healing sait
    # faire, pas ce que la validation amont intercepte.
    dict(id="geo_interference", categorie="Coherence physique (geometrie)",
         desc="Pastille plus large que l'interieur de la gaine",
         correctif_kb=False, cause_traitable=False,
         inject=lambda c: _patch_roddict(c, "rOuterFuel", [4.6])),
    dict(id="geo_gaine_negative", categorie="Coherence physique (geometrie)",
         desc="Epaisseur de gaine negative", correctif_kb=False, cause_traitable=False,
         inject=lambda c: _patch_roddict(c, "rInnerClad", [5.5, 5.5])),
    dict(id="geo_pastille_nulle", categorie="Coherence physique (geometrie)",
         desc="Rayon de pastille nul", correctif_kb=False, cause_traitable=False,
         inject=lambda c: _patch_roddict(c, "rOuterFuel", [0.0])),

    # --- Fichier manquant ---------------------------------------------------
    dict(id="fic_solverdict", categorie="Fichier manquant",
         desc="constant/solverDict supprime", correctif_kb=False, cause_traitable=False,
         inject=lambda c: _rm(c, "constant/solverDict")),
    dict(id="fic_champ_T", categorie="Fichier manquant",
         desc="Champ initial 0/T supprime", correctif_kb=False, cause_traitable=False,
         inject=lambda c: _rm(c, "0/T")),
    dict(id="fic_controldict", categorie="Fichier manquant",
         desc="system/controlDict supprime", correctif_kb=False, cause_traitable=False,
         inject=lambda c: _rm(c, "system/controlDict")),

    # --- Entree de dictionnaire invalide ------------------------------------
    dict(id="dic_endtime_texte", categorie="Entree invalide",
         desc="endTime non numerique", correctif_kb=False, cause_traitable=False,
         inject=lambda c: _set_foam(c, "system/controlDict", "endTime", "abc")),
    dict(id="dic_endtime_hors_histo", categorie="Entree invalide",
         desc="endTime au-dela de l'historique de puissance (1.5e8 s)",
         correctif_kb=True, cause_traitable=True,
         inject=lambda c: _set_foam(c, "system/controlDict",
                                    "endTime", "1.5e8")),
    dict(id="dic_deltat_negatif", categorie="Entree invalide",
         desc="Pas de temps negatif", correctif_kb=False, cause_traitable=False,
         inject=lambda c: _set_foam(c, "system/controlDict", "deltaT", "-1")),
]


# --------------------------------------------------------------------------
# Analyse du rapport produit par l'executeur
# --------------------------------------------------------------------------

def parse_report(report: str) -> dict:
    """Extrait du rapport textuel les indicateurs mesures."""
    repare = ("succès" in report) or ("success" in report.lower())
    inconnu = "Erreur inconnue de la base" in report
    sans_correctif = "Pas de correctif automatique" in report
    echec_maillage = "ÉCHEC du maillage" in report

    m = re.search(r"Diagnostic\s*:\s*(.+)", report)
    diagnostic = m.group(1).strip() if m else ""

    # Etape a laquelle le cas s'arrete. Un echec AU MAILLAGE est une bonne
    # nouvelle pour une faute geometrique : blockMesh refuse la geometrie avant
    # que le solveur ne soit lance. Le confondre avec un echec solveur ferait
    # passer un garde-fou correct pour une defaillance.
    if repare:
        etape = "ok"
    elif echec_maillage:
        etape = "maillage"
    else:
        etape = "solveur"

    return {
        "repare": repare,
        # Detecte = un motif de la base a reconnu l'erreur (par opposition au
        # repli LLM, qui signale explicitement une erreur inconnue).
        "detecte": bool(diagnostic) and not inconnu,
        "repli_llm": "Diagnostic LLM" in report,
        "sans_correctif_disponible": sans_correctif,
        "etape_echec": etape,
        "correctifs_appliques": len(re.findall(r"Correctif appliqué", report)),
        "tentatives": len(re.findall(r"--- Tentative \d+/\d+ ---", report)),
        "diagnostic": diagnostic[:150],
    }


def run_one(fault: dict, workdir: Path, creator, runner) -> dict:
    case = workdir / fault["id"]
    if case.exists():
        shutil.rmtree(case)

    creation = creator._run(case_dir=str(case), template_name=TEMPLATE,
                            params=json.dumps(BASE_PARAMS))
    if creation.startswith("ERREUR"):
        return {**fault_meta(fault), "erreur_creation": creation}

    fault["inject"](case)

    t0 = time.time()
    report = runner._run(case_dir=str(case), self_healing=True)
    duree = time.time() - t0

    return {**fault_meta(fault), **parse_report(report), "duree_s": round(duree, 1)}


def fault_meta(f: dict) -> dict:
    return {k: f[k] for k in ("id", "categorie", "desc", "correctif_kb", "cause_traitable")}


# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workdir", default="/tmp/bench_selfhealing")
    ap.add_argument("--json", default="evaluation/resultats_selfhealing.json")
    ap.add_argument("--only", default="", help="filtre sur l'id d'une faute")
    args = ap.parse_args()

    workdir = Path(args.workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    creator, runner = OffbeatInputCreatorTool(), OffbeatExecutorTool()

    faults = [f for f in FAULTS if args.only in f["id"]]
    results = []
    for i, f in enumerate(faults, 1):
        print(f"[{i}/{len(faults)}] {f['id']:24s} {f['desc']}", flush=True)
        r = run_one(f, workdir, creator, runner)
        results.append(r)
        etat = "REPARE" if r.get("repare") else "echec"
        print(f"      -> {etat} | detecte={r.get('detecte')} "
              f"| tentatives={r.get('tentatives')} | {r.get('duree_s')}s",
              flush=True)

    # --- Synthese par categorie -------------------------------------------
    par_cat = defaultdict(list)
    for r in results:
        par_cat[r["categorie"]].append(r)

    print("\n" + "=" * 78)
    print(f"{'Categorie':<34}{'n':>3} {'detectes':>9} {'repares':>8} "
          f"{'echec au maillage':>18}")
    print("-" * 78)
    for cat, rows in par_cat.items():
        n = len(rows)
        det = sum(1 for r in rows if r.get("detecte"))
        rep = sum(1 for r in rows if r.get("repare"))
        mail = sum(1 for r in rows if r.get("etape_echec") == "maillage")
        print(f"{cat:<34}{n:>3} {det:>4}/{n:<4} {rep:>3}/{n:<4} {mail:>13}/{n:<4}")
    print("=" * 78)

    fautes = [r for r in results if r.get("cause_traitable") is not None]
    n, nf = len(results), len(fautes)
    det = sum(1 for r in results if r.get("detecte"))
    rep = sum(1 for r in results if r.get("repare"))
    llm = sum(1 for r in results if r.get("repli_llm"))
    print(f"TOTAL : {n} cas dont {nf} fautes injectees "
          f"| detectes {det} | repares {rep} | repli LLM {llm}")

    # --- Le croisement qui porte la these ---------------------------------
    print("\nTaux de reparation selon que le correctif peut traiter la cause :")
    for label, sel in (("correctif KB + cause traitable",
                        lambda r: r["correctif_kb"] and r["cause_traitable"]),
                       ("correctif KB mais cause NON traitable",
                        lambda r: r["correctif_kb"] and not r["cause_traitable"]),
                       ("aucun correctif dans la KB",
                        lambda r: not r["correctif_kb"])):
        grp = [r for r in fautes if sel(r)]
        if grp:
            ok = sum(1 for r in grp if r.get("repare"))
            print(f"   {label:<40} {ok}/{len(grp)} repares")

    out = Path(args.json)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    print(f"\nResultats detailles -> {out}")


if __name__ == "__main__":
    main()
