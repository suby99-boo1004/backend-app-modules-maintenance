from __future__ import annotations

from typing import List, Optional
from pydantic import BaseModel, Field


class MaintenanceListItem(BaseModel):
    id: int
    requested_at: str
    title: str
    requester_name: str
    requester_org: str
    created_by_name: str = ""


class MaintenanceCreateIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=200, description="유지보수명")
    requester_name: str = Field(..., min_length=1, max_length=100, description="요청자")
    requester_org: str = Field(..., min_length=1, max_length=120, description="요청자 소속")
    requester_phone: str = Field(..., min_length=1, max_length=50, description="연락처")
    request_content: str = Field(..., min_length=1, description="요청 내용")


class AssigneeOut(BaseModel):
    user_id: int
    name: str = ""


class MaintenanceDetailOut(BaseModel):
    id: int
    title: str
    requested_at: str
    closed_at: Optional[str] = None

    created_by_user_id: Optional[int] = None
    created_by_name: str = ""

    requester_name: str = ""
    requester_org: str = ""
    requester_phone: str = ""
    request_content: str = ""

    resolution_content: Optional[str] = None
    assignees: List[AssigneeOut] = []


class MaintenanceCompleteIn(BaseModel):
    resolution_content: str = Field(..., min_length=1, description="처리 내용(필수)")
    assignee_user_ids: List[int] = Field(default_factory=list, description="처리 참여자(복수) user_id 목록")
