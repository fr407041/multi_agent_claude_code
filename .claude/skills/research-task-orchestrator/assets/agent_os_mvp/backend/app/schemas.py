from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class AgentRole(str, Enum):
    planner = "Planner"
    research = "Research"
    dev = "Dev"
    qa = "QA"
    reviewer = "Reviewer"


class GoalCreate(BaseModel):
    title: str = Field(min_length=3, max_length=120)
    description: str = Field(min_length=10, max_length=2000)


class GoalRecord(BaseModel):
    id: int
    title: str
    description: str
    status: str
    created_at: datetime
    updated_at: datetime


class TaskRecord(BaseModel):
    id: int
    goal_id: int
    title: str
    description: str
    agent_role: AgentRole
    status: str
    priority: int
    result_summary: str | None = None
    created_at: datetime
    updated_at: datetime


class AgentRunRecord(BaseModel):
    id: int
    goal_id: int
    task_id: int | None
    agent_role: AgentRole
    tool_name: str | None = None
    status: str
    input_payload: str
    output_payload: str
    created_at: datetime


class ReviewRecord(BaseModel):
    id: int
    goal_id: int
    task_id: int
    reviewer_role: AgentRole
    verdict: str
    notes: str
    created_at: datetime


class WorkBuddyRecord(BaseModel):
    id: int
    goal_id: int
    task_id: int
    primary_role: AgentRole
    buddy_role: AgentRole
    collaboration_note: str
    status: str
    created_at: datetime
    updated_at: datetime


class AuditLogRecord(BaseModel):
    id: int
    goal_id: int | None = None
    task_id: int | None = None
    actor: str
    action: str
    details: str
    created_at: datetime


class GoalCreatedResponse(BaseModel):
    goal: GoalRecord
    tasks: list[TaskRecord]


class DashboardResponse(BaseModel):
    goals: list[GoalRecord]
    tasks: list[TaskRecord]
    agent_runs: list[AgentRunRecord]
    reviews: list[ReviewRecord]
    workbuddies: list[WorkBuddyRecord]
    audit_logs: list[AuditLogRecord]
