"""
capture_session.py — Capture d'une session complete de l'agent, de bout en bout.

Le rapport decrit l'architecture de l'agent, mesure ses limites et valide sa
physique, mais ne montre nulle part l'agent EN TRAIN DE FONCTIONNER. Ce script
enregistre une session reelle — demande en francais, enchainement d'outils
effectivement declenche, resultats intermediaires, reponse finale — pour servir
d'exemple deroule en annexe.

Contrairement au banc de selection d'outils, les outils sont ICI REELLEMENT
EXECUTES : un cas est cree, le solveur tourne, les resultats sont analyses.
C'est une demonstration, pas une mesure.

Usage (depuis la racine, OpenFOAM sourcE) :
    python -m evaluation.capture_session
    python -m evaluation.capture_session --demande "..." --case-dir /tmp/demo
"""

import argparse
import json
import time
from pathlib import Path

DEMANDE = (
    "Crée un cas de crayon combustible REP dans {case_dir} avec une puissance "
    "linéique de 22 kW/m sur environ un an, lance la simulation, puis dis-moi "
    "si les marges de sûreté sont respectées."
)


def _resume(contenu: str, n: int = 400) -> str:
    """Tronque proprement un contenu long pour la transcription."""
    contenu = (contenu or "").strip()
    if len(contenu) <= n:
        return contenu
    return contenu[:n].rstrip() + f"\n[... {len(contenu) - n} caracteres tronques ...]"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--case-dir", default="/tmp/demo_agent")
    ap.add_argument("--demande", default="")
    ap.add_argument("--json", default="evaluation/session_capturee.json")
    ap.add_argument("--txt", default="evaluation/session_capturee.txt")
    args = ap.parse_args()

    demande = args.demande or DEMANDE.format(case_dir=args.case_dir)

    from agents.supervisor import build_supervisor

    print("Construction du superviseur...", flush=True)
    agent = build_supervisor()
    config = {"configurable": {"thread_id": "capture-annexe"}}

    print(f"\nDEMANDE : {demande}\n" + "=" * 72, flush=True)
    t0 = time.time()
    resultat = agent.invoke({"messages": [{"role": "user", "content": demande}]},
                            config)
    duree = time.time() - t0

    messages = resultat.get("messages", [])

    # --- Reconstruction lisible de la sequence -----------------------------
    etapes, n_appels = [], 0
    for m in messages:
        role = m.__class__.__name__
        appels = list(getattr(m, "tool_calls", None) or [])
        if role == "HumanMessage":
            etapes.append({"type": "demande", "contenu": m.content})
        elif appels:
            n_appels += len(appels)
            for c in appels:
                etapes.append({"type": "appel_outil", "outil": c["name"],
                               "arguments": c.get("args", {})})
        elif role == "ToolMessage":
            etapes.append({"type": "resultat_outil",
                           "outil": getattr(m, "name", "?"),
                           "contenu": m.content})
        elif role == "AIMessage" and (m.content or "").strip():
            etapes.append({"type": "reponse", "contenu": m.content})

    # --- Transcription texte ----------------------------------------------
    lignes = [f"DEMANDE\n{demande}\n"]
    for e in etapes:
        if e["type"] == "demande":
            continue
        if e["type"] == "appel_outil":
            args_str = ", ".join(f"{k}={v!r}" for k, v in e["arguments"].items())
            lignes.append(f"--> APPEL   {e['outil']}({_resume(args_str, 200)})")
        elif e["type"] == "resultat_outil":
            lignes.append(f"<-- RESULTAT {e['outil']}\n{_resume(e['contenu'], 500)}\n")
        else:
            lignes.append(f"REPONSE DE L'AGENT\n{e['contenu']}\n")
    transcription = "\n".join(lignes)

    print(transcription)
    print("=" * 72)
    print(f"outils appeles : {n_appels} | messages : {len(messages)} "
          f"| duree totale : {duree:.1f} s")

    sortie = {"demande": demande, "duree_s": round(duree, 1),
              "nb_appels_outils": n_appels, "etapes": etapes}
    Path(args.json).write_text(json.dumps(sortie, indent=2, ensure_ascii=False),
                               encoding="utf-8")
    Path(args.txt).write_text(transcription, encoding="utf-8")
    print(f"\n-> {args.json}\n-> {args.txt}")


if __name__ == "__main__":
    main()
