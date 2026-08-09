from app.safety import protect_customer_decision
from app.schemas import AgentDecision


def test_sensitive_agent_reply_is_replaced_before_customer_delivery():
    decision = protect_customer_decision(AgentDecision(reply="IMEI 123456789012345"))

    assert decision.handoff is True
    assert "IMEI" not in decision.reply
