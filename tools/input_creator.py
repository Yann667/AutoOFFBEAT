"""
input_creator.py : Génération de cas OpenFOAM/OFFBEAT.

Équivalent de l'input_creator d'AutoFLUKA (qui produisait des cartes .inp
FLUKA). Ici, on s'appuie sur la structure réelle d'un cas OFFBEAT :

    cas/
    ├── rodDict                 ← géométrie du barreau (SYNTAXE PYTHON,
    │                             lue par rodMaker.py)
    ├── rodMaker.py             ← génère system/blockMeshDict depuis rodDict
    ├── constant/
    │   ├── solverDict          ← dictionnaire principal OFFBEAT :
    │   │                         solveurs, matériaux, puissance (lhgr), FGR…
    │   ├── axialProfile        ← profil de puissance axial
    │   └── systemPressure      ← pression caloporteur vs temps
    ├── system/
    │   ├── controlDict         ← endTime, deltaT, pas de temps adaptatif
    │   ├── fvSchemes / fvSolution
    │   └── probes / sample
    └── 0/                      ← champs initiaux T, D, gapGas, neutronFlux0

Le maillage (constant/polyMesh) n'est PAS écrit ici : il est régénéré par
rodMaker.py + blockMesh au moment de l'exécution (cf. offbeat_executor).

Approche : on part TOUJOURS d'un template validé des offbeat_skills puis on
surcharge les quelques paramètres demandés par l'utilisateur. Générer un cas
OFFBEAT complet « from scratch » n'est pas fiable (solverDict ~200 lignes de
modèles couplés) : on guide donc l'agent vers le mode template.
"""

import json
import re
import shutil
from pathlib import Path
from typing import Any, Dict, Type, Union

from pydantic import BaseModel, Field
from langchain_core.tools import BaseTool

SKILLS_DIR = Path(__file__).parent.parent / "offbeat_skills"
TEMPLATES_DIR = SKILLS_DIR / "templates"


# --------------------------------------------------------------------------
# Patch des dictionnaires OpenFOAM (syntaxe FOAM : `key   value;`)
# --------------------------------------------------------------------------

def _patch_foam_entry(case_dir: Path, rel_path: str, key: str, value: str) -> bool:
    """Remplace `key <ancienne valeur>;` par `key <value>;`."""
    f = case_dir / rel_path
    if not f.exists():
        return False
    text = f.read_text()
    new_text, n = re.subn(
        rf"(^\s*{re.escape(key)}\s+)\S[^;]*?(\s*;)",
        rf"\g<1>{value}\g<2>",
        text,
        flags=re.M,
    )
    if n:
        f.write_text(new_text)
    return bool(n)


def _patch_foam_lhgr(case_dir: Path, value: float) -> bool:
    """Remplace le plateau de puissance dans heatSourceOptions :
        lhgr  ( 0  15000  15000 );
    On garde le premier 0 (rampe) et on remplace les valeurs non nulles."""
    f = case_dir / "constant" / "solverDict"
    if not f.exists():
        return False
    text = f.read_text()

    def _repl(m: re.Match) -> str:
        nums = re.findall(r"[-+0-9.eE]+", m.group(1))
        new_nums = [n if float(n) == 0.0 else f"{value:g}" for n in nums]
        return "lhgr" + m.group(0)[len("lhgr"):].replace(
            m.group(1), "    " + "    ".join(new_nums) + " "
        )

    new_text, n = re.subn(r"lhgr\s*\(([^)]*)\)", _repl, text)
    if n:
        f.write_text(new_text)
    return bool(n)


# --------------------------------------------------------------------------
# Patch de rodDict (SYNTAXE PYTHON : `'key': value,`)
# --------------------------------------------------------------------------

def _patch_roddict(case_dir: Path, key: str, value) -> bool:
    """Remplace la valeur d'une clé Python dans rodDict.
    Gère les scalaires (`'wedgeAngle': 0.25,`) et les listes
    (`'rOuterFuel': [4.5],`). `value` est sérialisé en littéral Python."""
    f = case_dir / "rodDict"
    if not f.exists():
        return False
    text = f.read_text()
    literal = json.dumps(value)  # JSON ≈ littéral Python pour list/nombre/str
    # [^\]]*\] d'abord : une liste multi-elements (ex. [4.565, 4.565]) contient
    # une virgule AVANT son ']' final ; sans cette alternative prioritaire,
    # [^,\n]+ s'arrete a cette virgule interne et laisse un residu du type
    # '[4.68, 4.68], 4.565]' (litteral Python casse, plante silencieusement
    # rodMaker.py plus tard). Repli sur [^,\n]+ pour un scalaire (ex. 0.25).
    new_text, n = re.subn(
        rf"(['\"]{re.escape(key)}['\"]\s*:\s*)(\[[^\]]*\]|[^,\n]+)",
        rf"\g<1>{literal}",
        text,
    )
    if n:
        f.write_text(new_text)
    return bool(n)


def _read_roddict_numbers(case_dir: Path, key: str) -> list[float]:
    """Relit les nombres associes a une cle de rodDict (apres patch ou valeur
    par defaut du template), ex. 'rOuterFuel': [4.5] -> [4.5]. Retourne []
    si la cle est absente (le critere de coherence geometrique est alors
    ignore plutot que de bloquer sur un template atypique)."""
    f = case_dir / "rodDict"
    if not f.exists():
        return []
    m = re.search(rf"['\"]{re.escape(key)}['\"]\s*:\s*([^\n]+?),?\s*$",
                  f.read_text(), flags=re.M)
    if not m:
        return []
    return [float(n) for n in re.findall(r"[-+0-9.eE]+", m.group(1))]


def _check_fuel_clad_geometry(case_dir: Path) -> str:
    """Verifie que la pastille ne chevauche pas la gaine (rOuterFuel <
    rInnerClad, gap positif). Motif du crash reel observe : surcharger
    fuel_outer_radius SANS ajuster clad_inner_radius en consequence produit
    une interference geometrique -> pression de contact initiale absurde ->
    exception flottante (sinh() dans le modele de fluage Limback) des le
    premier pas de temps, INDEPENDAMMENT de deltaT (donc non corrigee par le
    self-healing existant, qui ne sait que reduire le pas de temps).
    Retourne un message d'erreur si incoherent, "" sinon."""
    r_fuel = _read_roddict_numbers(case_dir, "rOuterFuel")
    r_clad_in = _read_roddict_numbers(case_dir, "rInnerClad")
    if not r_fuel or not r_clad_in:
        return ""
    if max(r_fuel) >= min(r_clad_in):
        return (
            f"ERREUR : geometrie incoherente - rOuterFuel (max {max(r_fuel)} mm) "
            f">= rInnerClad (min {min(r_clad_in)} mm) : la pastille chevauche "
            "la gaine (gap negatif). Ajuste clad_inner_radius en consequence "
            "(garde un gap positif, ~0.05-0.1 mm typique) : sans ca, le "
            "solveur diverge immediatement (exception flottante des t=0, "
            "self-healing sans effet car ce n'est pas un probleme de deltaT)."
        )
    r_clad_out = _read_roddict_numbers(case_dir, "rOuterClad")
    if r_clad_out and max(r_clad_in) >= min(r_clad_out):
        return (
            f"ERREUR : geometrie incoherente - rInnerClad (max {max(r_clad_in)} mm) "
            f">= rOuterClad (min {min(r_clad_out)} mm) : epaisseur de gaine "
            "negative ou nulle."
        )
    return ""


# --------------------------------------------------------------------------
# Table de correspondance paramètre utilisateur -> cible
# --------------------------------------------------------------------------
# Chaque entrée : (type_de_patch, *args)
#   ("foam",  rel_path, foam_key)
#   ("rod",   rod_key)
#   ("lhgr",)   cas spécial pour la puissance linéique

PARAM_MAP = {
    "end_time":          ("foam", "system/controlDict", "endTime"),
    "delta_t":           ("foam", "system/controlDict", "deltaT"),
    "write_interval":    ("foam", "system/controlDict", "writeInterval"),
    "enrichment":        ("foam", "constant/solverDict", "enrichment"),
    "linear_heat_rate":  ("lhgr",),
    "fuel_outer_radius": ("rod", "rOuterFuel"),
    "fuel_height":       ("rod", "heightFuel"),
    "clad_inner_radius": ("rod", "rInnerClad"),
    "clad_outer_radius": ("rod", "rOuterClad"),
    "n_cells_r_fuel":    ("rod", "nCellsRFuel"),
    "wedge_angle":       ("rod", "wedgeAngle"),
}


def _coerce_nombre(valeur):
    """Convertit en nombre une valeur numerique transmise sous forme de texte.

    Un modele de langage cite volontiers ses arguments (`"4.5"` plutot que
    `4.5`). Sans cette normalisation, la valeur est ecrite telle quelle dans
    rodDict, donc entre guillemets, et produit un litteral Python invalide
    que rodMaker.py refuse ensuite, loin de la cause."""
    if isinstance(valeur, str):
        texte = valeur.strip()
        try:
            return int(texte)
        except ValueError:
            try:
                return float(texte)
            except ValueError:
                return valeur
    if isinstance(valeur, list):
        return [_coerce_nombre(v) for v in valeur]
    return valeur


def _apply_params(case_dir: Path, params: dict) -> tuple[list[str], list[str]]:
    """Applique les paramètres reconnus. Retourne (appliqués, ignorés)."""
    applied, ignored = [], []
    for key, value in params.items():
        spec = PARAM_MAP.get(key)
        if spec is None:
            ignored.append(key)
            continue
        kind = spec[0]
        if kind == "foam":
            ok = _patch_foam_entry(case_dir, spec[1], spec[2], str(value))
        elif kind == "rod":
            ok = _patch_roddict(case_dir, spec[1], value)
        elif kind == "lhgr":
            ok = _patch_foam_lhgr(case_dir, float(value))
        else:
            ok = False
        (applied if ok else ignored).append(f"{key}={value}")
    return applied, ignored


# --------------------------------------------------------------------------
# Outil LangChain
# --------------------------------------------------------------------------

class InputCreatorInput(BaseModel):
    case_dir: str = Field(
        description="Chemin absolu où créer le cas OFFBEAT, ex. /host/rod_test"
    )
    template_name: str = Field(
        default="fuel_rod_1D_pwr",
        description="Nom du sous-dossier dans offbeat_skills/templates/ à "
                    "utiliser comme base (ex. 'fuel_rod_1D_pwr').",
    )
    # str OU dict : un modele de langage produit spontanement un OBJET pour un
    # parametre nomme « params ». N'accepter qu'une chaine faisait echouer
    # l'appel avec « Input should be a valid string », et l'agent rejouait le
    # meme appel en boucle sans jamais converger (mesure : 30 appels, aucune
    # creation aboutie). Accepter les deux formes supprime la boucle.
    params: Union[str, Dict[str, Any]] = Field(
        default_factory=dict,
        description=(
            "Paramètres à surcharger, en objet JSON (un dictionnaire est "
            "accepté directement). Clés reconnues : "
            "end_time, delta_t, write_interval, enrichment, linear_heat_rate, "
            "fuel_outer_radius, fuel_height, clad_inner_radius, "
            "clad_outer_radius, n_cells_r_fuel, wedge_angle. "
            "Les rayons/hauteurs sont en MILLIMÈTRES (convention rodDict du "
            "template : rayon de pastille ~4.5, rayon intérieur de gaine "
            "~4.565), linear_heat_rate en W/m, end_time/delta_t en secondes. "
            "N'indiquer que les paramètres réellement demandés : tous les "
            "autres gardent la valeur validée du template."
        ),
    )


class OffbeatInputCreatorTool(BaseTool):
    """Crée un cas OFFBEAT à partir d'un template validé + surcharges."""

    name: str = "input_creator"
    description: str = (
        "Crée un cas OFFBEAT complet en copiant un template validé des "
        "offbeat_skills puis en surchargeant les paramètres demandés "
        "(puissance linéique, géométrie du barreau, durée de simulation, "
        "enrichissement…). Ne génère pas le maillage : c'est offbeat_executor "
        "qui lance rodMaker.py + blockMesh. Retourne la liste des paramètres "
        "appliqués et le chemin du cas prêt à exécuter."
    )
    args_schema: Type[BaseModel] = InputCreatorInput

    def _list_templates(self) -> list[str]:
        if not TEMPLATES_DIR.exists():
            return []
        return [d.name for d in TEMPLATES_DIR.iterdir() if d.is_dir()]

    def _run(
        self,
        case_dir: str,
        template_name: str = "fuel_rod_1D_pwr",
        params: Union[str, Dict[str, Any], None] = None,
    ) -> str:
        src = TEMPLATES_DIR / template_name
        if not src.exists():
            return (f"ERREUR : template '{template_name}' introuvable. "
                    f"Templates disponibles : {self._list_templates()}")

        # Accepte indifferemment un dictionnaire ou une chaine JSON.
        if params is None or params == "":
            p = {}
        elif isinstance(params, dict):
            p = dict(params)
        else:
            try:
                p = json.loads(params)
            except json.JSONDecodeError as exc:
                return f"ERREUR : params n'est pas un JSON valide : {exc}"
        if not isinstance(p, dict):
            return f"ERREUR : params doit décrire un objet, reçu {type(p).__name__}."

        p = {k: _coerce_nombre(v) for k, v in p.items()}

        case = Path(case_dir)
        # Une creation qui echoue ne doit RIEN laisser d'executable derriere
        # elle. Sans cette precaution, une copie partielle subsiste et
        # offbeat_executor accepte de la lancer : le calcul aboutit, se
        # presente comme un succes, mais tourne avec les valeurs par defaut du
        # gabarit au lieu des parametres demandes. C'est un resultat faux qui
        # n'a pas l'air faux. On ne nettoie que si le repertoire n'existait pas
        # avant : un cas prealablement present appartient a l'utilisateur.
        cree_par_nous = not case.exists()

        def _annuler():
            if cree_par_nous and case.exists():
                shutil.rmtree(case, ignore_errors=True)

        try:
            shutil.copytree(src, case, dirs_exist_ok=True)
        except Exception as exc:  # noqa: BLE001
            _annuler()
            return f"ERREUR lors de la copie du template : {exc}"

        try:
            applied, ignored = _apply_params(case, p)
        except Exception as exc:  # noqa: BLE001
            _annuler()
            return f"ERREUR lors de l'application des paramètres : {exc}"

        geometry_error = _check_fuel_clad_geometry(case)
        if geometry_error:
            _annuler()
            return (geometry_error + "\nLe répertoire du cas a été supprimé : "
                    "aucun calcul ne peut être lancé sur une géométrie invalide.")

        lines = [
            f"Cas OFFBEAT créé dans {case_dir} (template '{template_name}').",
            f"Paramètres appliqués : {applied or 'aucun (valeurs par défaut)'}",
        ]
        if ignored:
            lines.append(f"Paramètres ignorés (clé inconnue ou non trouvée) : {ignored}")
        lines.append(
            "Cas prêt. Lance offbeat_executor sur ce répertoire pour "
            "mailler (rodMaker + blockMesh) et exécuter le solveur."
        )
        return "\n".join(lines)
