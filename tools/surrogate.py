"""
surrogate.py – Emulateur rapide du crayon combustible (jumeau numerique, D5).

Le vrai « temps reel » (cf. GUIDE.md Partie 2). OFFBEAT est un solveur batch
(minutes a heures) : impossible de re-simuler a chaque seconde. La parade
classique du jumeau numerique est un **modele reduit (surrogate)** : on entraine
une regression sur un JEU de runs OFFBEAT, puis on predit les marges de surete
d'un nouveau point de fonctionnement en **millisecondes**, sans relancer le
solveur. OFFBEAT reste la « verite terrain » qu'on rejoue periodiquement.

Chaine :
  1. build_dataset() : balaye des conditions (ex. puissance lineique), lance
     OFFBEAT pour chacune (via input_creator + executor), releve les metriques
     de surete (via safety_analyzer.analyze) -> dataset CSV.
  2. train() : ajuste une regression polynomiale par metrique -> modele joblib.
  3. predict() : pour un nouveau point, predit T_max, deformation hoop, gap min
     EN INSTANTANE, puis reutilise les seuils safety_kb.json pour un verdict
     🟢/🟡/🔴 immediat.

Perimetre : UN crayon (les features/targets se generalisent a l'assemblage
plus tard). scikit-learn + joblib (cf. requirements.txt).
"""

import csv
import json
import argparse
from pathlib import Path

from tools.safety_analyzer import analyze, _load_safety_kb, _status

SURROGATE_DIR = Path(__file__).parent.parent / "offbeat_skills" / ".surrogate"
DATASET_PATH = SURROGATE_DIR / "dataset.csv"
MODEL_PATH = SURROGATE_DIR / "model.joblib"

# Feature(s) d'entree (conditions d'exploitation) et metriques de sortie.
# Chaque target est reliE a un critere de safety_kb.json pour le verdict.
# Emulateur 2D : la surete depend de la PUISSANCE et de la DUREE simulee
# (proxy du burnup). Le code reste generique sur le nombre de features.
FEATURES = ["linear_heat_rate", "end_time"]
TARGET_TO_RULE = {
    "peak_T":          "fuel_centerline_melt",
    "peak_hoop_strain": "cladding_hoop_strain_pcmi",
    "min_gap":         "gap_closure_pcmi_onset",
}
TARGETS = list(TARGET_TO_RULE)


# --------------------------------------------------------------------------
# 1) Construction du dataset (runs OFFBEAT)
# --------------------------------------------------------------------------

def _metrics_from_case(case_dir: str) -> dict:
    """Releve les metriques de surete d'un cas simule (via analyze)."""
    report = analyze(case_dir, prognosis=False)
    by_id = {c["id"]: c for c in report["criteria"]}
    out = {}
    for target, rule_id in TARGET_TO_RULE.items():
        c = by_id.get(rule_id)
        out[target] = c["value"] if c and c["value"] is not None else None
    return out


def build_dataset(lhgr_values, end_time_values=(3000,), template="fuel_rod_1D_pwr",
                  workdir="/tmp/surrogate_runs", dataset_path=DATASET_PATH) -> int:
    """Lance OFFBEAT pour chaque couple (puissance lineique, duree simulee) —
    produit cartesien — et enregistre les metriques de surete. Retourne le
    nombre de points reussis. Reutilise la chaine validee input_creator ->
    executor -> safety_analyzer. Un seul point d'entree = une grille 2D."""
    from tools.input_creator import OffbeatInputCreatorTool
    from tools.offbeat_executor import OffbeatExecutorTool

    SURROGATE_DIR.mkdir(parents=True, exist_ok=True)
    creator, runner = OffbeatInputCreatorTool(), OffbeatExecutorTool()
    rows = []
    for lhgr in lhgr_values:
        for end_time in end_time_values:
            case = str(Path(workdir) / f"lhgr_{int(lhgr)}_t_{int(end_time)}")
            params = json.dumps({"linear_heat_rate": lhgr, "end_time": end_time})
            print(f"[surrogate] run lhgr={lhgr} W/m, end_time={end_time} s ...")
            creator._run(case_dir=case, template_name=template, params=params)
            run_report = runner._run(case_dir=case)
            if "succès" not in run_report and "success" not in run_report.lower():
                print(f"[surrogate]   run non abouti (ignore) : "
                      f"{run_report.splitlines()[-1]}")
                continue
            metrics = _metrics_from_case(case)
            if any(v is None for v in metrics.values()):
                print(f"[surrogate]   metriques incompletes (ignore) : {metrics}")
                continue
            rows.append({"linear_heat_rate": lhgr, "end_time": end_time, **metrics})
            print(f"[surrogate]   OK -> {metrics}")

    if rows:
        with open(dataset_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FEATURES + TARGETS)
            w.writeheader()
            w.writerows(rows)
        print(f"[surrogate] dataset ecrit : {len(rows)} points -> {dataset_path}")
    return len(rows)


# --------------------------------------------------------------------------
# 2) Entrainement
# --------------------------------------------------------------------------

def _build_estimator(model_type: str, degree: int, n_features: int):
    """Construit le pipeline sklearn (mise a l'echelle systematique, car les
    variables ont des ordres de grandeur tres differents : lhgr ~1e4, temps ~1e3).
      - 'gp'   : processus gaussien (kriging) — interpole finement une surface
                 lisse ET fournit une incertitude. Standard pour emuler un code.
      - 'poly' : regression polynomiale (repli simple, extrapolation douce)."""
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler, PolynomialFeatures
    from sklearn.linear_model import LinearRegression

    if model_type == "gp":
        from sklearn.gaussian_process import GaussianProcessRegressor
        from sklearn.gaussian_process.kernels import (
            ConstantKernel, Matern, WhiteKernel)
        kernel = (ConstantKernel(1.0, (1e-2, 1e3))
                  * Matern(length_scale=[1.0] * n_features,
                           length_scale_bounds=(1e-2, 1e2), nu=2.5)
                  + WhiteKernel(noise_level=1e-3, noise_level_bounds=(1e-8, 1e1)))
        gp = GaussianProcessRegressor(kernel=kernel, normalize_y=True,
                                      n_restarts_optimizer=8, random_state=0)
        return Pipeline([("scaler", StandardScaler()), ("gp", gp)])
    return Pipeline([("scaler", StandardScaler()),
                     ("poly", PolynomialFeatures(degree)),
                     ("lin", LinearRegression())])


def train(dataset_path=DATASET_PATH, model_path=MODEL_PATH,
          model_type="gp", degree=2):
    """Entraine un emulateur par metrique et le sauvegarde (joblib). Rapporte
    une validation HONNETE par leave-one-out (R^2 et MAE) — bien plus fiable que
    le R^2 d'ajustement quand on n'a qu'une poignee de points. Retourne les
    scores LOO."""
    import numpy as np
    import joblib
    from sklearn.model_selection import LeaveOneOut, cross_val_predict
    from sklearn.metrics import r2_score, mean_absolute_error

    rows = list(csv.DictReader(open(dataset_path, encoding="utf-8")))
    if len(rows) < 4:
        raise RuntimeError(f"Dataset trop petit ({len(rows)} points) : lance "
                           "d'abord build_dataset avec une grille plus large.")
    X = np.array([[float(r[f]) for f in FEATURES] for r in rows])
    deg = min(degree, max(1, len(rows) - 2))
    models, fit_r2, cv_r2, cv_mae = {}, {}, {}, {}
    for t in TARGETS:
        y = np.array([float(r[t]) for r in rows])
        # validation leave-one-out : on predit chaque point avec un modele
        # entraine sur tous les AUTRES -> mesure honnete de generalisation.
        y_loo = cross_val_predict(_build_estimator(model_type, deg, X.shape[1]),
                                  X, y, cv=LeaveOneOut())
        cv_r2[t] = round(float(r2_score(y, y_loo)), 4)
        cv_mae[t] = float(mean_absolute_error(y, y_loo))
        pipe = _build_estimator(model_type, deg, X.shape[1])
        pipe.fit(X, y)                       # modele final sur toutes les donnees
        fit_r2[t] = round(float(pipe.score(X, y)), 4)
        models[t] = pipe
    SURROGATE_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump({"features": FEATURES, "targets": TARGETS, "degree": deg,
                 "model_type": model_type, "models": models}, model_path)
    print(f"[surrogate] modele '{model_type}' entraine ({len(rows)} points) "
          f"-> {model_path}")
    print(f"[surrogate] R^2 ajustement          : {fit_r2}")
    print(f"[surrogate] R^2 leave-one-out (honnete): {cv_r2}")
    print(f"[surrogate] MAE leave-one-out        : "
          f"{ {t: float(f'{cv_mae[t]:.4g}') for t in TARGETS} }")
    return {"fit_r2": fit_r2, "cv_r2": cv_r2, "cv_mae": cv_mae}


# --------------------------------------------------------------------------
# 3) Prediction instantanee + verdict de surete
# --------------------------------------------------------------------------

_BUNDLE_CACHE: dict = {}


def _load_bundle(model_path):
    """Charge le modele joblib, avec cache invalidE par la date de modification
    du fichier. Evite de relire le disque a chaque appel (curseurs de l'UI)."""
    import joblib
    key = str(model_path)
    mtime = Path(model_path).stat().st_mtime
    cached = _BUNDLE_CACHE.get(key)
    if cached is None or cached[0] != mtime:
        _BUNDLE_CACHE[key] = (mtime, joblib.load(model_path))
    return _BUNDLE_CACHE[key][1]


def _predict_one(pipe, x, is_gp: bool):
    """Prediction d'un pipeline sur un point. Pour un processus gaussien,
    retourne aussi l'ecart-type (incertitude) en unite d'origine ; sinon None."""
    if not is_gp:
        return float(pipe.predict(x)[0]), None
    # On applique les etapes de pre-traitement (scaler) puis on interroge le GP
    # avec return_std (non transmis par Pipeline.predict).
    xs = x
    for _, step in pipe.steps[:-1]:
        xs = step.transform(xs)
    mean, std = pipe.steps[-1][1].predict(xs, return_std=True)
    return float(mean[0]), float(std[0])


def predict(features: dict, model_path=MODEL_PATH) -> dict:
    """Predit les metriques de surete pour un point de fonctionnement, EN
    INSTANTANE (pas de solveur), puis attribue un verdict 🟢/🟡/🔴 par metrique
    en reutilisant les seuils de safety_kb.json. Avec un modele 'gp', chaque
    prediction porte une incertitude (± ecart-type). Retourne un dict structurE."""
    import numpy as np

    bundle = _load_bundle(model_path)
    is_gp = bundle.get("model_type") == "gp"
    x = np.array([[float(features[f]) for f in bundle["features"]]])
    rules = {r["id"]: r for r in _load_safety_kb()}

    criteria, worst = [], "🟢"
    for t in bundle["targets"]:
        value, std = _predict_one(bundle["models"][t], x, is_gp)
        rule = rules.get(TARGET_TO_RULE[t], {})
        limit = float(rule.get("limit", float("inf")))
        emoji, ratio = _status(value, limit, rule.get("direction", "below"),
                               float(rule.get("warning_fraction", 0.9)))
        if emoji == "🔴" or (emoji == "🟡" and worst != "🔴"):
            worst = emoji
        crit = {"target": t, "predicted": value, "status": emoji,
                "limit": limit, "ratio": round(ratio, 3),
                "unit": rule.get("unit", "")}
        if std is not None:
            crit["std"] = std
        criteria.append(crit)
    return {"features": features, "overall": worst, "criteria": criteria}


# --------------------------------------------------------------------------
# Outil LangChain : prediction « what-if » instantanee pour l'agent
# --------------------------------------------------------------------------

from typing import Type                                    # noqa: E402
from pydantic import BaseModel, Field                      # noqa: E402
from langchain_core.tools import BaseTool                  # noqa: E402


class SurrogateInput(BaseModel):
    linear_heat_rate: float = Field(
        description="Puissance lineique du crayon (W/m) a evaluer INSTANTANEMENT.")
    end_time: float = Field(
        default=3000,
        description="Duree simulee (s), proxy du burnup. Defaut 3000 s.")


class OffbeatSurrogateTool(BaseTool):
    """Predit les marges de surete d'un crayon SANS relancer le solveur."""

    name: str = "surrogate_predict"
    description: str = (
        "Predit EN INSTANTANE (emulateur entraine sur des runs OFFBEAT) les "
        "marges de surete d'un crayon pour une puissance lineique et une duree "
        "donnees : temperature a coeur, deformation de gaine (PCMI), fermeture "
        "du gap, avec un verdict 🟢/🟡/🔴. A utiliser pour des questions 'et si "
        "la puissance etait de X W/m ?' sans attendre une simulation."
    )
    args_schema: Type[BaseModel] = SurrogateInput

    def _run(self, linear_heat_rate: float, end_time: float = 3000) -> str:
        if not MODEL_PATH.exists():
            return ("Emulateur non entraine. Lance d'abord : "
                    "python -m tools.surrogate build --lhgr ... puis train.")
        try:
            r = predict({"linear_heat_rate": linear_heat_rate,
                         "end_time": end_time})
        except Exception as exc:  # noqa: BLE001
            return f"Prediction indisponible : {exc}"
        lines = [f"Prediction instantanee pour {linear_heat_rate:g} W/m, "
                 f"{end_time:g} s : {r['overall']}"]
        for c in r["criteria"]:
            unc = f" ± {c['std']:.3g}" if c.get("std") is not None else ""
            lines.append(f"  {c['status']} {c['target']} = {c['predicted']:.4g}"
                         f"{unc} {c['unit']} ({c['ratio']*100:.0f}% du seuil)")
        return "\n".join(lines)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Emulateur surrogate (crayon, D5).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="construire le dataset (grille de runs OFFBEAT)")
    b.add_argument("--lhgr", type=float, nargs="+", required=True,
                   help="liste de puissances lineiques (W/m) a simuler")
    b.add_argument("--end-time", type=float, nargs="+", default=[3000],
                   help="liste de durees simulees (s) ; produit cartesien avec --lhgr")

    tr = sub.add_parser("train", help="entrainer le modele depuis le dataset")
    tr.add_argument("--model-type", choices=["gp", "poly"], default="gp",
                    help="gp = processus gaussien (fin, +/- incertitude) ; poly = polynome")
    tr.add_argument("--degree", type=int, default=2, help="degre si model-type=poly")

    p = sub.add_parser("predict", help="predire pour un point de fonctionnement")
    p.add_argument("--lhgr", type=float, required=True)
    p.add_argument("--end-time", type=float, default=3000)

    a = ap.parse_args()
    if a.cmd == "build":
        build_dataset(a.lhgr, end_time_values=a.end_time)
    elif a.cmd == "train":
        train(model_type=a.model_type, degree=a.degree)
    elif a.cmd == "predict":
        import json as _j
        print(_j.dumps(predict({"linear_heat_rate": a.lhgr,
                                "end_time": a.end_time}), indent=2,
                       ensure_ascii=False))
