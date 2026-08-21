"""
supervisor.py : Agent superviseur d'AutoOFFBEAT (langchain 1.x).

Migré vers la nouvelle API LangGraph : `create_agent` remplace le couple
`create_tool_calling_agent` + `AgentExecutor` de langchain 0.3, et la
mémoire de conversation est gérée par un *checkpointer* (InMemorySaver)
indexé par `thread_id`, au lieu de `RunnableWithMessageHistory`.

L'agent reste agnostique au LLM : il fonctionne tel quel avec
ChatAnthropic, ChatGoogleGenerativeAI ou ChatOllama (voir llm_factory),
du moment que le modèle supporte le tool-calling.

Conventions d'usage (cf. app.py) :
    agent = build_supervisor()
    cfg   = {"configurable": {"thread_id": "<session>"}}
    out   = agent.invoke({"messages": [{"role": "user", "content": txt}]}, cfg)
    reply = out["messages"][-1].content
"""

from langchain.agents import create_agent
from langgraph.checkpoint.memory import InMemorySaver

from config.llm_factory import get_supervisor_llm
from tools.offbeat_executor import OffbeatExecutorTool
from tools.input_creator import OffbeatInputCreatorTool
from tools.data_processor import OffbeatDataProcessorTool
from tools.safety_analyzer import OffbeatSafetyAnalyzerTool

SYSTEM_PROMPT = """Tu es AutoOFFBEAT, un assistant expert en performance
du combustible nucléaire avec le solveur OFFBEAT (basé sur OpenFOAM).

Tes capacités, via tes outils :
1. input_creator : créer un cas OpenFOAM/OFFBEAT en partant d'un template
   validé des offbeat_skills (ex. fuel_rod_1D_pwr) et en surchargeant les
   paramètres demandés (puissance linéique, rayons/hauteurs du barreau,
   durée de simulation, enrichissement…).
2. offbeat_executor : mailler le cas (rodMaker.py + blockMesh) puis lancer
   le solveur `offbeat`, surveiller log.offbeat, et tenter une
   auto-réparation en cas de crash (self-healing).
3. data_processor : post-traiter les résultats (champs T, contraintes,
   déformations) via pyvista et produire des profils/figures/JSON.
4. safety_analyzer : analyser la SÛRETÉ d'un crayon simulé (jumeau numérique) :
   comparer T, contraintes/déformations de gaine, gap et oxydation aux seuils
   de conception et renvoyer un statut 🟢/🟡/🔴 par critère, avec un pronostic
   (quand un seuil sera franchi). À utiliser pour détecter une situation de
   danger (fusion combustible, PCMI, rupture de gaine).
5. surrogate_predict : (si disponible) prédire INSTANTANÉMENT les marges de
   sûreté pour une puissance linéique donnée, sans relancer le solveur. À
   utiliser pour les questions « et si la puissance était de X W/m ? ».

Règles :
- Travaille toujours dans le répertoire de cas indiqué par l'utilisateur
  (monté sous /host, comme dans AutoFLUKA).
- Enchaîne logiquement : créer le cas -> l'exécuter -> post-traiter.
- Les rayons/hauteurs du rodDict sont en mm, la puissance linéique en W/m,
  les temps en secondes.
- Si l'executor rapporte un échec après self-healing, explique le
  diagnostic à l'utilisateur et propose une correction manuelle.
- Réponds dans la langue de l'utilisateur.
"""


def build_supervisor():
    """Construit l'agent superviseur (graphe LangGraph compilé), prêt à être
    appelé depuis un callback Dash. La mémoire est portée par le checkpointer
    interne ; chaque conversation est isolée par son `thread_id`."""
    llm = get_supervisor_llm()

    tools = [
        OffbeatInputCreatorTool(),
        OffbeatExecutorTool(),
        OffbeatDataProcessorTool(),
        OffbeatSafetyAnalyzerTool(),
    ]

    # Outil RAG optionnel : ajouté seulement s'il est prêt (index construit +
    # modèle d'embeddings dispo). S'il échoue, l'agent démarre sans lui,
    # le RAG ne doit jamais empêcher le fonctionnement de base.
    try:
        from tools.rag_retriever import get_knowledge_tool
        tools.append(get_knowledge_tool())
    except Exception as exc:  # noqa: BLE001
        import logging
        logging.getLogger("autooffbeat.supervisor").info(
            "RAG désactivé (%s). Pour l'activer : ollama pull nomic-embed-text "
            "puis python -m tools.rag_retriever --ingest", exc)

    # Outil surrogate optionnel (jumeau numérique D5) : ajouté seulement si un
    # modèle a été entraîné (offbeat_skills/.surrogate/model.joblib). Permet
    # des prédictions de sûreté « what-if » instantanées, sans relancer OFFBEAT.
    try:
        from tools.surrogate import OffbeatSurrogateTool, MODEL_PATH
        if MODEL_PATH.exists():
            tools.append(OffbeatSurrogateTool())
    except Exception as exc:  # noqa: BLE001
        import logging
        logging.getLogger("autooffbeat.supervisor").info(
            "Surrogate désactivé (%s). Pour l'activer : "
            "python -m tools.surrogate build --lhgr ... puis train.", exc)

    return create_agent(
        model=llm,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
        checkpointer=InMemorySaver(),
    )


if __name__ == "__main__":
    # Test rapide en CLI, indépendant du GUI.
    supervisor = build_supervisor()
    config = {"configurable": {"thread_id": "cli-test"}}
    while True:
        user_input = input("\nVous > ")
        if user_input.lower() in {"exit", "quit"}:
            break
        result = supervisor.invoke(
            {"messages": [{"role": "user", "content": user_input}]},
            config=config,
        )
        print(f"\nAutoOFFBEAT > {result['messages'][-1].content}")
