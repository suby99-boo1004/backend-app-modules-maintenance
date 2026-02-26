from __future__ import annotations

from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel


class MaintenanceCreateIn(BaseModel):
    title: str
    requester_name: str
    requester_org: str
    requester_phone: str
    request_content: str


class MaintenanceListItem(BaseModel):
    id: int
    requested_at: datetime
    title: str
    requester_name: str = ""
    requester_org: str = ""
    created_by_name: str = ""


class MaintenanceCompleteIn(BaseModel):
    resolution_content: str
    assignee_user_ids: List[int] = []


class AssigneeOut(BaseModel):
    user_id: int
    name: str = ""


class MaintenanceDetailOut(BaseModel):
    id: int
    title: str
    requested_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None

    created_by_user_id: Optional[int] = None
    created_by_name: str = ""

    requester_name: str = ""
    requester_org: str = ""
    requester_phone: str = ""
    request_content: str = ""

    resolution_content: Optional[str] = None
    assignees: List[AssigneeOut] = []

    # 첨부파일 메타
    attachment_name: Optional[str] = None
    attachment_path: Optional[str] = None
    attachment_mime: Optional[str] = None
    attachment_size: Optional[int] = None

    # 다운로드 URL (서버 경로 노출 방지)
    attachment_download_url: Optional[str] = None

    class Config:
        from_attributes = True
