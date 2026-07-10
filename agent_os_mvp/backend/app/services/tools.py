from __future__ import annotations

from typing import Callable


def tkms_search(query: str) -> str:
    return f"Mock TKMS search results for: {query}"


def oracle_query_readonly(query: str) -> str:
    return f"Mock Oracle readonly rows for: {query}"


def gitlab_create_mr_draft(title: str) -> str:
    return f"Mock GitLab MR draft created: {title}"


def obsidian_write_draft(content: str) -> str:
    return f"Mock Obsidian draft saved with {len(content)} characters"


TOOL_REGISTRY: dict[str, Callable[[str], str]] = {
    "tkms_search": tkms_search,
    "oracle_query_readonly": oracle_query_readonly,
    "gitlab_create_mr_draft": gitlab_create_mr_draft,
    "obsidian_write_draft": obsidian_write_draft,
}


ROLE_TOOL_WHITELIST: dict[str, tuple[str, ...]] = {
    "Planner": ("obsidian_write_draft",),
    "Research": ("tkms_search", "oracle_query_readonly", "obsidian_write_draft"),
    "Dev": ("gitlab_create_mr_draft", "obsidian_write_draft"),
    "QA": ("oracle_query_readonly", "obsidian_write_draft"),
    "Reviewer": ("obsidian_write_draft",),
}


def run_tool_for_role(role: str, tool_name: str, payload: str) -> str:
    if tool_name not in TOOL_REGISTRY:
        raise ValueError(f"Tool '{tool_name}' is not registered")
    allowed_tools = ROLE_TOOL_WHITELIST.get(role, ())
    if tool_name not in allowed_tools:
        raise PermissionError(f"Role '{role}' cannot use tool '{tool_name}'")
    return TOOL_REGISTRY[tool_name](payload)
