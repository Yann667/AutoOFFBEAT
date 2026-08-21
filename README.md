# AutoOFFBEAT

**An LLM agent and digital twin for the OFFBEAT nuclear fuel performance code.**

ENSTA Paris *Projet de Recherche* (PRe), carried out at SPEIT, Shanghai Jiao
Tong University · April to July 2026
Student: Yann Butel (ENSTA Paris, class of 2027) ·
Host supervisor: GONG Helin (SPEIT) ·
ENSTA supervisor: Patrice Paricaud

---

## What this is

Fuel performance simulation relies on codes whose use stays confined to
specialists. AutoOFFBEAT drives [OFFBEAT](https://gitlab.com/foam-for-nuclear/offbeat),
an OpenFOAM-based fuel behaviour solver, from requests written in plain
language. A supervisor built on LangChain orchestrates domain tools; the tools,
not the model, do the physics.

The system was then extended into a **digital twin** of a PWR fuel rod: safety
criteria monitoring, threshold-crossing prognosis, a dashboard, and a
Gaussian-process surrogate that returns a simulation's outcome in milliseconds
with a calibrated uncertainty.

**The second half of the work is the part I would most like reviewed**: rather
than adding features, I built benchmarks that measure what the system actually
does, and they turned out to be where the real findings are. Every number below
comes from a run in this repository; none is illustrative.

> **The full report (52 pages, English) is the main deliverable:**
> [`rapport/rapport_PRe_EN.pdf`](rapport/rapport_PRe_EN.pdf)

---

## Design principle

One rule structures the whole project:

> **The model orchestrates, it does not compute. Deterministic first, model as
> fallback.**

Concretely: the language model chooses which tool to call and with what
arguments. It never produces a physical value, never edits a solver dictionary
directly, and never decides whether a safety criterion is met. Self-healing
tries a pattern-matched deterministic fix first, and only falls back to the
model when the error is unknown to the knowledge base, and even then the
model's diagnosis is displayed rather than applied.

---

## Headline results

### Reference simulation: a full two-year irradiation cycle

A PWR rod at 25 kW/m simulated over 6.3 × 10⁷ s. The chain reproduces the
expected physics: the pellet-cladding gap closes, and cladding hoop stress
switches from compression to tension at that moment.

| Criterion | Value | Limit | Status |
|---|---:|---:|---|
| Pellet centreline temperature | 1467 K | 3113 K | safe (47 % margin) |
| Cladding plastic strain (PCMI) | 0 | 1.00 % | safe |
| Cladding creep strain | 0.36 % | 1.00 % | safe (36 %) |
| Cladding hoop stress | 79.1 MPa | 250 MPa (`sigmaY` field) | safe (32 %) |
| Pellet-cladding gap | −8.0 µm | 5.0 µm | **exceeded** (contact established) |

The stress limit is not a constant: it is read point-by-point from the
`sigmaY` field the solver itself computes.

### Surrogate: dating a safety event, not just fitting a curve

| | Value |
|---|---|
| Predicted gap closure | 387 days |
| Computed by OFFBEAT | 370 days |
| Deviation | **4.4 %** (16 days over a 2-year cycle) |
| Cost | 2 ms vs 36 s, a factor of ≈ 18 000 |

At a linear power **absent from its training set**. Leave-one-out R² on the
realistic domain: 0.9951 (temperature), 0.9905 (creep strain), 0.9715 (gap
width).

### Measured evaluations

| Benchmark | Before | After | What the fix was |
|---|---|---|---|
| Tool selection (26 requests) | 77 % | **88 %** | Rewrote two overlapping tool descriptions |
| ↳ safety analyser alone | 25 % | **100 %** | " |
| Documentary retrieval, accuracy@1 | 27 % | **87 %** | Switched to a multilingual embedding model |
| ↳ recall@4 | 27 % | **100 %** | " |
| End-to-end 4-step request | 0 cases created, 30 calls, 179 s | **success, 5 calls, 39 s** | Tool schema accepted objects; context window 4096 → 16384 |
| LLM latency per call | 64 s (CPU) | **0.33 s** (GPU) | ≈ 190× |

None of these three failures (overlapping descriptions, degenerate training
domain, cross-lingual retrieval) was visible without measurement, and each was
fixed **without rewriting any application code**. What was missing was not
technique, but instrumentation.

### Self-healing: what it can and cannot repair

Fault injection, 14 cases (12 faults + 2 healthy controls):

| Situation | Repaired |
|---|---|
| Correction in base **and** cause treatable by it | 1/1 |
| Correction in base but cause **not** treatable by it | **0/3** |
| No correction in base for this pattern | 0/8 |

Self-healing repairs *exactly* the faults whose root cause falls within the
scope of the available correction, and only those. The middle row is the
finding: the pattern matches, the diagnosis prints, the fix fires, and it fails
every time, consuming the full retry budget. **The mechanism looks like it is
working while it structurally cannot succeed.**

The consequence is a shift of effort: validating inputs *before* execution
beats enriching the crash-pattern base. A case that cannot be physically valid
should never reach the solver.

---

## Fourteen correctness defects, none of which raised an error

The most dangerous defects found were not crashes but **plausible, correctly
formatted, wrong numbers**. A few, to give their flavour:

- A **closed** (hence negative) pellet-cladding gap was reported as *safe*: the
  ratio to the limit is a division, and a negative value flips its sign. This
  masked the only exceeded criterion of the reference case.
- "Maximum cladding temperature" read **1467 K** instead of 637 K, an error of
  830 K. The extremum was taken over the whole mesh, hence at the pellet
  centreline. The same cause reversed the *sign* of the hoop stress: tension
  reported where the cladding is in compression.
- The cladding stress criterion checked **350 MPa** while the solver applies
  **250 MPa**: a frozen constant, out of sync with the material model.
- The PCMI criterion states a limit on **plastic** strain but was reading
  **total** strain. Plastic strain is exactly zero here; the irreversible strain
  actually present is *creep*, which no criterion was monitoring.
- The surrogate passed leave-one-out cross-validation at R² > 0.998, on a
  domain 10 000× shorter than the problem of interest, where the rod has
  already reached thermal equilibrium and the second input variable carries
  almost no information. **A rigorously validated, useless model.**

The full table of fourteen is in §4.8 of the report. No automatic tool flagged
any of them; they were found by confronting a computed value with a physical
expectation.

---

## What is *not* validated

Stated plainly, because it bears on how any result here should be read:

- **The five safety thresholds in `offbeat_skills/safety_kb.json` are flagged
  as unvalidated.** They are published design orders of magnitude, not invented
  values, but confirming them is a prerequisite to any serious use of the safety
  analysis. This is the first thing I would like to go through with you.
- The **Zircaloy yield strength** is strongly temperature-dependent and is
  currently supplied as a single value. The architecture now reads it from the
  solver's `sigmaY` field, which removes the drift, but the underlying material
  model still deserves a check.
- The UO₂ **melting-temperature/burnup correlation** *was* settled during the
  internship: the project carried MATPRO's −3.2 K/(GWd/tU), superseded in
  FRAPCON-4.0/FRAPTRAN-2.0 by −0.5 K/(GWd/tU) (PNNL-19417 Rev. 2, subroutine
  `PHYPRP`). At 50 GWd/tU the two differ by 135 K on the limit.
- The surrogate covers **two parameters only** and is reliable **inside its
  training domain only**. Its uncertainty is displayed alongside every
  prediction for that reason.
- Scope is the **single rod**. Moving to the assembly changes the nature of the
  problem and is an open question.

---

## Repository layout

```
agents/supervisor.py      LangChain 1.x supervisor (create_agent)
config/llm_factory.py     Provider-agnostic model factory (Ollama / Anthropic / Gemini)
tools/
  input_creator.py        Generates an OFFBEAT case from a validated template
  offbeat_executor.py     Runs blockMesh + offbeat, self-healing loop
  data_processor.py       pyvista post-processing, zone-aware (fuel vs cladding)
  safety_analyzer.py      Evaluates the safety criteria
  surrogate.py            Gaussian-process surrogate (Matérn kernel)
  twin_monitor.py         Digital twin: monitoring, prognosis, dashboard
  rag_retriever.py        Documentary assistant (FAISS + multilingual embeddings)
evaluation/
  bench_selfhealing.py    Fault injection, 14 cases
  bench_tool_selection.py Tool selection, 26 requests
  bench_rag.py            Retrieval quality, 15 questions
  resultats_*.json        Raw results of the runs reported above
offbeat_skills/
  templates/              fuel_rod_1D_pwr, fuel_rod_2D_rz
  error_kb.json           Crash patterns → diagnosis → scripted fix
  safety_kb.json          Safety criteria (see caveat above)
  .surrogate/             Training dataset + fitted model (avoids re-running 25 sims)
rapport/                  LaTeX sources; en/ holds the English version by part
app.py                    Dash web interface
run_sim.py                LLM-free path: create → run → post-process, from the CLI
```

The benchmarks are re-runnable and shipped with the code: they are a
non-regression harness, not one-off measurements.

---

## Reproducing

Requires Linux (or WSL2): OpenFOAM and OFFBEAT are native Linux codes. Keep
the project and the cases on the native filesystem; OpenFOAM I/O is severely
degraded on a mounted host filesystem.

```bash
# 1. OpenFOAM v2506 + OFFBEAT (master, compiled from source)
source /usr/lib/openfoam/openfoam2506/etc/bashrc

# 2. Python
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then set OFFBEAT_BIN and the LLM provider

# 3. A local model (the project runs fully offline on Ollama)
ollama pull qwen2.5:7b
ollama pull bge-m3          # multilingual embeddings, see the RAG result above
```

Then:

```bash
python run_sim.py                    # LLM-free: create → run → post-process
python app.py                        # web interface, http://localhost:8000
python -m evaluation.bench_selfhealing
python -m evaluation.bench_tool_selection
python -m evaluation.bench_rag
```

`run_sim.py` is the "engine path": it exercises the whole physical chain
without any language model, and is the reliable way to reproduce the numbers
above.

---

## Report

| File | Contents |
|---|---|
| [`rapport/rapport_PRe_EN.pdf`](rapport/rapport_PRe_EN.pdf) | **Full report, English, 52 pages** |
| `rapport/rapport_PRe_EN.tex` | Assembled LaTeX source |
| `rapport/en/p01…p16.tex` | Source by part; edit here, then `cat en/p*.tex > rapport_PRe_EN.tex` |
| `rapport/compiler.sh --en` | Compiles in ~5 s (3 passes) |

Chapter 4 (results and stress-testing) and chapter 5 (false starts) are the
substantive ones.

---

## Next steps

In the order I would tackle them:

1. **Validate the safety thresholds.** Everything else rests on them.
2. **Settle the scope**: single rod, or assembly?
3. **Shift effort from repair to input validation.** The benchmark says this is
   where the reliability is.
4. **Extend the surrogate to a third variable** (initial gap geometry). A
   25-simulation design now takes half an hour; the code already handles an
   arbitrary number of variables.
5. **Fix time-step self-healing**: the correction is undone by the solver's
   adaptive controller. It should act on the *maximum* time step, not the
   initial one. The change is identified and localised.
6. **Extend the tool-selection benchmark to multi-step requests**: it currently
   evaluates only the first call, and the end-to-end session in appendix G
   shows that long chaining is where the agent still drops steps.

---

## Note on language

This README and the report are in English. Some internal development
documentation predates that decision and is still in French: `GUIDE.md`
(usage guide), `EXPLICATION_CODE.md` (code walkthrough), `CLAUDE.md` (coding
conventions), and `offbeat_skills/HOWTO_ajouter_une_erreur.md`. Happy to
translate any of them on request. The OFFBEAT documentation under
`offbeat_skills/docs/` is in English, as it comes from upstream.
