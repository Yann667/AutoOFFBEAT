#!/usr/bin/env python3
"""
verify.py : check that this repository does what the report says it does.

Run it after `pip install -r requirements.txt`. Nothing else is required for
levels A to D, which are the ones that re-derive the report's headline figures
from the data committed here:

    python verify.py

Levels E and F exercise the parts that need external software (OpenFOAM +
OFFBEAT for the solver, Ollama for the language model). They are skipped with
an explanation when that software is absent, never reported as failures.

    python verify.py --all       also run the solver and the agent
    python verify.py --solver    add the solver level only
    python verify.py --llm       add the language model level only

Exit code is 0 if nothing failed, 1 otherwise. Skips do not fail the run.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# ── affichage ────────────────────────────────────────────────────────────────

_TTY = sys.stdout.isatty()


def _c(code: str, txt: str) -> str:
    return f"\033[{code}m{txt}\033[0m" if _TTY else txt


GREEN, RED, YELLOW, DIM, BOLD = "32", "31", "33", "2", "1"

_counts = {"pass": 0, "fail": 0, "skip": 0}


def level(letter: str, title: str) -> None:
    print(f"\n{_c(BOLD, f'{letter}. {title}')}")
    print(_c(DIM, "─" * 70))


def ok(label: str, detail: str = "") -> None:
    _counts["pass"] += 1
    print(f"  {_c(GREEN, 'pass')}  {label}" + (f"  {_c(DIM, detail)}" if detail else ""))


def fail(label: str, detail: str = "") -> None:
    _counts["fail"] += 1
    print(f"  {_c(RED, 'FAIL')}  {label}" + (f"  {detail}" if detail else ""))


def skip(label: str, why: str) -> None:
    _counts["skip"] += 1
    print(f"  {_c(YELLOW, 'skip')}  {label}  {_c(DIM, why)}")


def close(value: float, expected: float, tol: float) -> bool:
    """Relative comparison, with an absolute fallback for values near zero."""
    if expected == 0:
        return abs(value) <= tol
    return abs(value - expected) / abs(expected) <= tol


# ── A. environnement ─────────────────────────────────────────────────────────

MODULES = [
    "config.llm_factory",
    "tools.input_creator",
    "tools.offbeat_executor",
    "tools.data_processor",
    "tools.safety_analyzer",
    "tools.surrogate",
    "tools.twin_monitor",
    "tools.rag_retriever",
]


def level_a() -> None:
    level("A", "Environment")

    v = sys.version_info
    if v >= (3, 11):
        ok("Python version", f"{v.major}.{v.minor}.{v.micro}")
    else:
        fail("Python version", f"{v.major}.{v.minor} found, 3.11+ required")

    for name in MODULES:
        try:
            importlib.import_module(name)
            ok(f"import {name}")
        except Exception as exc:                                  # noqa: BLE001
            fail(f"import {name}", f"{type(exc).__name__}: {exc}")


# ── B. intégrité des données ─────────────────────────────────────────────────

def level_b() -> None:
    level("B", "Committed data")

    # Base des critères de sûreté : 5 entrées, toutes marquées non validées.
    try:
        kb = json.loads((ROOT / "offbeat_skills/safety_kb.json").read_text())
        crit = kb["criteria"] if isinstance(kb, dict) and "criteria" in kb else kb
        n = len(crit)
        ok("safety_kb.json parses", f"{n} criteria")
        if n == 5:
            ok("safety criteria count", "5, as appendix D")
        else:
            fail("safety criteria count", f"{n}, appendix D says 5")

        unvalidated = sum(1 for c in crit if not c.get("validated", False))
        if unvalidated == n:
            ok("all criteria flagged unvalidated", "as the report states")
        else:
            fail("criteria validation flags", f"{n - unvalidated} marked validated")
    except Exception as exc:                                      # noqa: BLE001
        fail("safety_kb.json", f"{type(exc).__name__}: {exc}")

    try:
        ekb = json.loads((ROOT / "offbeat_skills/error_kb.json").read_text())
        ok("error_kb.json parses", f"{len(ekb) if isinstance(ekb, list) else len(ekb.get('errors', []))} entries")
    except Exception as exc:                                      # noqa: BLE001
        fail("error_kb.json", f"{type(exc).__name__}: {exc}")

    # Gabarits de cas.
    for tpl in ("fuel_rod_1D_pwr", "fuel_rod_2D_rz"):
        d = ROOT / "offbeat_skills/templates" / tpl
        missing = [s for s in ("0", "constant", "system") if not (d / s).is_dir()]
        if d.is_dir() and not missing:
            ok(f"template {tpl}", "0/ constant/ system/ present")
        elif d.is_dir():
            fail(f"template {tpl}", f"missing {', '.join(missing)}")
        else:
            fail(f"template {tpl}", "directory absent")

    # Plan d'expériences de l'émulateur : 5 puissances x 5 durées.
    try:
        import csv
        rows = list(csv.DictReader((ROOT / "offbeat_skills/.surrogate/dataset.csv").open()))
        p = {r["linear_heat_rate"] for r in rows}
        t = {r["end_time"] for r in rows}
        if len(rows) == 25 and len(p) == 5 and len(t) == 5:
            ok("surrogate dataset", "25 points, 5 powers x 5 durations")
        else:
            fail("surrogate dataset",
                 f"{len(rows)} rows, {len(p)} powers, {len(t)} durations; appendix E says 25 / 5 / 5")
    except Exception as exc:                                      # noqa: BLE001
        fail("surrogate dataset", f"{type(exc).__name__}: {exc}")


# ── C. émulateur : reproduit le tableau de sûreté sans solveur ───────────────

# Valeurs du cas de référence, tableau 4.x du rapport.
EXPECTED = {
    "peak_T":            (1467.0,   0.02),   # K,  simulated
    "peak_creep_strain": (3.55e-3,  0.10),   # -
    "min_gap":           (-8.0e-6,  0.15),   # m,  negative = contact
}


def level_c() -> None:
    level("C", "Surrogate: reproducing the reference case without a solver")

    try:
        from tools.surrogate import predict
    except Exception as exc:                                      # noqa: BLE001
        fail("import surrogate", f"{type(exc).__name__}: {exc}")
        return

    if not (ROOT / "offbeat_skills/.surrogate/model.joblib").exists():
        skip("surrogate prediction", "model.joblib absent; run `python -m tools.surrogate --train`")
        return

    try:
        t0 = time.perf_counter()
        res = predict({"linear_heat_rate": 25000, "end_time": 6.3e7})
        dt_ms = (time.perf_counter() - t0) * 1e3
    except Exception as exc:                                      # noqa: BLE001
        fail("surrogate prediction", f"{type(exc).__name__}: {exc}")
        return

    ok("surrogate prediction", f"{dt_ms:.0f} ms for 25 kW/m over 2 years")

    by_target = {c["target"]: c for c in res.get("criteria", [])}
    for target, (expected, tol) in EXPECTED.items():
        c = by_target.get(target)
        if c is None:
            fail(f"{target}", "absent from the prediction")
            continue
        got = c["predicted"]
        if close(got, expected, tol):
            ok(f"{target} vs simulated",
               f"{got:.4g} predicted, {expected:.4g} simulated (within {tol:.0%})")
        else:
            fail(f"{target} vs simulated",
                 f"{got:.4g} predicted, {expected:.4g} expected, outside {tol:.0%}")

    # Le jeu fermé doit ressortir comme critère franchi : c'est le defaut n. 1
    # du rapport, ou un gap negatif etait rapporte comme sur.
    gap = by_target.get("min_gap")
    if gap is not None:
        if gap["predicted"] < 0 and gap["status"] != "\U0001F7E2":
            ok("closed gap flagged as exceeded", "negative gap is not reported safe")
        else:
            fail("closed gap flagged as exceeded",
                 f"gap {gap['predicted']:.3g} m reported {gap['status']}")


# ── D. bancs d'essai : recalcul depuis les résultats bruts ───────────────────

def level_d() -> None:
    level("D", "Benchmarks: recomputing the reported figures from the raw results")

    ev = ROOT / "evaluation"

    # D1. Sélection d'outils : 20/26 puis 23/26.
    for fname, expected, label in (
        ("resultats_tool_selection.json", 20, "tool selection, before"),
        ("resultats_tool_selection_v2.json", 23, "tool selection, after"),
    ):
        try:
            d = json.loads((ev / fname).read_text())
            got = sum(1 for r in d if r.get("correct") is True)
            if len(d) == 26 and got == expected:
                ok(label, f"{got}/26 = {100 * got / 26:.0f}%, as reported")
            else:
                fail(label, f"{got}/{len(d)}, report says {expected}/26")
        except Exception as exc:                                  # noqa: BLE001
            fail(label, f"{type(exc).__name__}: {exc}")

    # L'analyseur de sûreté passe de 1/4 à 4/4 : c'est le coeur du resultat.
    try:
        before = json.loads((ev / "resultats_tool_selection.json").read_text())
        after = json.loads((ev / "resultats_tool_selection_v2.json").read_text())
        b = sum(1 for r in before if r.get("attendu") == "safety_analyzer" and r.get("correct"))
        a = sum(1 for r in after if r.get("attendu") == "safety_analyzer" and r.get("correct"))
        if (b, a) == (1, 4):
            ok("safety analyser selection", "1/4 -> 4/4 after rewording the descriptions")
        else:
            fail("safety analyser selection", f"{b}/4 -> {a}/4, report says 1/4 -> 4/4")
    except Exception as exc:                                      # noqa: BLE001
        fail("safety analyser selection", f"{type(exc).__name__}: {exc}")

    # D2. Recherche documentaire : 4/15 avant le modèle multilingue.
    try:
        d = json.loads((ev / "resultats_rag.json").read_text())
        top1 = sum(1 for r in d if r.get("exact_top1"))
        recall = sum(1 for r in d if r.get("rappel_k"))
        if len(d) == 15 and top1 == 4 and recall == 4:
            ok("documentary retrieval, before",
               "accuracy@1 = recall@4 = 4/15 (27%), the collapse the report diagnoses")
        else:
            fail("documentary retrieval, before",
                 f"top1 {top1}/{len(d)}, recall {recall}/{len(d)}; report says 4/15 for both")
    except Exception as exc:                                      # noqa: BLE001
        fail("documentary retrieval, before", f"{type(exc).__name__}: {exc}")

    # D3. Auto-réparation : le tableau croisé 1/1, 0/3, 0/8.
    try:
        d = json.loads((ev / "resultats_selfhealing.json").read_text())
        faults = [r for r in d if not str(r.get("categorie", "")).startswith("Controle")]
        det = sum(1 for r in d if r.get("detecte"))
        rep = sum(1 for r in d if r.get("repare"))
        if len(d) == 14 and len(faults) == 12 and det == 9 and rep == 3:
            ok("self-healing totals", "14 cases, 12 faults, 9 detected, 3 repaired")
        else:
            fail("self-healing totals",
                 f"{len(d)} cases, {len(faults)} faults, {det} detected, {rep} repaired")

        cells = {
            "correction available and cause treatable":
                [r for r in faults if r.get("correctif_kb") and r.get("cause_traitable")],
            "correction available, cause not treatable":
                [r for r in faults if r.get("correctif_kb") and not r.get("cause_traitable")],
            "no correction available":
                [r for r in faults if not r.get("correctif_kb")],
        }
        expected_cells = {
            "correction available and cause treatable": (1, 1),
            "correction available, cause not treatable": (0, 3),
            "no correction available": (0, 8),
        }
        for name, group in cells.items():
            got = (sum(1 for r in group if r.get("repare")), len(group))
            if got == expected_cells[name]:
                ok(f"cross-tab: {name}", f"{got[0]}/{got[1]}")
            else:
                fail(f"cross-tab: {name}",
                     f"{got[0]}/{got[1]}, report says "
                     f"{expected_cells[name][0]}/{expected_cells[name][1]}")
    except Exception as exc:                                      # noqa: BLE001
        fail("self-healing cross-tab", f"{type(exc).__name__}: {exc}")


# ── E. solveur ───────────────────────────────────────────────────────────────

def _offbeat_binary() -> str | None:
    cand = os.environ.get("OFFBEAT_BIN") or shutil.which("offbeat")
    return cand if cand and Path(cand).exists() else None


def level_e(run: bool) -> None:
    level("E", "Solver (OpenFOAM + OFFBEAT)")

    binary = _offbeat_binary()
    if binary is None:
        skip("OFFBEAT binary", "not found; set OFFBEAT_BIN or put offbeat on PATH")
        skip("short simulation", "requires the solver")
        return
    ok("OFFBEAT binary", binary)

    blockmesh = os.environ.get("BLOCKMESH_BIN") or shutil.which("blockMesh")
    if blockmesh and Path(blockmesh).exists():
        ok("blockMesh binary", blockmesh)
    else:
        skip("blockMesh binary", "not found; source the OpenFOAM bashrc first")
        skip("short simulation", "requires blockMesh")
        return

    if not run:
        skip("short simulation", "pass --solver or --all to run it (a few minutes)")
        return

    print(_c(DIM, "        running run_sim.py, this takes a few minutes..."))
    try:
        proc = subprocess.run(
            [sys.executable, "run_sim.py"],
            cwd=ROOT, capture_output=True, text=True, timeout=3600,
        )
        if proc.returncode == 0:
            ok("short simulation", "run_sim.py completed")
        else:
            fail("short simulation",
                 f"run_sim.py exited {proc.returncode}: {proc.stderr.strip()[-200:]}")
    except subprocess.TimeoutExpired:
        fail("short simulation", "timed out after 1 h")
    except Exception as exc:                                      # noqa: BLE001
        fail("short simulation", f"{type(exc).__name__}: {exc}")


# ── F. modèle de langage ─────────────────────────────────────────────────────

def level_f(run: bool) -> None:
    level("F", "Language model (Ollama)")

    base = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    try:
        import urllib.request
        with urllib.request.urlopen(f"{base}/api/tags", timeout=3) as resp:
            tags = json.loads(resp.read())
        names = [m["name"] for m in tags.get("models", [])]
        ok("Ollama reachable", f"{base}, {len(names)} model(s)")
    except Exception:                                             # noqa: BLE001
        skip("Ollama reachable", f"nothing answering on {base}; see the README")
        skip("tool-selection benchmark", "requires a language model")
        return

    wanted = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")
    if any(n.split(":")[0] == wanted.split(":")[0] for n in names):
        ok(f"model {wanted}", "present")
    else:
        skip(f"model {wanted}", f"absent; run `ollama pull {wanted}`")
        skip("tool-selection benchmark", "requires the model")
        return

    embed = os.environ.get("EMBED_MODEL", "bge-m3")
    if any(n.split(":")[0] == embed.split(":")[0] for n in names):
        ok(f"embedding model {embed}", "present, and multilingual as section 4.6 requires")
    else:
        skip(f"embedding model {embed}",
             f"absent; run `ollama pull {embed}`. A monolingual model scores 0/11 here")

    if not run:
        skip("tool-selection benchmark", "pass --llm or --all to run it (about a minute)")
        return

    print(_c(DIM, "        running the 26 requests of the tool-selection benchmark..."))
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "evaluation.bench_tool_selection"],
            cwd=ROOT, capture_output=True, text=True, timeout=1800,
        )
        if proc.returncode == 0:
            ok("tool-selection benchmark", "completed; compare with table 4.x")
        else:
            fail("tool-selection benchmark",
                 f"exited {proc.returncode}: {proc.stderr.strip()[-200:]}")
    except subprocess.TimeoutExpired:
        fail("tool-selection benchmark", "timed out after 30 min")
    except Exception as exc:                                      # noqa: BLE001
        fail("tool-selection benchmark", f"{type(exc).__name__}: {exc}")


# ── point d'entrée ───────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Check that this repository does what the report says it does.")
    ap.add_argument("--solver", action="store_true", help="also run a simulation (minutes)")
    ap.add_argument("--llm", action="store_true", help="also run the tool-selection benchmark")
    ap.add_argument("--all", action="store_true", help="both of the above")
    args = ap.parse_args()

    run_solver = args.solver or args.all
    run_llm = args.llm or args.all

    print(_c(BOLD, "AutoOFFBEAT verification"))
    print(_c(DIM, "Levels A to D need only the Python dependencies. E and F are skipped"))
    print(_c(DIM, "when the external software is absent, which is not a failure."))

    level_a()
    level_b()
    level_c()
    level_d()
    level_e(run_solver)
    level_f(run_llm)

    print(f"\n{_c(DIM, '─' * 70)}")
    p, f, s = _counts["pass"], _counts["fail"], _counts["skip"]
    verdict = _c(RED, "FAILED") if f else _c(GREEN, "OK")
    print(f"  {verdict}   {p} passed, {f} failed, {s} skipped")
    if f == 0 and s:
        print(_c(DIM, "  Skips are levels needing OpenFOAM/OFFBEAT or Ollama. "
                      "See the README to enable them."))
    print()
    return 1 if f else 0


if __name__ == "__main__":
    sys.exit(main())
