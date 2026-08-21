"""
safety_analyzer.py : Analyse de surete d'un cas OFFBEAT (jumeau numerique, D1+D2).

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

# cellZones OFFBEAT (definies par blockNameFuel/blockNameClad dans rodDict,
# cf. offbeat_skills/templates/fuel_rod_1D_pwr/rodDict) : permet de restreindre
# un critere a la gaine ou a la pastille plutot que tout le domaine.
CLADDING_ZONE = "cladding"

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


def _find_zone_block(dataset, zone_name: str):
    """Cherche recursivement un bloc nomme `zone_name` (cellZone OpenFOAM,
    ex. 'cladding') dans le MultiBlock retourne par le reader pyvista.
    Retourne le bloc combine (UnstructuredGrid) ou None si absent (ex. reader
    non configure avec read_zones=True, ou zone inexistante dans ce cas)."""
    try:
        n = len(dataset)
    except TypeError:
        return None
    for i in range(n):
        name = dataset.get_block_name(i)
        block = dataset[i]
        if block is None:
            continue
        if name is not None and name.strip().lower() == zone_name.lower():
            return block.combine() if hasattr(block, "combine") else block
        if hasattr(block, "get_block_name"):  # sous-MultiBlock : descendre
            found = _find_zone_block(block, zone_name)
            if found is not None:
                return found
    return None


def _candidate_meshes(dataset, zone: str = None):
    """Maillages a interroger, par ordre de preference.

    `dataset.combine()` ne peut pas servir de repli universel : sur un cas
    MULTI-REGIONS (cas de verification d'OFFBEAT, qui embarquent un maillage de
    reference a cote du maillage de calcul), il fusionne aussi les blocs
    depourvus de donnees et le resultat perd TOUS les champs. Le critere serait
    alors rapporte comme « champ absent » alors que la donnee existe.

    Ordre retenu : la cellZone demandee, PUIS la fusion, PUIS les blocs
    'internalMesh'. Cet ordre est important et a ete etabli empiriquement :
    mettre 'internalMesh' avant la fusion casse la lecture de `gapWidth`, dont
    les valeurs utiles vivent sur les patches de frontiere (le volume porte une
    valeur sentinelle de 1e15). Le repli 'internalMesh' n'intervient donc que
    lorsque la fusion ne porte pas le champ, exactement le cas multi-regions,
    ce qui rend ce correctif purement additif."""
    out = []
    if zone:
        z = _find_zone_block(dataset, zone)
        if z is not None:
            out.append(z)
    try:
        out.append(dataset.combine())
    except Exception:  # noqa: BLE001
        pass

    def walk(mb):
        try:
            n = len(mb)
        except TypeError:
            return
        for i in range(n):
            b = mb[i]
            if b is None:
                continue
            nom = (mb.get_block_name(i) or "").strip().lower()
            if hasattr(b, "get_block_name"):
                walk(b)
            elif nom == "internalmesh":
                out.append(b)

    walk(dataset)
    return out


def _extract(mesh, field: str, component):
    """Tableau 1D d'un champ sur un maillage donne, composante selectionnee."""
    arr = np.asarray(mesh[field])
    if component is not None and arr.ndim == 2:
        arr = arr[:, component]
    return arr


def _pick_mesh(dataset, champs, zone: str = None):
    """Premier maillage candidat portant TOUS les champs demandes.

    Exiger un maillage unique est indispensable des lors qu'on compare deux
    champs point par point : deux blocs differents n'ont aucune raison de
    partager l'ordre des cellules, et le rapport calcule serait alors un
    appariement arbitraire."""
    for mesh in _candidate_meshes(dataset, zone):
        if all(c in mesh.array_names for c in champs):
            return mesh
    return None


def _evaluate_rule(dataset, rule: dict):
    """Evalue un critere et retourne (valeur, limite, ratio) ou None.

    Deux modes :

    - **limite scalaire** (`limit`) : comportement historique, on reduit le
      champ puis on compare a la constante.
    - **limite en CHAMP** (`limit_field`) : la limite est elle-meme un champ
      calcule par le solveur, typiquement `sigmaY`, la limite d'ecoulement,
      qui depend de la temperature, de l'irradiation et du burnup selon le
      modele materiau configure. Le rapport est alors calcule POINT PAR POINT
      puis reduit, ce qui est la seule facon correcte de proceder : le pic de
      contrainte et le minimum de limite ne sont pas au meme endroit.

    Le second mode garantit en outre que le critere de surete evalue la meme
    limite que celle effectivement appliquee par la simulation : l'ecart entre
    les deux etait de 100 MPa avant cette correction."""
    field = rule["field"]
    comp = rule.get("component")
    reduction = rule.get("reduction", "max")
    direction = rule.get("direction", "below")
    zone = rule.get("zone")
    limit_field = rule.get("limit_field")

    if not limit_field:
        val = _field_value(dataset, field, comp, reduction, zone)
        if val is None:
            return None
        lim = float(rule["limit"])
        _, ratio = _status(val, lim, direction,
                           float(rule.get("warning_fraction", 0.9)))
        return val, lim, ratio

    mesh = _pick_mesh(dataset, [field, limit_field], zone)
    if mesh is None:
        # Champ de limite indisponible : repli sur la limite scalaire si le
        # critere en fournit une, sinon critere non evaluable.
        if "limit" in rule:
            return _evaluate_rule(dataset, {**rule, "limit_field": None})
        return None

    v = _extract(mesh, field, comp).astype(float)
    L = _extract(mesh, limit_field, rule.get("limit_component")).astype(float)

    with np.errstate(divide="ignore", invalid="ignore"):
        if direction == "above":
            r = np.where(v > 0, L / np.where(v > 0, v, 1.0), _RATIO_OVERFLOW)
        else:
            num = np.abs(v) if reduction == "max_abs" else v
            r = np.where(L != 0, num / np.where(L != 0, L, 1.0), _RATIO_OVERFLOW)
    r = np.nan_to_num(r, nan=0.0, posinf=_RATIO_OVERFLOW, neginf=0.0)

    i = int(np.argmax(r))
    return float(v[i]), float(L[i]), float(r[i])


def _field_value(dataset, field: str, component, reduction: str, zone: str = None):
    """Extrait la valeur reduite d'un champ. Par defaut sur TOUT le domaine
    maille ; si `zone` est fourni (ex. 'cladding'), restreint aux cellules de
    cette cellZone si le reader l'a exposee (cf. _load_foam_zoned), sinon
    retombe silencieusement sur tout le domaine (proxy conservatif, comme
    avant). Retourne None si le champ est absent du cas. Pour un champ
    tensoriel, `component` selectionne la composante (ex. 1 = hoop
    theta-theta pour sigmaCyl/epsilonCyl en ordre VTK)."""
    for mesh in _candidate_meshes(dataset, zone):
        if field in mesh.array_names:
            arr = np.asarray(mesh[field])
            if component is not None and arr.ndim == 2:
                arr = arr[:, component]
            return _reduce(arr, reduction)
    return None


# --------------------------------------------------------------------------
# Statut d'un critere : 🟢 / 🟡 / 🔴
# --------------------------------------------------------------------------

_RATIO_OVERFLOW = 999.0  # toujours >= 1 (rouge), fini, JSON-safe


def _status(value: float, limit: float, direction: str,
            warning_fraction: float) -> tuple[str, float]:
    """Compare `value` a `limit` et retourne (emoji, ratio d'approche).
    ratio >= 1        -> seuil franchi (rouge)
    ratio >= warning  -> vigilance (jaune)
    sinon             -> sur (vert)

    direction 'below' : la valeur doit RESTER SOUS la limite (fusion, PCT,
      deformation) ; ratio = value / limit.
    direction 'above' : la valeur doit RESTER AU-DESSUS de la limite (gapWidth :
      danger si le gap devient trop petit) ; ratio = limit / value SI value > 0.
      Si value <= 0 (gap ferme, contact/interference : cas reel observe sur un
      run a haut burnup), limit/value donnerait un ratio NEGATIF (ex. -0.6),
      lu comme "sur" par erreur alors que c'est le pire cas possible (au-dela
      de la fermeture totale) -> force un ratio "depassement" (toujours rouge).
      Sentinelle FINIE (pas float('inf')) : Infinity n'est pas du JSON valide
      (JSON.parse() cote navigateur/API plante dessus, meme si le module
      json de Python l'accepte en interne sans broncher)."""
    if direction == "above":
        ratio = limit / value if value > 0 else _RATIO_OVERFLOW
    else:
        ratio = value / limit if limit != 0 else _RATIO_OVERFLOW
    if ratio >= 1.0:
        return "🔴", ratio
    if ratio >= warning_fraction:
        return "🟡", ratio
    return "🟢", ratio


def _load_foam_zoned(case_dir: Path, time_step: str = "latestTime"):
    """Comme data_processor._load_foam, mais active la lecture des cellZones
    OpenFOAM (fuel/cladding, cf. rodDict) pour permettre les criteres restreints
    a la gaine (cladding_hoop_strain_pcmi, cladding_hoop_stress). Duplique par
    prudence la petite logique de data_processor._load_foam au lieu de la
    reutiliser : ses lecteurs n'exposent read_zones qu'AVANT le .read(), donc on
    ne peut pas se greffer sur un dataset deja lu. Ne touche pas au reader
    partage par les autres outils (aucune regression possible sur eux)."""
    import pyvista as pv

    case_dir = Path(case_dir)
    foam_file = next(case_dir.glob("*.foam"), None)
    if foam_file is None:
        foam_file = case_dir / f"{case_dir.name}.foam"
        foam_file.touch()

    reader = pv.OpenFOAMReader(str(foam_file))
    # Sans ces deux flags VTK, les cellZones ne sont ni lues, ni peuplees en
    # donnees de champ (CopyDataToCellZones = False par defaut) : _find_zone_block
    # ne trouverait jamais 'cladding' et on retomberait silencieusement sur le
    # proxy tout-domaine (comportement inchange, jamais pire qu'avant).
    reader.reader.SetReadZones(True)
    reader.reader.SetCopyDataToCellZones(True)

    if time_step == "latestTime":
        reader.set_active_time_value(reader.time_values[-1])
    else:
        reader.set_active_time_value(float(time_step))

    return reader.read()


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

def _series(case_dir: Path, field: str, component, reduction: str, zone: str = None):
    """Retourne (temps, valeurs) du champ reduit a CHAQUE pas de temps ecrit.
    C'est la matiere premiere du pronostic : l'evolution temporelle du pic."""
    import pyvista as pv

    foam = next(Path(case_dir).glob("*.foam"), None)
    if foam is None:
        foam = Path(case_dir) / f"{Path(case_dir).name}.foam"
        foam.touch()
    reader = pv.OpenFOAMReader(str(foam))
    if zone:
        reader.reader.SetReadZones(True)
        reader.reader.SetCopyDataToCellZones(True)
    times, values = [], []
    for t in reader.time_values:
        reader.set_active_time_value(t)
        v = _field_value(reader.read(), field, component, reduction, zone)
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
    zone = rule.get("zone")
    try:
        times, values = _series(Path(case_dir), field, comp, reduction, zone)
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
    rules = _load_safety_kb()
    # Le reader "zone" n'est utilise que si au moins un critere le demande
    # (evite le cout de lecture des cellZones pour les cas courants).
    needs_zones = any(r.get("zone") for r in rules)
    dataset = _load_foam_zoned(case, time_step) if needs_zones else _load_foam(case, time_step)
    criteria, worst = [], "🟢"

    for rule in rules:
        unit = rule.get("unit", "")
        zone = rule.get("zone")
        evalue = _evaluate_rule(dataset, rule)
        if evalue is None:
            manquant = rule.get("limit_field") or rule["field"]
            criteria.append({
                "id": rule["id"], "status": "⚪", "value": None,
                "limit": float(rule.get("limit", 0.0)), "unit": unit,
                "ratio": None,
                "prognosis": "", "criterion": rule.get("criterion", ""),
                "diagnosis": rule.get("diagnosis", ""),
                "note": f"champ '{manquant}' absent du cas"})
            continue

        value, limit, ratio = evalue
        seuil_alerte = float(rule.get("warning_fraction", 0.9))
        emoji = "🔴" if ratio >= 1.0 else ("🟡" if ratio >= seuil_alerte else "🟢")
        if emoji == "🔴" or (emoji == "🟡" and worst != "🔴"):
            worst = emoji
        prog = prognose(case, rule) if (prognosis and emoji != "🟢") else ""
        criterion_out = {
            "id": rule["id"], "status": emoji, "value": value,
            "limit": limit, "unit": unit,
            "ratio": round(ratio, 3), "prognosis": prog,
            "criterion": rule.get("criterion", ""),
            "diagnosis": rule.get("diagnosis", "")}

        notes = []
        if zone and _find_zone_block(dataset, zone) is None:
            notes.append(f"cellZone '{zone}' introuvable dans ce cas : repli sur "
                         "le pic tout-domaine (proxy conservatif).")
        if rule.get("limit_field"):
            if _pick_mesh(dataset, [rule["field"], rule["limit_field"]], zone):
                notes.append(f"limite lue dans le champ '{rule['limit_field']}' "
                             "calculé par le solveur, comparaison point par point.")
            else:
                notes.append(f"champ de limite '{rule['limit_field']}' "
                             "indisponible : repli sur la limite constante.")
        if notes:
            criterion_out["note"] = " ".join(notes)
        criteria.append(criterion_out)

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
        "Evalue les marges de surete d'un cas OFFBEAT DEJA SIMULE, en lisant "
        "les champs ecrits sur disque. EXIGE le chemin d'un repertoire de cas "
        "dont le calcul est termine. Compare les pics (temperature a coeur, "
        "contrainte et deformation de gaine, largeur de gap) aux seuils de "
        "conception de offbeat_skills/safety_kb.json, retourne un statut "
        "🟢/🟡/🔴 par critere, et extrapole l'instant de franchissement a "
        "partir des pas de temps deja ecrits dans le cas. "
        "A utiliser des que l'utilisateur demande si un cas EXISTANT est sur, "
        "s'il y a un risque de fusion du combustible, de PCMI ou de rupture "
        "de gaine. "
        "NE PAS confondre : surrogate_predict repond sans aucun cas, a partir "
        "de simples parametres ; offbeat_executor lance le calcul mais "
        "n'evalue aucun critere de surete."
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
            if c.get("note"):
                line += "\n  ⚠ " + c["note"]
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
