from __future__ import annotations

from typing import Any

from app.config import normalize_phone
from app.storage.database import Repository


_INDIVIDUAL_ACTIONS = {
    "assume": "human_active",
    "resume": "bot_active",
    "close": "closed",
}


class AdminCommandService:
    """Apply the allowlisted conversation commands used by admin surfaces."""

    def __init__(self, repository: Repository):
        self.repository = repository

    def execute(
        self,
        action: str,
        *,
        operator: str,
        channel: str,
        phone: str | None = None,
    ) -> dict[str, Any]:
        action = str(action or "").strip().lower()
        if action != "release_all" and action not in _INDIVIDUAL_ACTIONS:
            raise ValueError("Ação administrativa desconhecida")

        if action == "release_all":
            if phone:
                raise ValueError("Liberar todos não aceita telefone")
            released_count = self.repository.release_all_human_conversations(
                f"Comando {action} via {channel} por {operator}"
            )
            result = {
                "action": action,
                "phone": None,
                "status": "bot_active",
                "released_count": released_count,
                "message": (
                    f"{released_count} conversa(s) em atendimento humano "
                    "foram liberadas para o robô."
                ),
            }
            self.repository.audit(
                "admin_command",
                None,
                {
                    "action": action,
                    "channel": channel,
                    "operator": operator,
                    "released_count": released_count,
                },
            )
            return result

        normalized_phone = normalize_phone(phone)
        if not 10 <= len(normalized_phone) <= 15:
            raise ValueError("Telefone inválido")
        status = _INDIVIDUAL_ACTIONS[action]
        self.repository.set_conversation_status(
            normalized_phone,
            status,
            f"Comando {action} via {channel} por {operator}",
        )
        self.repository.audit(
            "admin_command",
            normalized_phone,
            {
                "action": action,
                "channel": channel,
                "operator": operator,
                "released_count": 0,
            },
        )
        return {
            "action": action,
            "phone": normalized_phone,
            "status": status,
            "released_count": 0,
            "message": f"Conversa {normalized_phone}: status alterado para {status}.",
        }
