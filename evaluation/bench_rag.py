"""
bench_rag.py : L'assistant documentaire retrouve-t-il le bon document ?

L'assistant documentaire (recherche semantique) est le seul composant du
systeme qui n'etait etaye par aucune mesure. Ce banc evalue la brique qui
determine tout le reste : la RECHERCHE. Si le mauvais extrait remonte, la
reponse sera fausse quelle que soit la qualite du modele de langage.

Protocole : a chaque question est associe le document du corpus qui contient
reellement la reponse. On interroge le retriever et on mesure

  - exactitude@1 : le bon document est-il celui du premier extrait ?
  - rappel@k     : le bon document apparait-il parmi les k extraits remontes ?

Le second indicateur est le plus pertinent en pratique : l'agent recoit les k
extraits, il lui suffit que le bon s'y trouve.

LIMITE ASSUMEE : le corpus ne compte que quatre documents. Un tirage au hasard
donnerait deja environ 25 % d'exactitude@1. Les chiffres doivent se lire comme
une verification de non-regression sur un petit corpus, pas comme une mesure de
passage a l'echelle.

Usage (depuis la racine du depot) :
    python -m evaluation.bench_rag
"""

import argparse
import json
from collections import defaultdict
from pathlib import Path

# (question, document du corpus contenant la reponse)
QUESTIONS = [
    # --- structure d'un cas -------------------------------------------------
    ("Que contient le répertoire 0 d'un cas OFFBEAT ?",
     "offbeat_case_structure.md"),
    ("Où sont stockées les conditions aux limites de température ?",
     "offbeat_case_structure.md"),
    ("À quoi sert le fichier solverDict ?", "offbeat_case_structure.md"),
    ("Quel répertoire contient les informations de maillage ?",
     "offbeat_case_structure.md"),
    ("Où trouve-t-on la composition initiale du gaz du jeu ?",
     "offbeat_case_structure.md"),
    ("Que contient le dossier system d'un cas ?",
     "offbeat_case_structure.md"),

    # --- commandes ----------------------------------------------------------
    ("Quelle commande construit le maillage de calcul ?",
     "offbeat_commands.md"),
    ("Comment lance-t-on le solveur OFFBEAT ?", "offbeat_commands.md"),
    ("Quel dictionnaire est lu par blockMesh ?", "offbeat_commands.md"),
    ("À quoi sert la commande changeDictionary ?", "offbeat_commands.md"),
    ("Dans quel ordre exécuter les commandes pour un cas OFFBEAT ?",
     "offbeat_commands.md"),

    # --- self-healing / ajout d'une erreur ----------------------------------
    ("Comment ajouter une nouvelle erreur à la base de self-healing ?",
     "HOWTO_ajouter_une_erreur.md"),
    ("Comment provoquer volontairement un crash du solveur ?",
     "HOWTO_ajouter_une_erreur.md"),
    ("Comment extraire un motif regex stable depuis un log de plantage ?",
     "HOWTO_ajouter_une_erreur.md"),
    ("Comment identifier la signature réelle d'une erreur dans le log ?",
     "HOWTO_ajouter_une_erreur.md"),
]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-k", type=int, default=4, help="nombre d'extraits remontes")
    ap.add_argument("--json", default="evaluation/resultats_rag.json")
    args = ap.parse_args()

    from tools.rag_retriever import get_retriever

    retriever = get_retriever(k=args.k)
    resultats = []

    for i, (question, attendu) in enumerate(QUESTIONS, 1):
        try:
            extraits = retriever.invoke(question)
            sources = [Path(d.metadata.get("source", "?")).name for d in extraits]
            err = None
        except Exception as exc:  # noqa: BLE001
            sources, err = [], str(exc)[:150]

        top1 = bool(sources) and sources[0] == attendu
        dans_k = attendu in sources
        resultats.append({"question": question, "attendu": attendu,
                          "sources": sources, "exact_top1": top1,
                          "rappel_k": dans_k, "erreur": err})
        etat = "OK " if top1 else ("~  " if dans_k else "NON")
        print(f"[{i:2d}/{len(QUESTIONS)}] {etat} attendu={attendu:32s} "
              f"top1={(sources[0] if sources else '-')}")
        if err:
            print(f"          erreur : {err}")

    n = len(resultats)
    t1 = sum(r["exact_top1"] for r in resultats)
    rk = sum(r["rappel_k"] for r in resultats)

    par_doc = defaultdict(lambda: [0, 0, 0])
    for r in resultats:
        d = par_doc[r["attendu"]]
        d[2] += 1
        d[0] += r["exact_top1"]
        d[1] += r["rappel_k"]

    print("\n" + "=" * 70)
    print(f"{'Document attendu':<36}{'top-1':>10}{'rappel@k':>12}")
    print("-" * 70)
    for doc, (a, b, tot) in par_doc.items():
        print(f"{doc:<36}{a:>5}/{tot:<4}{b:>7}/{tot:<4}")
    print("=" * 70)
    print(f"Exactitude@1 : {t1}/{n} = {100*t1/n:.0f} %")
    print(f"Rappel@{args.k}     : {rk}/{n} = {100*rk/n:.0f} %")
    print(f"(corpus de {len(par_doc)} documents : un tirage au hasard donnerait "
          f"~{100/len(par_doc):.0f} % en top-1)")

    out = Path(args.json)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(resultats, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    print(f"\n-> {out}")


if __name__ == "__main__":
    main()
