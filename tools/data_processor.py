"""
data_processor.py : Post-traitement des résultats OFFBEAT via pyvista.

Équivalent du data_processor d'AutoFLUKA (qui décodait les _fort.xx
FLUKA) : ici on lit les fichiers VTK/foam produits par OFFBEAT et on
extrait les profils utiles (température axiale, contrainte de cerclage,
déformation du cladding) que l'agent peut commenter à l'utilisateur.

pyvista comprend nativement les formats OpenFOAM (.foam, VTK legacy,
VTK XML) ; aucun convertisseur externe n'est nécessaire.
"""

import json
import os
from pathlib import Path
from typing import Literal, Type

import numpy as np
from pydantic import BaseModel, Field
from langchain_core.tools import BaseTool

# matplotlib en backend non-interactif (Agg) : génère des PNG sans serveur X,
# indispensable sous WSL/Docker où aucun affichage n'est disponible.
# On force MPLCONFIGDIR vers un dossier inscriptible (le HOME peut ne pas
# l'être en conteneur) pour éviter le warning de cache à chaque appel.
if not os.environ.get("MPLCONFIGDIR"):
    import tempfile
    os.environ["MPLCONFIGDIR"] = os.path.join(tempfile.gettempdir(), "matplotlib")
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    _HAS_MPL = True
except ImportError:  # le post-traitement numérique reste possible sans figures
    _HAS_MPL = False


# --------------------------------------------------------------------------
# Helpers de lecture
# --------------------------------------------------------------------------

def _load_foam(case_dir: Path, time_step: str = "latestTime"):
    """Charge le cas OpenFOAM via pyvista et retourne le dataset."""
    try:
        import pyvista as pv
    except ImportError:
        raise ImportError(
            "pyvista n'est pas installé. "
            "Ajouter 'pyvista' dans requirements.txt."
        )

    foam_file = next(case_dir.glob("*.foam"), None)
    if foam_file is None:
        # Crée un fichier .foam vide si absent (convention pyvista)
        foam_file = case_dir / f"{case_dir.name}.foam"
        foam_file.touch()

    reader = pv.OpenFOAMReader(str(foam_file))
    if time_step == "latestTime":
        reader.set_active_time_value(reader.time_values[-1])
    else:
        reader.set_active_time_value(float(time_step))

    return reader.read()


def _load_foam_zoned(case_dir: Path, time_step: str = "latestTime"):
    """Comme _load_foam, mais active la lecture des cellZones OpenFOAM
    (fuel/cladding, cf. offbeat_skills/templates/fuel_rod_1D_pwr/rodDict) pour
    permettre de restreindre un pic (PCT, contrainte de cerclage) a la gaine
    plutot qu'a tout le domaine. Duplique la logique de _load_foam plutot que
    de la reutiliser : read_zones ne peut se configurer qu'AVANT le .read()."""
    import pyvista as pv

    case_dir = Path(case_dir)
    foam_file = next(case_dir.glob("*.foam"), None)
    if foam_file is None:
        foam_file = case_dir / f"{case_dir.name}.foam"
        foam_file.touch()

    reader = pv.OpenFOAMReader(str(foam_file))
    reader.reader.SetReadZones(True)
    reader.reader.SetCopyDataToCellZones(True)

    if time_step == "latestTime":
        reader.set_active_time_value(reader.time_values[-1])
    else:
        reader.set_active_time_value(float(time_step))

    return reader.read()


def _find_zone_block(dataset, zone_name: str):
    """Cherche recursivement un bloc nomme `zone_name` (cellZone OpenFOAM,
    ex. 'cladding') dans le MultiBlock retourne par _load_foam_zoned.
    Retourne le bloc combine (UnstructuredGrid) ou None si absent (zone
    inexistante dans ce cas, ou reader non configure avec read_zones)."""
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


def _component(values, component: int | None):
    """Extrait une composante d'un tableau potentiellement multi-composantes.
    OFFBEAT stocke les contraintes comme des tenseurs symétriques à 6
    composantes (xx, xy, xz, yy, yz, zz pour sigma ; rr, rθ, rz, θθ, θz, zz
    pour sigmaCyl). `component=None` -> scalaire inchangé."""
    arr = np.asarray(values)
    if component is not None and arr.ndim == 2:
        arr = arr[:, component]
    return arr.tolist()


def _valid_mask(sampled):
    """Masque booléen des points réellement tombés DANS le maillage.
    pyvista/VTK pose 'vtkValidPointMask' à 1 pour ces points, 0 sinon.
    Indispensable ici : l'axe r=0 traverse des zones sans maillage (gap,
    plénum au-dessus du combustible) où le champ vaudrait faussement 0."""
    if "vtkValidPointMask" in sampled.array_names:
        return np.asarray(sampled["vtkValidPointMask"]).astype(bool)
    return np.ones(sampled.n_points, dtype=bool)


def _axial_profile(dataset, field: str, n_points: int = 50,
                   component: int | None = None, label: str | None = None,
                   x_offset: float = 0.0) -> dict:
    """Échantillonne un champ le long de l'axe Z du barreau. Pour un champ
    tensoriel/vectoriel, `component` sélectionne la composante voulue.
    Les points hors-maillage sont écartés (masque de validité)."""
    import pyvista as pv

    mesh = dataset.combine()
    bounds = mesh.bounds          # (xmin, xmax, ymin, ymax, zmin, zmax)
    z_min, z_max = bounds[4], bounds[5]

    # Ligne de sonde quasi sur l'axe. Un léger offset radial évite l'arête
    # du wedge (apex à r=0) où l'échantillonnage peut être invalide.
    if x_offset <= 0.0:
        x_offset = max(abs(bounds[0]), abs(bounds[1])) * 0.01 or 1e-6
    line = pv.Line(
        pointa=(x_offset, 0.0, z_min),
        pointb=(x_offset, 0.0, z_max),
        resolution=n_points,
    )
    sampled = line.sample(mesh)
    key = label or field
    if field not in sampled.array_names:
        return {"z_m": [], key: []}

    mask = _valid_mask(sampled)
    z_coords = np.asarray(sampled.points[:, 2])[mask].tolist()
    vals = np.asarray(_component(sampled[field], component))[mask].tolist()
    return {"z_m": z_coords, key: vals}


def _peak_value(dataset, field: str, component: int | None = None,
                use_abs: bool = False, zone: str | None = None) -> float | None:
    """Valeur maximale d'un champ (composante `component` si tensoriel).
    `use_abs=True` retourne la valeur de plus grande amplitude (utile pour
    une contrainte qui peut être compressive/négative). Si `zone` est fourni
    (ex. 'cladding'), restreint aux cellules de cette cellZone si le reader
    l'a exposée (cf. _load_foam_zoned), sinon retombe silencieusement sur
    tout le domaine (comportement inchangé si la zone est indisponible)."""
    # Ordre des candidats : zone demandee, puis fusion, puis blocs
    # 'internalMesh'. Ce dernier repli ne sert qu'aux cas MULTI-REGIONS (cas de
    # verification d'OFFBEAT), ou la fusion perd tous les champs. Le placer
    # APRES la fusion est essentiel : l'inverse casse la lecture de `gapWidth`,
    # dont les valeurs utiles vivent sur les patches de frontiere.
    candidats = []
    if zone:
        z = _find_zone_block(dataset, zone)
        if z is not None:
            candidats.append(z)
    try:
        candidats.append(dataset.combine())
    except Exception:  # noqa: BLE001
        pass

    def _walk(mb):
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
                _walk(b)
            elif nom == "internalmesh":
                candidats.append(b)

    _walk(dataset)

    for mesh in candidats:
        if field in mesh.array_names:
            arr = np.asarray(mesh[field])
            if component is not None and arr.ndim == 2:
                arr = arr[:, component]
            if use_abs:
                return float(arr[np.argmax(np.abs(arr))])
            return float(np.max(arr))
    return None


def _radial_profile_at_midplane(dataset, field: str, n_points: int = 30,
                                component: int | None = None,
                                label: str | None = None) -> dict:
    """Profil radial au plan médian Z. Pour un champ tensoriel/vectoriel,
    `component` sélectionne la composante voulue."""
    import pyvista as pv

    mesh = dataset.combine()
    bounds = mesh.bounds
    z_mid = (bounds[4] + bounds[5]) / 2.0
    r_max = max(abs(bounds[0]), abs(bounds[1]), abs(bounds[2]), abs(bounds[3]))

    line = pv.Line(
        pointa=(0.0, 0.0, z_mid),
        pointb=(r_max, 0.0, z_mid),
        resolution=n_points,
    )
    sampled = line.sample(mesh)
    key = label or field
    if field not in sampled.array_names:
        return {"r_m": [], key: []}

    mask = _valid_mask(sampled)
    r_coords = np.asarray(sampled.points[:, 0])[mask].tolist()
    values = np.asarray(_component(sampled[field], component))[mask].tolist()
    return {"r_m": r_coords, key: values}


# --------------------------------------------------------------------------
# Génération de figures (matplotlib, backend Agg)
# --------------------------------------------------------------------------

def _save_plot(x, y, xlabel: str, ylabel: str, title: str, path: Path) -> bool:
    """Trace y(x) et sauvegarde un PNG. Retourne False si matplotlib absent
    ou si les données sont vides."""
    if not _HAS_MPL or not x or not y:
        return False
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.plot(x, y, "-o", markersize=3, color="tab:red")
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return True


# --------------------------------------------------------------------------
# Outil LangChain
# --------------------------------------------------------------------------

class DataProcessorInput(BaseModel):
    case_dir: str = Field(
        description="Chemin absolu du répertoire de cas OFFBEAT après simulation."
    )
    analysis: Literal["axial_T", "radial_T", "axial_stress", "peak_T", "summary"] = Field(
        default="summary",
        description=(
            "Type d'analyse :\n"
            "  axial_T      : profil axial de température (champ T)\n"
            "  radial_T     : profil radial de T au plan médian\n"
            "  axial_stress : profil axial de sigmaHoop (contrainte de cerclage)\n"
            "  peak_T       : température de gaine maximale (PCT)\n"
            "  summary      : exécute axial_T + radial_T + peak_T"
        ),
    )
    time_step: str = Field(
        default="latestTime",
        description="Pas de temps à lire, ex. '10.0' ou 'latestTime'.",
    )
    output_json: str = Field(
        default="",
        description="Si renseigné, chemin du fichier JSON où sauvegarder "
                    "les résultats (optionnel).",
    )
    make_plots: bool = Field(
        default=True,
        description="Si True, génère des figures PNG (matplotlib) dans "
                    "<case_dir>/figures/ pour chaque profil extrait.",
    )


class OffbeatDataProcessorTool(BaseTool):
    """Post-traite les résultats OFFBEAT avec pyvista."""

    name: str = "data_processor"
    description: str = (
        "Lit les sorties VTK/foam d'un cas OFFBEAT et extrait : "
        "profil axial de température, profil radial, contrainte de cerclage, "
        "température de gaine maximale (PCT). "
        "Retourne les données numériques en JSON et un résumé textuel."
    )
    args_schema: Type[BaseModel] = DataProcessorInput

    def _run(
        self,
        case_dir: str,
        analysis: str = "summary",
        time_step: str = "latestTime",
        output_json: str = "",
        make_plots: bool = True,
    ) -> str:
        case = Path(case_dir)
        if not case.exists():
            return f"ERREUR : répertoire '{case_dir}' introuvable."

        try:
            dataset = _load_foam_zoned(case, time_step)
        except ImportError as exc:
            return str(exc)
        except Exception as exc:  # noqa: BLE001
            return f"ERREUR lecture foam : {exc}"

        results: dict = {"case_dir": case_dir, "time_step": time_step}
        summary_lines = []
        figures: list[str] = []
        fig_dir = case / "figures"
        if make_plots and _HAS_MPL:
            fig_dir.mkdir(exist_ok=True)

        try:
            if analysis in ("axial_T", "summary"):
                data = _axial_profile(dataset, "T")
                results["axial_T"] = data
                if data["T"]:
                    t_max = max(data["T"])
                    t_min = min(data["T"])
                    summary_lines.append(
                        f"Profil axial T : min={t_min:.1f} K, max={t_max:.1f} K"
                    )
                    if make_plots and _save_plot(
                        data["z_m"], data["T"], "z [m]", "T [K]",
                        "Profil axial de température", fig_dir / "axial_T.png"):
                        figures.append(str(fig_dir / "axial_T.png"))

            if analysis in ("radial_T", "summary"):
                data = _radial_profile_at_midplane(dataset, "T")
                results["radial_T"] = data
                if data["T"]:
                    summary_lines.append(
                        f"Profil radial T (plan médian) : "
                        f"centre={data['T'][0]:.1f} K, "
                        f"périphérie={data['T'][-1]:.1f} K"
                    )
                    if make_plots and _save_plot(
                        data["r_m"], data["T"], "r [m]", "T [K]",
                        "Profil radial de température (plan médian)",
                        fig_dir / "radial_T.png"):
                        figures.append(str(fig_dir / "radial_T.png"))

            if analysis in ("axial_stress", "summary"):
                # ATTENTION à l'ordre des composantes : pyvista expose les
                # tenseurs symétriques dans l'ordre VTK (XX, YY, ZZ, XY, YZ, XZ),
                # PAS l'ordre natif OpenFOAM (XX, XY, XZ, YY, YZ, ZZ).
                # Pour sigmaCyl (rr, rθ, rz, θθ, θz, zz) → en ordre VTK la
                # contrainte de cerclage θθ se retrouve à l'index 1.
                arrays = dataset.combine().array_names
                if "sigmaCyl" in arrays:
                    field, comp, label = "sigmaCyl", 1, "sigmaHoop"
                elif "sigmaEq" in arrays:
                    field, comp, label = "sigmaEq", None, "sigmaEq_vonMises"
                else:
                    field, comp, label = "sigma", 1, "sigma_yy"
                # Pic en valeur absolue restreint a la cellZone 'cladding' :
                # sur tout le domaine, le pic peut se trouver dans la pastille
                # avec un signe different de la contrainte de gaine reelle
                # (confirme empiriquement, cf. safety_analyzer.py). Repli
                # silencieux sur tout le domaine si la zone est indisponible.
                s_peak = _peak_value(dataset, field, component=comp,
                                     use_abs=True, zone="cladding")
                if s_peak is not None:
                    results["peak_stress_Pa"] = s_peak
                    zone_ok = _find_zone_block(dataset, "cladding") is not None
                    where = "gaine" if zone_ok else "tout le domaine (zone 'cladding' indisponible)"
                    summary_lines.append(
                        f"Contrainte de cerclage max ({label}, |.| {where}) : "
                        f"{s_peak/1e6:.2f} MPa"
                    )
                # Profil le long de l'axe (combustible) à titre indicatif
                data = _axial_profile(dataset, field, component=comp, label=label)
                results["axial_stress"] = data
                if data.get(label) and any(abs(v) > 1.0 for v in data[label]):
                    if make_plots and _save_plot(
                        data["z_m"], [v / 1e6 for v in data[label]],
                        "z [m]", f"{label} [MPa]",
                        f"Profil axial de contrainte ({label})",
                        fig_dir / "axial_stress.png"):
                        figures.append(str(fig_dir / "axial_stress.png"))

            if analysis in ("peak_T", "summary"):
                # Restreint a la cellZone 'cladding' : sans ca, le "PCT"
                # renvoyait en realite le T max de TOUT le domaine, c.a.d. le
                # centre de la pastille (ecart constate ~830 K sur un cas
                # reel). Repli silencieux sur tout le domaine si indisponible.
                pct = _peak_value(dataset, "T", zone="cladding")
                results["peak_clad_T_K"] = pct
                if pct is not None:
                    zone_ok = _find_zone_block(dataset, "cladding") is not None
                    note = "" if zone_ok else " (zone 'cladding' indisponible, repli tout-domaine : peut être surestimé)"
                    summary_lines.append(f"PCT (Peak Cladding Temperature) : {pct:.1f} K{note}")

        except Exception as exc:  # noqa: BLE001
            return f"ERREUR post-traitement : {exc}"

        if figures:
            results["figures"] = figures
            summary_lines.append("Figures générées :\n" + "\n".join(f"  {f}" for f in figures))
        elif make_plots and not _HAS_MPL:
            summary_lines.append("(matplotlib absent : aucune figure générée)")

        if output_json:
            try:
                Path(output_json).write_text(
                    json.dumps(results, indent=2), encoding="utf-8"
                )
                summary_lines.append(f"Résultats sauvegardés dans {output_json}")
            except OSError as exc:
                summary_lines.append(f"(impossible d'écrire {output_json} : {exc})")

        if not summary_lines:
            return "Aucune donnée extraite. Vérifie les noms de champs dans le cas."
        return "\n".join(summary_lines)
