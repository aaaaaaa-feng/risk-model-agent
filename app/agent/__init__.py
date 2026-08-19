"""Agent adapters.

V1 intentionally ships an offline, rule-based assistant. It never computes model
metrics itself and it cannot approve or execute a plan.
"""

from app.agent.rule_based import build_agent_response

__all__ = ["build_agent_response"]
