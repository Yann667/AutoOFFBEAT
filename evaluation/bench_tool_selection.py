"""
bench_tool_selection.py : Le superviseur choisit-il le bon outil ?

Le rapport mesure la chaine physique (solveur, emulateur) et la boucle de
reparation, mais RIEN ne mesurait jusqu'ici la couche LLM elle-meme : face a
une demande en francais, l'agent appelle-t-il l'outil pertinent ?

Methode : on n'execute PAS les outils. On lie les outils au modele
(`bind_tools`) et on lit l'intention, la liste `tool_calls` produite. Cela
mesure exactement la selection, sans effet de bord (aucun cas cree, aucun
solveur lance) et en une seule passe par requete.

Ce banc n'etait pas praticable avant l'accelaration GPU : a ~4 min par
invocation sur processeur, 26 requetes demandaient pres de 2 h. A ~0,3 s elles
tiennent en moins d'une minute.

Usage (depuis la racine du depot) :
    python -m evaluation.bench_tool_selection
"""

import argparse
import json
import time
from collections import defaultdict
from pathlib import Path

from config.llm_factory import get_supervisor_llm

# --------------------------------------------------------------------------
# Jeu de requetes. 'attendu' = None signifie « aucun outil ne doit etre
# appele » (question generale, salutation) : repondre directement est la
# bonne conduite, declencher un outil est un faux positif.
# --------------------------------------------------------------------------
REQUETES = [
    # --- creation de cas ---------------------------------------------------
    ("Crée un cas de crayon combustible dans /tmp/essai1 avec une puissance "
     "linéique de 25000 W/m", "input_creator"),
    ("Prépare-moi une simulation de barreau REP à 20 kW/m sur 2 ans dans "
     "/tmp/essai2", "input_creator"),
    ("Génère un cas avec un rayon de pastille de 4.2 mm dans /tmp/essai3",
     "input_creator"),
    ("Fabrique un nouveau cas OFFBEAT dans /tmp/essai4 à partir du template "
     "fuel_rod_1D_pwr", "input_creator"),

    # --- execution ---------------------------------------------------------
    ("Lance la simulation du cas /tmp/essai1", "offbeat_executor"),
    ("Exécute le solveur OFFBEAT sur /tmp/essai2", "offbeat_executor"),
    ("Fais tourner le calcul dans /tmp/essai3 et répare automatiquement si "
     "ça plante", "offbeat_executor"),
    ("Relance /tmp/essai4, le run précédent a échoué", "offbeat_executor"),

    # --- post-traitement ---------------------------------------------------
    ("Trace le profil axial de température du cas /tmp/essai1",
     "data_processor"),
    ("Quelle est la température maximale atteinte dans /tmp/essai2 ?",
     "data_processor"),
    ("Post-traite les résultats de /tmp/essai3 et donne-moi un résumé",
     "data_processor"),
    ("Montre-moi le profil radial de température au plan médian de /tmp/essai1",
     "data_processor"),

    # --- analyse de surete -------------------------------------------------
    ("Est-ce que le cas /tmp/essai1 respecte les marges de sûreté ?",
     "safety_analyzer"),
    ("Vérifie si un critère de sûreté est franchi dans /tmp/essai2",
     "safety_analyzer"),
    ("Y a-t-il un risque de fusion du combustible dans /tmp/essai3 ?",
     "safety_analyzer"),
    ("Analyse les marges PCMI du cas /tmp/essai4 et dis-moi quand la limite "
     "sera atteinte", "safety_analyzer"),

    # --- emulateur (prediction rapide, sans solveur) -----------------------
    ("Sans relancer le solveur, que donnerait un crayon à 30 kW/m après "
     "2 ans ?", "surrogate_predict"),
    ("Estime rapidement la température maximale pour 22 kW/m sur 1 an",
     "surrogate_predict"),
    ("Prédis instantanément les marges de sûreté à 28 kW/m et 5e7 secondes",
     "surrogate_predict"),

    # --- documentation -----------------------------------------------------
    ("Comment est structuré un cas OFFBEAT ?", "offbeat_knowledge"),
    ("Que dit la documentation OFFBEAT sur le modèle de gap ?",
     "offbeat_knowledge"),
    ("Cherche dans la doc comment définir les conditions aux limites",
     "offbeat_knowledge"),

    # --- aucun outil attendu -----------------------------------------------
    ("Bonjour, qui es-tu ?", None),
    ("Qu'est-ce que le PCMI en physique du combustible ?", None),
    ("Merci pour ton aide", None),
    ("Explique-moi en deux phrases la différence entre un jumeau numérique "
     "et une simulation classique", None),
]


def evaluer(llm_avec_outils, requete: str) -> dict:
    """Une passe unique : on lit l'INTENTION, on n'execute rien."""
    t0 = time.time()
    try:
        rep = llm_avec_outils.invoke(requete)
        appels = [c["name"] for c in (rep.tool_calls or [])]
        err = None
    except Exception as exc:  # noqa: BLE001
        appels, err = [], str(exc)[:200]
    return {"appels": appels, "latence_s": round(time.time() - t0, 2),
            "erreur": err}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", default="evaluation/resultats_tool_selection.json")
    args = ap.parse_args()

    # On reconstruit la meme liste d'outils que le superviseur.
    from tools.input_creator import OffbeatInputCreatorTool
    from tools.offbeat_executor import OffbeatExecutorTool
    from tools.data_processor import OffbeatDataProcessorTool
    from tools.safety_analyzer import OffbeatSafetyAnalyzerTool

    outils = [OffbeatInputCreatorTool(), OffbeatExecutorTool(),
              OffbeatDataProcessorTool(), OffbeatSafetyAnalyzerTool()]
    try:
        from tools.rag_retriever import get_knowledge_tool
        outils.append(get_knowledge_tool())
    except Exception:
        pass
    try:
        from tools.surrogate import OffbeatSurrogateTool, MODEL_PATH
        if MODEL_PATH.exists():
            outils.append(OffbeatSurrogateTool())
    except Exception:
        pass

    print(f"Outils exposés : {[o.name for o in outils]}\n")
    llm = get_supervisor_llm().bind_tools(outils)

    resultats = []
    for i, (req, attendu) in enumerate(REQUETES, 1):
        r = evaluer(llm, req)
        appels = r["appels"]
        premier = appels[0] if appels else None
        # Correct = le bon outil est appele en PREMIER (ou aucun appel quand
        # aucun n'est attendu). On note separement le cas ou le bon outil est
        # appele mais pas en tete : l'intention est bonne, l'ordre non.
        correct = (premier == attendu)
        present = (attendu in appels) if attendu else (not appels)
        resultats.append({"requete": req, "attendu": attendu,
                          "appels": appels, "correct": correct,
                          "attendu_present": present, **r})
        etat = "OK " if correct else "NON"
        print(f"[{i:2d}/{len(REQUETES)}] {etat} attendu={str(attendu):18s} "
              f"obtenu={str(premier):18s} ({r['latence_s']}s)")
        if r["erreur"]:
            print(f"          erreur: {r['erreur']}")

    # --- synthese ----------------------------------------------------------
    n = len(resultats)
    ok = sum(1 for r in resultats if r["correct"])
    okp = sum(1 for r in resultats if r["attendu_present"])
    lat = sum(r["latence_s"] for r in resultats) / n

    par_outil = defaultdict(lambda: [0, 0])
    for r in resultats:
        cle = r["attendu"] or "(aucun outil)"
        par_outil[cle][1] += 1
        if r["correct"]:
            par_outil[cle][0] += 1

    print("\n" + "=" * 62)
    print(f"{'Outil attendu':<24}{'correct':>12}{'taux':>10}")
    print("-" * 62)
    for cle, (bon, tot) in par_outil.items():
        print(f"{cle:<24}{bon:>7}/{tot:<4}{100*bon/tot:>9.0f}%")
    print("=" * 62)
    print(f"Exactitude (bon outil en premier)  : {ok}/{n} = {100*ok/n:.0f}%")
    print(f"Bon outil present dans les appels  : {okp}/{n} = {100*okp/n:.0f}%")
    print(f"Latence moyenne par requete        : {lat:.2f} s")

    out = Path(args.json)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(resultats, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    print(f"\nResultats detailles -> {out}")


if __name__ == "__main__":
    main()
