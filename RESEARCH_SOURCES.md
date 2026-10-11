# Fuentes de la integración LangGraph + PydanticAI

- LangGraph interrupts: https://docs.langchain.com/oss/python/langgraph/interrupts
  - `interrupt()` pausa el grafo, persiste el estado mediante checkpointer y permite reanudar con `Command` y el mismo `thread_id`.
- LangGraph persistence/durable execution: https://docs.langchain.com/oss/python/langgraph/durable-execution
  - Checkpointer para estado de hilo, aprobación humana y tolerancia a fallos; Store para memoria de largo plazo entre hilos.
- PydanticAI Harness: https://pydantic.dev/docs/ai/harness/
  - Harnesses/capacidades componibles como Coder, Researcher, Memory, Guardrails, sandboxes, subagentes y workflows dinámicos.
- PydanticAI durable execution: https://pydantic.dev/docs/ai/capabilities/durable_execution/overview/
  - Soporte para motores durables como Temporal, DBOS, Prefect, Restate y AWS Lambda; un solo motor durable por agente.
- OpenAI guardrails y aprobaciones: https://developers.openai.com/api/docs/guides/agents/guardrails-approvals
  - Guardrails para checks automáticos y revisión humana para efectos sensibles.
- Temporal Durable AI: https://docs.temporal.io/ai
  - Workflows que sobreviven crashes, timeouts y esperas de aprobación humana.
