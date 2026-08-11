"""
input_creator.py – Génération de cas OpenFOAM/OFFBEAT.

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
from typing import Type

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
    new_text, n = re.subn(
        rf"(['\"]{re.escape(key)}['\"]\s*:\s*)[^,\n]+",
        rf"\g<1>{literal}",
        text,
    )
    if n:
        f.write_text(new_text)
    return bool(n)


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
    params: str = Field(
        default="{}",
        description=(
            "Paramètres à surcharger, au format JSON. Clés reconnues : "
            "end_time, delta_t, write_interval, enrichment, linear_heat_rate, "
            "fuel_outer_radius, fuel_height, clad_inner_radius, "
            "clad_outer_radius, n_cells_r_fuel, wedge_angle. "
            "Les rayons/hauteurs sont en mm (convention rodDict du template), "
            "linear_heat_rate en W/m, end_time/delta_t en secondes."
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
        params: str = "{}",
    ) -> str:
        src = TEMPLATES_DIR / template_name
        if not src.exists():
            return (f"ERREUR : template '{template_name}' introuvable. "
                    f"Templates disponibles : {self._list_templates()}")

        try:
            p = json.loads(params) if params else {}
        except json.JSONDecodeError as exc:
            return f"ERREUR : params n'est pas un JSON valide – {exc}"

        case = Path(case_dir)
        try:
            shutil.copytree(src, case, dirs_exist_ok=True)
        except Exception as exc:  # noqa: BLE001
            return f"ERREUR lors de la copie du template : {exc}"

        applied, ignored = _apply_params(case, p)

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
