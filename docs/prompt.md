# Política do agente

O conteúdo completo das instruções operacionais usado pelo agente em produção está em [`app/agent_instructions.md`](../app/agent_instructions.md). Esse arquivo é a fonte única das regras do agente; altere-o para mudar o comportamento.

A instrução auxiliar que interpreta mensagens e histórico permanece em `TURN_INTERPRETER_INSTRUCTIONS`, em [`app/agent.py`](../app/agent.py). Ela orienta a leitura do turno e não substitui as regras de atendimento.
