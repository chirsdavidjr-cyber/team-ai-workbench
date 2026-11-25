"""Agent 包：function calling 闭环。

对外仍可通过 ``from app.agent import agent_stream, WORKBENCH_HINT, ...`` 使用。
"""

from .prompts import BASE_RULES, HANDOVER_HINT, WORKBENCH_HINT
from .specs import (
    ACTION_GROUPS,
    ALL_ACTION_NAMES,
    TOOL_SPECS,
    clean_allowed_actions,
    expand_actions,
    filter_tool_specs,
    scoped_hint,
)
from .runner import MAX_ROUNDS, TOOL_RESULT_MAX, agent_stream
from .actions import run_tool_action
from .confirm import DELETE_OPS, PendingConfirmError

__all__ = [
    "ACTION_GROUPS",
    "ALL_ACTION_NAMES",
    "BASE_RULES",
    "DELETE_OPS",
    "HANDOVER_HINT",
    "MAX_ROUNDS",
    "PendingConfirmError",
    "TOOL_RESULT_MAX",
    "TOOL_SPECS",
    "WORKBENCH_HINT",
    "agent_stream",
    "clean_allowed_actions",
    "expand_actions",
    "filter_tool_specs",
    "run_tool_action",
    "scoped_hint",
]
