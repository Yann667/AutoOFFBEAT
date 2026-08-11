#!/usr/bin/env python3
"""
run_sim.py – Lancement direct d'une simulation OFFBEAT, SANS LLM.

Enchaîne les trois outils du projet en ligne de commande :
    input_creator  ->  offbeat_executor  ->  data_processor

C'est le « chemin moteur » : il ne dépend ni d'Ollama ni d'aucun
fournisseur LLM. Utile pour tester/produire des simulations de façon
fiable, ou comme filet de sécurité quand un petit modèle n'enchaîne
pas correctement les appels d'outils.

Prérequis : OpenFOAM sourcé dans le shell courant (pour blockMesh/offbeat).
    source /usr/lib/openfoam/openfoam2506/etc/bashrc
    source .venv/bin/activate

Exemples
--------
# Cas rapide (1 pas de temps) pour smoke-test :
    python run_sim.py --case-dir /tmp/sim1 --lhgr 20000 --end-time 3600 \
                      --write-interval 1

# Cas plus complet avec géométrie modifiée et post-traitement ciblé :
    python run_sim.py --case-dir /tmp/sim2 --lhgr 25000 --end-time 3.15e7 \
                      --fuel-outer-radius 4.6 --analysis axial_T

# Sauter l'exécution (juste créer le cas) :
    python run_sim.py --case-dir /tmp/sim3 --no-run
"""

import argparse
import json
import sys

from tools.input_creator import OffbeatInputCreatorTool
from tools.offbeat_executor import OffbeatExecutorTool
from tools.data_processor import OffbeatDataProcessorTool


def _banner(title: str) -> None:
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Lance une simulation OFFBEAT (input -> exec -> post), sans LLM.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--case-dir", required=True,
                   help="Répertoire où créer et exécuter le cas, ex. /tmp/sim1")
    p.add_argument("--template", default="fuel_rod_1D_pwr",
                   help="Template des offbeat_skills/templates/")

    # Paramètres surchargeables (cf. PARAM_MAP de input_creator)
    p.add_argument("--lhgr", type=float, help="Puissance linéique [W/m] (linear_heat_rate)")
    p.add_argument("--end-time", help="endTime du controlDict [s]")
    p.add_argument("--delta-t", help="deltaT du controlDict [s]")
    p.add_argument("--write-interval", help="writeInterval du controlDict")
    p.add_argument("--enrichment", help="Enrichissement (solverDict)")
    p.add_argument("--fuel-outer-radius", help="rOuterFuel [mm] (rodDict)")
    p.add_argument("--fuel-height", help="heightFuel [mm] (rodDict)")
    p.add_argument("--clad-inner-radius", help="rInnerClad [mm] (rodDict)")
    p.add_argument("--clad-outer-radius", help="rOuterClad [mm] (rodDict)")
    p.add_argument("--n-cells-r-fuel", help="nCellsRFuel (rodDict)")

    # Contrôle du déroulé
    p.add_argument("--no-healing", action="store_true",
                   help="Désactive la boucle de self-healing de l'executor")
    p.add_argument("--no-run", action="store_true",
                   help="Crée le cas mais ne lance pas le solveur")
    p.add_argument("--no-post", action="store_true",
                   help="N'effectue pas le post-traitement")
    p.add_argument("--analysis", default="summary",
                   choices=["axial_T", "radial_T", "axial_stress", "peak_T", "summary"],
                   help="Type d'analyse du data_processor")

    args = p.parse_args(argv)

    # Construit le dict de paramètres pour input_creator (clés non nulles only)
    param_keys = {
        "linear_heat_rate": args.lhgr,
        "end_time": args.end_time,
        "delta_t": args.delta_t,
        "write_interval": args.write_interval,
        "enrichment": args.enrichment,
        "fuel_outer_radius": args.fuel_outer_radius,
        "fuel_height": args.fuel_height,
        "clad_inner_radius": args.clad_inner_radius,
        "clad_outer_radius": args.clad_outer_radius,
        "n_cells_r_fuel": args.n_cells_r_fuel,
    }
    params = {k: v for k, v in param_keys.items() if v is not None}

    # 1) INPUT CREATOR
    _banner("1/3  INPUT CREATOR")
    out = OffbeatInputCreatorTool()._run(
        case_dir=args.case_dir,
        template_name=args.template,
        params=json.dumps(params),
    )
    print(out)
    if out.startswith("ERREUR"):
        return 1

    # 2) OFFBEAT EXECUTOR
    if args.no_run:
        print("\n[--no-run] Solveur non lancé.")
        return 0
    _banner("2/3  OFFBEAT EXECUTOR (rodMaker + blockMesh + offbeat)")
    out = OffbeatExecutorTool()._run(
        case_dir=args.case_dir,
        self_healing=not args.no_healing,
    )
    print(out)
    success = "succès" in out
    if not success:
        print("\n[!] La simulation n'a pas abouti — post-traitement sauté.")
        return 2

    # 3) DATA PROCESSOR
    if args.no_post:
        return 0
    _banner("3/3  DATA PROCESSOR (pyvista)")
    out = OffbeatDataProcessorTool()._run(
        case_dir=args.case_dir,
        analysis=args.analysis,
        output_json=f"{args.case_dir}/results.json",
    )
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
