#!/usr/bin/env python3
"""
explore.py : open a simulated case in an interactive 3D window.

The 1.5D rod is stored as a thin wedge, which is unreadable as such. This
script revolves it into a real rod, cuts a quarter away and lets you turn it
around, change the field and step through time.

    python explore.py                          # reference case, temperature
    python explore.py --field sigmaCyl         # hoop stress instead
    python explore.py --case /path/to/case     # another case
    python explore.py --list                   # what fields are available

Controls in the window : drag to rotate, scroll to zoom, "s"/"w" for surface or
wireframe, "q" to quit. The slider at the bottom steps through the written
times.

Needs a graphical display. Under WSL2 that means WSLg, which recent Windows
provides out of the box ; check with `echo $DISPLAY`.
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import numpy as np
import pyvista as pv

DEFAUT = Path.home() / "pwr_rod_25kw_2y_v2"
R_PELLET, R_CLAD_IN = 4.5, 4.565          # mm, geometry of the shipped template


def trouver_foam(case: Path) -> Path:
    """OpenFOAM readers need a .foam stub ; create one if the case lacks it."""
    stubs = sorted(case.glob("*.foam"))
    if stubs:
        return stubs[0]
    stub = case / f"{case.name}.foam"
    stub.touch()
    print(f"created {stub.name} (empty stub the reader needs)")
    return stub


def profil(im, champ: str, composante: int | None):
    """Radial profile at the mid-plane, plus the axial coordinate."""
    c = im.cell_centers().points
    v = np.asarray(im.cell_data[champ])
    if v.ndim > 1:
        v = v[:, composante if composante is not None else 0]
    return c[:, 0] * 1000.0, c[:, 2], v.ravel()


def secteur(r0, r1, nr, T_de_r, longueur, nth=140):
    r = np.linspace(r0, r1, nr)
    th = np.linspace(-math.pi, math.pi / 2, nth)        # a quarter removed
    x = np.linspace(0.0, longueur, 2)
    R, TH, X = np.meshgrid(r, th, x, indexing="ij")
    g = pv.StructuredGrid(X, R * np.cos(TH), R * np.sin(TH))
    g["value"] = T_de_r(R.ravel(order="F"))
    return g


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--case", type=Path, default=DEFAUT, help="case directory")
    ap.add_argument("--field", default="T", help="field to colour by (default T)")
    ap.add_argument("--component", type=int, default=None,
                    help="component of a vector or tensor field, e.g. 1 for hoop")
    ap.add_argument("--length", type=float, default=58.0,
                    help="millimetres of rod to show (default 58)")
    ap.add_argument("--cmap", default="inferno")
    ap.add_argument("--list", action="store_true", help="list fields and times, then exit")
    a = ap.parse_args()

    if not a.case.is_dir():
        print(f"no such case : {a.case}", file=sys.stderr)
        return 1

    rd = pv.OpenFOAMReader(str(trouver_foam(a.case)))
    temps = list(rd.time_values)
    rd.set_active_time_value(temps[-1])
    rd.enable_all_cell_arrays()
    im = rd.read()["internalMesh"]

    if a.list:
        print(f"\ncase   : {a.case}")
        print(f"times  : {len(temps)} written, from {temps[0]:.4g} to {temps[-1]:.4g} s")
        print(f"cells  : {im.n_cells}")
        print("\nfields :")
        for n in sorted(im.cell_data.keys()):
            arr = np.asarray(im.cell_data[n])
            forme = "scalar" if arr.ndim == 1 else f"{arr.shape[1]} components"
            print(f"  {n:24} {forme}")
        return 0

    if a.field not in im.cell_data:
        print(f"field '{a.field}' absent. Run with --list to see what is there.",
              file=sys.stderr)
        return 1

    def construire(t):
        rd.set_active_time_value(t)
        m = rd.read()["internalMesh"]
        r_mm, z_m, v = profil(m, a.field, a.component)
        zs = np.unique(np.round(z_m, 6))
        sel = np.isclose(np.round(z_m, 6), zs[len(zs) // 2])
        o = np.argsort(r_mm[sel])
        rp, vp = r_mm[sel][o], v[sel][o]
        f = lambda r: np.interp(r, rp, vp)
        return (secteur(1e-3, R_PELLET, 56, f, a.length),
                secteur(R_CLAD_IN, rp.max(), 14, f, a.length),
                float(vp.min()), float(vp.max()))

    def valeurs(t, grille, r0, r1, nr):
        """Field values on an existing grid : the geometry never moves, only
        the numbers, so we rewrite the array instead of rebuilding the mesh."""
        rd.set_active_time_value(t)
        m = rd.read()["internalMesh"]
        r_mm, z_m, v = profil(m, a.field, a.component)
        zs = np.unique(np.round(z_m, 6))
        sel = np.isclose(np.round(z_m, 6), zs[len(zs) // 2])
        o = np.argsort(r_mm[sel])
        rp, vp = r_mm[sel][o], v[sel][o]
        r = np.linspace(r0, r1, nr)
        th = np.linspace(-math.pi, math.pi / 2, 140)
        x = np.linspace(0.0, a.length, 2)
        R, _, _ = np.meshgrid(r, th, x, indexing="ij")
        grille["value"] = np.interp(R.ravel(order="F"), rp, vp)
        return float(vp.min()), float(vp.max())

    pellet, clad, lo, hi = construire(temps[-1])
    unite = {"T": "K", "sigmaCyl": "Pa", "gapWidth": "m"}.get(a.field, "")
    titre = f"{a.field}" + (f" [{unite}]" if unite else "")

    p = pv.Plotter(window_size=(1500, 780))
    p.set_background("white")
    kw = dict(cmap=a.cmap, clim=(lo, hi), scalars="value", smooth_shading=True,
              ambient=.40, diffuse=.75, specular=.30, show_scalar_bar=False)
    ap_, ac_ = p.add_mesh(pellet, **kw), p.add_mesh(clad, **kw)
    p.add_scalar_bar(title=titre, n_labels=5, color="#141D26", fmt="%.4g")
    p.add_text(f"{a.case.name}\n{a.length:.0f} mm of rod, a quarter cut away",
               position="upper_left", font_size=10, color="#4A5766")

    if len(temps) > 1:
        r_clad_ext = float(np.max(np.hypot(clad.points[:, 1], clad.points[:, 2])))

        def au_temps(val):
            t = min(temps, key=lambda x: abs(x - val))
            lo_, hi_ = valeurs(t, pellet, 1e-3, R_PELLET, 56)
            valeurs(t, clad, R_CLAD_IN, r_clad_ext, 14)
            for act in (ap_, ac_):
                act.mapper.scalar_range = (lo_, hi_)
            p.scalar_bar.SetLookupTable(ap_.mapper.lookup_table)
            p.add_text(f"t = {t:.4g} s", position="upper_right",
                       font_size=11, color="#141D26", name="temps")
            p.render()
        p.add_slider_widget(au_temps, [temps[0], temps[-1]], value=temps[-1],
                            title="time [s]", pointa=(.32, .08), pointb=(.72, .08),
                            style="modern", color="#4A5766")

    print(f"opening {a.case.name} : {a.field} from {lo:.4g} to {hi:.4g}. "
          f"Press q to close.")
    p.show()
    return 0


if __name__ == "__main__":
    sys.exit(main())
