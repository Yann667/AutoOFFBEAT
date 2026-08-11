"""
app.py – GUI Dash d'AutoOFFBEAT.

Structure identique à autofluka_app.py :
  - fenêtre de chat + dcc.Store pour l'historique côté client
  - session_id fixe (app mono-utilisateur) transmis à RunnableWithMessageHistory
  - spinner pendant l'appel à l'agent

Le superviseur est instancié une seule fois au démarrage du module.
"""

import os
import re
import base64
import logging
import uuid

import dash
from dash import Dash, dcc, html, Input, Output, State
from dash.exceptions import PreventUpdate

from agents.supervisor import build_supervisor
from tools.safety_analyzer import analyze          # D4 : tableau de bord surete
from tools.surrogate import predict as surrogate_predict, MODEL_PATH as SURROGATE_MODEL

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_DIR = os.path.join(os.path.dirname(__file__), "AutoOFFBEAT_logs")
os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, "autooffbeat.log")),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("autooffbeat.app")

# ── Agent (singleton) ─────────────────────────────────────────────────────────
# Le checkpointer LangGraph gère la mémoire par thread_id.
supervisor = build_supervisor()
SESSION_ID = str(uuid.uuid4())          # une session par démarrage de conteneur
INVOKE_CONFIG = {"configurable": {"thread_id": SESSION_ID}}

# ── Dash app ──────────────────────────────────────────────────────────────────
app = Dash(
    __name__,
    title="AutoOFFBEAT",
    suppress_callback_exceptions=True,
)

app.layout = html.Div([
    html.Div([
        html.H1("AutoOFFBEAT", style={"margin": "0"}),
        html.P(
            "Agent LLM pour les simulations OFFBEAT / OpenFOAM",
            style={"margin": "4px 0 0", "color": "#888", "fontSize": "0.9em"},
        ),
    ], style={"padding": "16px 24px", "borderBottom": "1px solid #e0e0e0"}),

    # Corps : chat (gauche) + tableau de bord de sûreté (droite)
    html.Div([

        # ── Colonne chat ──────────────────────────────────────────────
        html.Div([
            html.Div(id="chat-window", style={
                "flex": "1", "overflowY": "auto", "padding": "16px 24px",
                "display": "flex", "flexDirection": "column", "gap": "12px",
            }),
            # Zone de saisie
            html.Div([
                dcc.Textarea(
                    id="user-input",
                    placeholder="Décris la simulation que tu veux lancer…",
                    style={"flex": "1", "resize": "vertical", "minHeight": "60px",
                           "fontFamily": "inherit", "fontSize": "0.95em",
                           "padding": "8px", "border": "1px solid #ccc",
                           "borderRadius": "6px"},
                ),
                html.Div([
                    html.Button("Envoyer", id="send-btn", n_clicks=0, style={
                        "background": "#1a73e8", "color": "#fff",
                        "border": "none", "borderRadius": "6px",
                        "padding": "8px 20px", "cursor": "pointer",
                    }),
                    html.Button("Effacer", id="clear-btn", n_clicks=0, style={
                        "background": "#f1f3f4", "color": "#333",
                        "border": "none", "borderRadius": "6px",
                        "padding": "8px 20px", "cursor": "pointer",
                    }),
                ], style={"display": "flex", "gap": "8px", "alignItems": "flex-end"}),
            ], style={
                "display": "flex", "gap": "12px", "padding": "12px 24px",
                "borderTop": "1px solid #e0e0e0", "alignItems": "flex-start",
            }),
        ], style={"flex": "1", "display": "flex", "flexDirection": "column",
                  "minWidth": "0"}),

        # ── Barre latérale : sûreté du crayon (jumeau numérique, D4) ────
        html.Div([
            html.H3("🛡️ Sûreté du crayon", style={"margin": "0 0 4px"}),
            html.P("Jumeau numérique — marges de sûreté du cas simulé.",
                   style={"margin": "0 0 12px", "color": "#888",
                          "fontSize": "0.8em"}),
            dcc.Input(
                id="safety-case-dir", type="text",
                placeholder="/chemin/vers/le/cas",
                style={"width": "100%", "padding": "6px", "boxSizing": "border-box",
                       "border": "1px solid #ccc", "borderRadius": "6px",
                       "fontSize": "0.85em"},
            ),
            html.Div([
                html.Button("Analyser", id="safety-btn", n_clicks=0, style={
                    "background": "#188038", "color": "#fff", "border": "none",
                    "borderRadius": "6px", "padding": "6px 16px",
                    "cursor": "pointer", "fontSize": "0.85em"}),
                dcc.Checklist(
                    id="safety-auto", options=[{"label": " Auto (15 s)", "value": "on"}],
                    value=[], style={"fontSize": "0.8em", "display": "flex",
                                     "alignItems": "center"}),
            ], style={"display": "flex", "gap": "10px", "alignItems": "center",
                      "margin": "8px 0"}),
            dcc.Interval(id="safety-interval", interval=15000, disabled=True),
            html.Div(id="safety-panel"),

            # ── Simulateur instantané (émulateur D5, sans solveur ni LLM) ──
            html.Hr(style={"margin": "18px 0", "border": "none",
                           "borderTop": "1px solid #e0e0e0"}),
            html.H3("🎛️ Simulateur instantané", style={"margin": "0 0 4px"}),
            html.P("Prédiction émulateur (sans solveur) — bouge les curseurs.",
                   style={"margin": "0 0 12px", "color": "#888",
                          "fontSize": "0.8em"}),
            html.Label("Puissance linéique (W/m)", style={
                "fontSize": "0.8em", "fontWeight": "600", "display": "block"}),
            dcc.Slider(id="wi-lhgr", min=12000, max=36000, step=1000, value=24000,
                       marks={12000: "12k", 18000: "18k", 24000: "24k",
                              30000: "30k", 36000: "36k"},
                       tooltip={"placement": "bottom", "always_visible": False}),
            html.Label("Durée simulée (s)", style={
                "fontSize": "0.8em", "fontWeight": "600", "display": "block",
                "marginTop": "10px"}),
            dcc.Slider(id="wi-endtime", min=2000, max=6500, step=250, value=3500,
                       marks={2000: "2000", 3500: "3500", 5000: "5000",
                              6500: "6500"},
                       tooltip={"placement": "bottom", "always_visible": False}),
            html.Div(id="whatif-panel", style={"marginTop": "12px"}),
        ], style={
            "width": "320px", "flexShrink": "0", "padding": "16px",
            "borderLeft": "1px solid #e0e0e0", "overflowY": "auto",
            "background": "#fafafa",
        }),

    ], style={"flex": "1", "display": "flex", "minHeight": "0"}),

    # Stockage côté client de l'historique affiché
    dcc.Store(id="conversation-store", data=[]),

    dcc.Loading(id="loading", type="circle",
                children=html.Div(id="loading-dummy")),
], style={
    "display": "flex", "flexDirection": "column",
    "height": "100vh", "fontFamily": "Inter, sans-serif",
})


# ── Helpers figures ───────────────────────────────────────────────────────────

# Repère les chemins de PNG cités dans la réponse de l'agent (data_processor
# renvoie « Figures générées :\n  /chemin/axial_T.png … »).
_PNG_RE = re.compile(r"(/\S+?\.png)")


def _extract_figure_paths(text: str) -> list[str]:
    """Chemins de PNG mentionnés dans le texte ET existant réellement."""
    seen, paths = set(), []
    for p in _PNG_RE.findall(text or ""):
        if p not in seen and os.path.isfile(p):
            seen.add(p)
            paths.append(p)
    return paths


def _encode_image(path: str) -> str | None:
    """Encode un PNG en data-URI base64 (affichable sans route statique)."""
    try:
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        return f"data:image/png;base64,{b64}"
    except OSError:
        return None


# ── Helpers tableau de bord sûreté (D4) ───────────────────────────────────────

_STATUS_COLOR = {"🟢": "#188038", "🟡": "#f9ab00", "🔴": "#d93025", "⚪": "#9aa0a6"}
_CRIT_LABEL = {
    "fuel_centerline_melt":      "Fusion combustible (T cœur)",
    "cladding_hoop_strain_pcmi": "PCMI — déformation gaine",
    "cladding_hoop_stress":      "Contrainte gaine (hoop)",
    "gap_closure_pcmi_onset":    "Fermeture du gap",
}


def _gauge_row(crit: dict):
    """Une ligne de jauge : pastille de couleur, libellé, valeur/limite, barre."""
    color = _STATUS_COLOR.get(crit["status"], "#9aa0a6")
    label = _CRIT_LABEL.get(crit["id"], crit["id"])
    ratio = crit.get("ratio")
    if crit["status"] == "⚪" or ratio is None:
        detail = crit.get("note", "non évaluable")
        bar_pct = 0
    else:
        unit = crit["unit"] if crit["unit"] != "-" else ""
        detail = (f"{crit['value']:.4g} {unit} / {crit['limit']:.4g} {unit} "
                  f"— {ratio*100:.0f}% du seuil")
        bar_pct = max(0, min(ratio * 100, 100))     # borne 0–100 %

    children = [
        html.Div([
            html.Span("●", style={"color": color, "marginRight": "6px"}),
            html.Span(label, style={"fontWeight": "600", "fontSize": "0.85em"}),
        ]),
        html.Div(detail, style={"fontSize": "0.75em", "color": "#555",
                                "margin": "2px 0 4px"}),
        # barre de progression vers le seuil
        html.Div(html.Div(style={
            "width": f"{bar_pct}%", "height": "6px", "background": color,
            "borderRadius": "3px"}),
            style={"width": "100%", "height": "6px", "background": "#e6e6e6",
                   "borderRadius": "3px"}),
    ]
    if crit.get("prognosis"):
        children.append(html.Div("⏱ " + crit["prognosis"],
                                 style={"fontSize": "0.72em", "color": "#a15c00",
                                        "fontStyle": "italic", "marginTop": "4px"}))
    return html.Div(children, style={"margin": "0 0 14px"})


def render_safety_panel(case_dir: str):
    """Construit le panneau de sûreté pour un cas, ou un message d'aide."""
    if not case_dir or not case_dir.strip():
        return html.P("Renseigne un chemin de cas puis clique « Analyser ».",
                      style={"fontSize": "0.8em", "color": "#888"})
    if not os.path.isdir(case_dir.strip()):
        return html.P(f"Répertoire introuvable : {case_dir}",
                      style={"fontSize": "0.8em", "color": "#d93025"})
    try:
        report = analyze(case_dir.strip())
    except Exception as exc:  # noqa: BLE001 : le panneau ne doit jamais casser l'app
        return html.P(f"Analyse impossible : {exc}",
                      style={"fontSize": "0.8em", "color": "#d93025"})

    overall = report["overall"]
    banner_txt = {"🔴": "DANGER — critère franchi",
                  "🟡": "VIGILANCE — proche du seuil",
                  "🟢": "SÛR — dans les marges"}[overall]
    banner = html.Div(f"{overall} {banner_txt}", style={
        "background": _STATUS_COLOR[overall], "color": "#fff",
        "padding": "8px 10px", "borderRadius": "8px", "fontWeight": "600",
        "fontSize": "0.85em", "marginBottom": "12px", "textAlign": "center"})
    rows = [_gauge_row(c) for c in report["criteria"]]
    return [banner] + rows


# ── Helpers simulateur instantané (émulateur D5) ──────────────────────────────

_TARGET_LABEL = {
    "peak_T":           "T à cœur",
    "peak_hoop_strain": "Déformation gaine (PCMI)",
    "min_gap":          "Gap minimal",
}


def _surrogate_row(crit: dict):
    """Ligne de jauge pour une métrique prédite par l'émulateur."""
    color = _STATUS_COLOR.get(crit["status"], "#9aa0a6")
    label = _TARGET_LABEL.get(crit["target"], crit["target"])
    ratio = crit["ratio"]
    unit = crit["unit"] if crit["unit"] != "-" else ""
    bar_pct = max(0, min(ratio * 100, 100))
    unc = f" ± {crit['std']:.3g}" if crit.get("std") is not None else ""
    return html.Div([
        html.Div([
            html.Span("●", style={"color": color, "marginRight": "6px"}),
            html.Span(label, style={"fontWeight": "600", "fontSize": "0.85em"}),
        ]),
        html.Div(f"{crit['predicted']:.4g}{unc} {unit} — {ratio*100:.0f}% du seuil",
                 style={"fontSize": "0.75em", "color": "#555",
                        "margin": "2px 0 4px"}),
        html.Div(html.Div(style={"width": f"{bar_pct}%", "height": "6px",
                                 "background": color, "borderRadius": "3px"}),
                 style={"width": "100%", "height": "6px", "background": "#e6e6e6",
                        "borderRadius": "3px"}),
    ], style={"margin": "0 0 12px"})


def render_surrogate_panel(lhgr, end_time):
    """Panneau du simulateur : prédiction instantanée (émulateur), ou message
    d'aide si le modèle n'est pas encore entraîné."""
    if not SURROGATE_MODEL.exists():
        return html.P("Émulateur non entraîné. Lance : "
                      "python -m tools.surrogate build --lhgr ... puis train.",
                      style={"fontSize": "0.78em", "color": "#888"})
    try:
        r = surrogate_predict({"linear_heat_rate": lhgr, "end_time": end_time})
    except Exception as exc:  # noqa: BLE001
        return html.P(f"Prédiction indisponible : {exc}",
                      style={"fontSize": "0.78em", "color": "#d93025"})
    overall = r["overall"]
    banner_txt = {"🔴": "DANGER prévu", "🟡": "VIGILANCE", "🟢": "SÛR"}[overall]
    banner = html.Div(f"{overall} {banner_txt}", style={
        "background": _STATUS_COLOR[overall], "color": "#fff",
        "padding": "6px 10px", "borderRadius": "8px", "fontWeight": "600",
        "fontSize": "0.82em", "marginBottom": "10px", "textAlign": "center"})
    return [banner] + [_surrogate_row(c) for c in r["criteria"]]


# ── Callbacks ─────────────────────────────────────────────────────────────────

@app.callback(
    Output("whatif-panel", "children"),
    Input("wi-lhgr", "value"),
    Input("wi-endtime", "value"),
)
def update_whatif(lhgr, end_time):
    """Recalcule la prédiction émulateur à chaque mouvement de curseur (~ms)."""
    return render_surrogate_panel(lhgr, end_time)


@app.callback(
    Output("safety-interval", "disabled"),
    Input("safety-auto", "value"),
)
def toggle_safety_auto(auto_value):
    """Active/désactive le rafraîchissement automatique du panneau de sûreté."""
    return "on" not in (auto_value or [])


@app.callback(
    Output("safety-panel", "children"),
    Input("safety-btn", "n_clicks"),
    Input("safety-interval", "n_intervals"),
    State("safety-case-dir", "value"),
    prevent_initial_call=True,
)
def update_safety_panel(_clicks, _ticks, case_dir):
    """Recalcule les jauges de sûreté (au clic ou au tick auto)."""
    return render_safety_panel(case_dir)


@app.callback(
    Output("conversation-store", "data"),
    Output("user-input", "value"),
    Output("loading-dummy", "children"),
    Input("send-btn", "n_clicks"),
    State("user-input", "value"),
    State("conversation-store", "data"),
    prevent_initial_call=True,
)
def handle_send(_send, user_text, history):
    if not user_text or not user_text.strip():
        raise PreventUpdate

    log.info("User : %s", user_text.strip())
    try:
        result = supervisor.invoke(
            {"messages": [{"role": "user", "content": user_text.strip()}]},
            config=INVOKE_CONFIG,
        )
        messages = result.get("messages", [])
        reply = messages[-1].content if messages else "(pas de réponse)"
    except Exception as exc:  # noqa: BLE001
        log.exception("Erreur agent")
        reply = f"**Erreur** : {exc}"

    log.info("Agent : %s", reply[:200])
    figures = _extract_figure_paths(reply)
    history = (history or []) + [
        {"role": "user", "content": user_text.strip()},
        {"role": "assistant", "content": reply, "figures": figures},
    ]
    return history, "", ""


@app.callback(
    Output("chat-window", "children"),
    Input("conversation-store", "data"),
)
def render_chat(history):
    if not history:
        return []
    bubbles = []
    for msg in history:
        is_user = msg["role"] == "user"
        content = [dcc.Markdown(msg["content"])]

        # Affiche les figures PNG (encodées en base64) sous la réponse.
        for fig_path in msg.get("figures", []):
            data_uri = _encode_image(fig_path)
            if not data_uri:
                continue
            content.append(html.Img(
                src=data_uri,
                style={"maxWidth": "100%", "marginTop": "8px",
                       "borderRadius": "8px", "border": "1px solid #ddd",
                       "background": "#fff", "display": "block"},
            ))

        bubbles.append(html.Div(
            content,
            style={
                "alignSelf": "flex-end" if is_user else "flex-start",
                "background": "#1a73e8" if is_user else "#f1f3f4",
                "color": "#fff" if is_user else "#202124",
                "padding": "10px 14px",
                "borderRadius": "14px",
                "maxWidth": "75%" if is_user else "85%",
                "fontSize": "0.93em",
            },
        ))
    return bubbles


@app.callback(
    Output("conversation-store", "data", allow_duplicate=True),
    Input("clear-btn", "n_clicks"),
    prevent_initial_call=True,
)
def clear_chat(_):
    # Nouveau thread_id → nouvelle mémoire (le checkpointer repart de zéro)
    global SESSION_ID, INVOKE_CONFIG
    SESSION_ID = str(uuid.uuid4())
    INVOKE_CONFIG = {"configurable": {"thread_id": SESSION_ID}}
    return []


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    debug = os.getenv("DEBUG", "false").lower() == "true"
    log.info("Démarrage AutoOFFBEAT sur le port %d (debug=%s)", port, debug)
    app.run(host="0.0.0.0", port=port, debug=debug)
