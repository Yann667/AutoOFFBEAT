"""
safety_analyzer.py – Analyse de surete d'un cas OFFBEAT (jumeau numerique, D1+D2).

Brique 1 du jumeau numerique (cf. GUIDE.md Partie 2). Compare les pics des
champs d'un crayon combustible (temperature, contraintes, deformation, gap,
oxydation) aux seuils de securite de offbeat_skills/safety_kb.json et attribue
un statut par critere :

    🟢 sur          (loin du seuil)
    🟡 vigilance    (au-dela de warning_fraction du seuil)
    🔴 franchi      (seuil atteint ou depasse)

Patron IDENTIQUE au self-healing de l'executor (cf. CLAUDE.md §6) :
  1. Deterministe d'abord : seuils lus dans safety_kb.json (JSON editable SANS
     rebuild, comme error_kb.json). Rapide, gratuit, fiable.
  2. Repli LLM (optionnel, llm_explain=True) : interpreter les criteres rouges
     en langage clair via le LLM de debug (jamais applique automatiquement).

Brique 2 (pronostic / prediction) : `prognose()` exploite les DIFFERENTS pas de
temps deja ecrits par le solveur pour extrapoler QUAND un critere atteindra sa
limite au rythme actuel. Purement predictif, base sur la tendance observee.

safety_kb.json = seuils INDICATIFS (validated:false) : ce sont des criteres de
conception publies (fusion UO2, ~1% de deformation gaine...), pas des correctifs
inventes, mais leurs valeurs exactes sont a confirmer avec l'encadrant.
"""

import json
from pathlib import Path
from typing import Type

import numpy as np
from pydantic import BaseModel, Field
from langchain_core.tools import BaseTool

from tools.data_processor import _load_foam  # lecteur foam/VTK deja eprouve

SAFETY_KB_PATH = Path(__file__).parent.parent / "offbeat_skills" / "safety_kb.json"

# Repli minimal si safety_kb.json est absent/invalide : l'analyse doit continuer
# a fonctionner sur le critere le plus critique (la fusion du combustible).
_FALLBACK_KB = [
    {
        "id": "fuel_centerline_melt", "field": "T", "component": None,
        "reduction": "max", "limit": 3113.0, "unit": "K", "direction": "below",
        "warning_fraction": 0.90,
        "criterion": "Temperature a coeur pastille sous la fusion UO2.",
        "diagnosis": "Marge a la fusion du combustible.",
    }
]


# --------------------------------------------------------------------------
# Lecture d'une valeur reduite d'un champ
# --------------------------------------------------------------------------

def _reduce(arr: np.ndarray, reduction: str) -> float:
    """Reduit un tableau de valeurs en un scalaire selon `reduction` :
        max     -> valeur maximale (T a coeur, contrainte de traction...)
        min     -> valeur minimale (gapWidth : le gap le plus ferme du domaine)
        max_abs -> valeur de plus grande amplitude (contrainte/deformation qui
                   peut etre compressive/negative)."""
    if reduction == "min":
        return float(np.min(arr))
    if reduction == "max_abs":
        return float(arr[np.argmax(np.abs(arr))])
    return float(np.max(arr))          # "max" par defaut


def _field_value(dataset, field: str, component, reduction: str):
    """Extrait la valeur reduite d'un champ sur tout le domaine maille.
    Retourne None si le champ est absent du cas. Pour un champ tensoriel,
    `component` selectionne la composante (ex. 1 = hoop theta-theta pour
    sigmaCyl/epsilonCyl en ordre VTK)."""
    mesh = dataset.combine()
    if field not in mesh.array_names:
        return None
    arr = np.asarray(mesh[field])
    if component is not None and arr.ndim == 2:
        arr = arr[:, component]
    return _reduce(arr, reduction)


# --------------------------------------------------------------------------
# Statut d'un critere : 🟢 / 🟡 / 🔴
# --------------------------------------------------------------------------

def _status(value: float, limit: float, direction: str,
            warning_fraction: float) -> tuple[str, float]:
    """Compare `value` a `limit` et retourne (emoji, ratio d'approche).
    ratio >= 1        -> seuil franchi (rouge)
    ratio >= warning  -> vigilance (jaune)
    sinon             -> sur (vert)

    direction 'below' : la valeur doit RESTER SOUS la limite (fusion, PCT,
      deformation) ; ratio = value / limit.
    direction 'above' : la valeur doit RESTER AU-DESSUS de la limite (gapWidth :
      danger si le gap devient trop petit) ; ratio = limit / value."""
    if direction == "above":
        ratio = limit / value if value != 0 else float("inf")
    else:
        ratio = value / limit if limit != 0 else float("inf")
    if ratio >= 1.0:
        return "🔴", ratio
    if ratio >= warning_fraction:
        return "🟡", ratio
    return "🟢", ratio


def _load_safety_kb() -> list:
    """Charge les seuils depuis safety_kb.json. Tolerant aux pannes : en cas
    d'absence/erreur, on retombe sur _FALLBACK_KB (l'analyse ne casse jamais)."""
    try:
        rules = json.loads(SAFETY_KB_PATH.read_text(encoding="utf-8"))
        return [r for r in rules if "field" in r and "limit" in r] or _FALLBACK_KB
    except Exception:
        return _FALLBACK_KB


# --------------------------------------------------------------------------
# Brique 2 (D2) : pronostic par extrapolation de tendance
# --------------------------------------------------------------------------

def _series(case_dir: Path, field: str, component, reduction: str):
    """Retourne (temps, valeurs) du champ reduit a CHAQUE pas de temps ecrit.
    C'est la matiere premiere du pronostic : l'evolution temporelle du pic."""
    import pyvista as pv

    foam = next(Path(case_dir).glob("*.foam"), None)
    if foam is None:
        foam = Path(case_dir) / f"{Path(case_dir).name}.foam"
        foam.touch()
    reader = pv.OpenFOAMReader(str(foam))
    times, values = [], []
    for t in reader.time_values:
        reader.set_active_time_value(t)
        v = _field_value(reader.read(), field, component, reduction)
        if v is not None:
            times.append(float(t))
            values.append(v)
    return times, values


def prognose(case_dir, rule: dict) -> str:
    """Extrapole QUAND le critere `rule` atteindra sa limite, d'apres la
    tendance lineaire des pas de temps deja simules. Renvoie un message clair.
    Ne s'engage pas si la tendance n'evolue pas VERS la limite."""
    field = rule["field"]
    comp = rule.get("component")
    reduction = rule.get("reduction", "max")
    limit = float(rule["limit"])
    direction = rule.get("direction", "below")
    try:
        times, values = _series(Path(case_dir), field, comp, reduction)
    except Exception as exc:                      # jamais casser l'appelant
        return f"pronostic indisponible ({exc})"
    if len(times) < 2:
        return "pronostic impossible (moins de 2 pas de temps ecrits)"

    a, b = np.polyfit(times, values, 1)           # value ~ a*t + b
    if abs(a) < 1e-30:
        return "tendance plate : pas de franchissement prevu au rythme actuel"

    # Vers la limite ? 'below' -> danger si ca MONTE ; 'above' -> si ca DESCEND.
    moving_toward = (a > 0) if direction == "below" else (a < 0)
    if not moving_toward:
        return ("tendance qui s'eloigne de la limite : "
                "pas de franchissement prevu au rythme actuel")

    t_cross = (limit - b) / a
    t_last = times[-1]
    if t_cross <= t_last:
        return f"limite deja atteinte autour de t = {t_cross:.3g} s"
    dt = t_cross - t_last
    return (f"au rythme actuel, limite ({limit:g} {rule.get('unit','')}) "
            f"atteinte vers t = {t_cross:.3g} s "
            f"(dans ~{dt:.3g} s apres le dernier pas simule)")


# --------------------------------------------------------------------------
# Coeur d'analyse (structurE) : reutilisE par l'outil ET le tableau de bord D4
# --------------------------------------------------------------------------

def analyze(case_dir, time_step: str = "latestTime",
            prognosis: bool = True) -> dict:
    """Evalue tous les criteres de safety_kb.json sur un cas et retourne un
    resultat STRUCTURE (dict), directement exploitable par une interface :

        {"case_dir", "overall": "🟢|🟡|🔴",
         "criteria": [{"id","status","value","limit","unit","ratio",
                       "prognosis","criterion","diagnosis"}, ...]}

    Peut lever une exception de lecture (le caller decide quoi en faire)."""
    case = Path(case_dir)
    dataset = _load_foam(case, time_step)
    criteria, worst = [], "🟢"

    for rule in _load_safety_kb():
        unit = rule.get("unit", "")
        value = _field_value(dataset, rule["field"], rule.get("component"),
                             rule.get("reduction", "max"))
        if value is None:
            criteria.append({
                "id": rule["id"], "status": "⚪", "value": None,
                "limit": float(rule["limit"]), "unit": unit, "ratio": None,
                "prognosis": "", "criterion": rule.get("criterion", ""),
                "diagnosis": rule.get("diagnosis", ""),
                "note": f"champ '{rule['field']}' absent du cas"})
            continue

        emoji, ratio = _status(value, float(rule["limit"]),
                               rule.get("direction", "below"),
                               float(rule.get("warning_fraction", 0.9)))
        if emoji == "🔴" or (emoji == "🟡" and worst != "🔴"):
            worst = emoji
        prog = prognose(case, rule) if (prognosis and emoji != "🟢") else ""
        criteria.append({
            "id": rule["id"], "status": emoji, "value": value,
            "limit": float(rule["limit"]), "unit": unit,
            "ratio": round(ratio, 3), "prognosis": prog,
            "criterion": rule.get("criterion", ""),
            "diagnosis": rule.get("diagnosis", "")})

    return {"case_dir": str(case_dir), "overall": worst, "criteria": criteria}


# --------------------------------------------------------------------------
# Outil LangChain (D1)
# --------------------------------------------------------------------------

class SafetyAnalyzerInput(BaseModel):
    case_dir: str = Field(
        description="Chemin absolu d'un cas OFFBEAT terminE (crayon combustible)."
    )
    time_step: str = Field(
        default="latestTime",
        description="Pas de temps a analyser, ex. '3600' ou 'latestTime'.",
    )
    prognosis: bool = Field(
        default=True,
        description="Si True, extrapole pour chaque critere non-vert QUAND la "
                    "limite sera atteinte (necessite plusieurs pas de temps).",
    )
    llm_explain: bool = Field(
        default=False,
        description="Si True, demande au LLM de debug une interpretation en "
                    "langage clair des criteres rouges (plus lent).",
    )
    output_json: str = Field(
        default="",
        description="Si renseigne, chemin d'un JSON ou sauvegarder le rapport.",
    )


class OffbeatSafetyAnalyzerTool(BaseTool):
    """Evalue les marges de surete d'un crayon combustible simule."""

    name: str = "safety_analyzer"
    description: str = (
        "Analyse la surete d'un cas OFFBEAT terminE (crayon combustible) : "
        "compare les pics de temperature, contrainte/deformation de gaine, "
        "largeur de gap et oxydation aux seuils de conception "
        "(offbeat_skills/safety_kb.json) et retourne un statut 🟢/🟡/🔴 par "
        "critere. Peut aussi PREDIRE quand un seuil sera franchi (pronostic par "
        "extrapolation de tendance). A utiliser apres une simulation pour "
        "detecter une situation de danger (fusion combustible, PCMI, rupture "
        "de gaine)."
    )
    args_schema: Type[BaseModel] = SafetyAnalyzerInput

    def _run(self, case_dir: str, time_step: str = "latestTime",
             prognosis: bool = True, llm_explain: bool = False,
             output_json: str = "") -> str:
        if not Path(case_dir).exists():
            return f"ERREUR : repertoire '{case_dir}' introuvable."
        try:
            report = analyze(case_dir, time_step, prognosis)
        except ImportError as exc:
            return str(exc)
        except Exception as exc:  # noqa: BLE001
            return f"ERREUR lecture foam : {exc}"

        worst = report["overall"]
        lines, red_rules = [], []
        for c in report["criteria"]:
            if c["status"] == "⚪":
                lines.append(f"⚪ {c['id']} : {c.get('note', 'non evaluable')}")
                continue
            line = (f"{c['status']} {c['id']} : {c['value']:.4g} {c['unit']} "
                    f"(limite {c['limit']:.4g} {c['unit']}, "
                    f"{c['ratio']*100:.0f}% du seuil)")
            if c["prognosis"]:
                line += "\n  → " + c["prognosis"]
            if c["status"] == "🔴":
                red_rules.append(c)
            lines.append(line)

        header = {"🔴": "🔴 DANGER : au moins un critere de surete franchi.",
                  "🟡": "🟡 VIGILANCE : un critere approche de sa limite.",
                  "🟢": "🟢 SUR : tous les criteres evalues sont dans les marges."}[worst]
        out = [header, ""] + lines

        # Repli LLM (optionnel) : interpreter les criteres rouges en clair.
        if llm_explain and red_rules:
            out.append("\n--- Interpretation (LLM) ---")
            out.append(self._llm_explain(case_dir, red_rules))

        if output_json:
            try:
                Path(output_json).write_text(
                    json.dumps(report, indent=2, ensure_ascii=False),
                    encoding="utf-8")
                out.append(f"\nRapport sauvegarde dans {output_json}")
            except OSError as exc:
                out.append(f"\n(impossible d'ecrire {output_json} : {exc})")

        return "\n".join(out)

    def _llm_explain(self, case_dir: str, red_rules: list) -> str:
        """Interpretation LLM des criteres rouges. Robuste : ne casse jamais."""
        try:
            from config.llm_factory import get_debug_llm
            crit = "\n".join(f"- {r['id']} : {r.get('diagnosis','')}"
                             for r in red_rules)
            resp = get_debug_llm().invoke(
                "Tu es un expert en surete du combustible nucleaire. Un cas "
                f"OFFBEAT ({case_dir}) a franchi ces criteres de surete :\n{crit}\n"
                "Explique en 3-4 phrases claires le risque physique et la "
                "premiere action a envisager (sans inventer de valeurs)."
            )
            return resp.content
        except Exception as exc:  # noqa: BLE001
            return f"(interpretation LLM indisponible : {exc})"


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Analyse de surete D1/D2 (crayon).")
    ap.add_argument("case_dir")
    ap.add_argument("--time-step", default="latestTime")
    ap.add_argument("--no-prognosis", action="store_true")
    ap.add_argument("--llm", action="store_true", help="interpretation LLM des rouges")
    ap.add_argument("--json", default="", help="chemin de sortie JSON")
    a = ap.parse_args()
    print(OffbeatSafetyAnalyzerTool()._run(
        a.case_dir, time_step=a.time_step, prognosis=not a.no_prognosis,
        llm_explain=a.llm, output_json=a.json))
