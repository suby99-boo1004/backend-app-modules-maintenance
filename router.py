from __future__ import annotations

from typing import List, Optional, Literal
from pathlib import Path
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.core.db import get_db
from app.core.dependencies import get_current_user_id

from .schemas import (
    MaintenanceListItem,
    MaintenanceCreateIn,
    MaintenanceDetailOut,
    MaintenanceCompleteIn,
)

router = APIRouter(prefix="/api/maintenance", tags=["maintenance"])


@router.get("", response_model=List[MaintenanceListItem])
def list_maintenance(
    year: int = Query(..., description="년도(예: 2026). 월/일 검색 없음."),
    tab: Literal["in_progress", "completed"] = Query("in_progress"),
    q: Optional[str] = Query(None, description="검색어(유지보수명/요청자소속/등록자)"),
    db: Session = Depends(get_db),
):
    from .service import MaintenanceService
    return MaintenanceService(db).list(year=year, tab=tab, q=q)


@router.post("", response_model=MaintenanceDetailOut)
def create_maintenance(
    payload: MaintenanceCreateIn,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    from .service import MaintenanceService
    return MaintenanceService(db).create(user_id, payload)


@router.get("/{ticket_id}", response_model=MaintenanceDetailOut)
def get_maintenance_detail(
    ticket_id: int,
    db: Session = Depends(get_db),
):
    from .service import MaintenanceService
    item = MaintenanceService(db).get_detail(ticket_id)
    if not item:
        raise HTTPException(status_code=404, detail="유지보수 내역을 찾을 수 없습니다.")
    return item


@router.post("/{ticket_id}/attachment")
async def upload_attachment(
    ticket_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """첨부파일 업로드 (A안: 처리완료 시 함께 업로드)
    - 허용: .zip, .pdf, .png, .jpg
    - 1개, 20MB 이하
    """
    filename = file.filename or ""
    ext = filename[filename.rfind("."):].lower() if "." in filename else ""
    allowed = {".zip", ".pdf", ".png", ".jpg"}
    if ext not in allowed:
        raise HTTPException(status_code=400, detail="허용되지 않는 파일 형식입니다. (.zip, .pdf, .png, .jpg)")

    max_bytes = 20 * 1024 * 1024
    size = 0
    chunks: list[bytes] = []
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        size += len(chunk)
        if size > max_bytes:
            raise HTTPException(status_code=400, detail="파일 용량은 20MB 이하만 가능합니다.")
        chunks.append(chunk)

    data = b"".join(chunks)

    # service.py 버전 불일치 문제를 피하기 위해 업로드 저장은 router에서 직접 수행
    base_dir = Path(__file__).resolve().parents[2] / "uploads" / "maintenance" / str(ticket_id)
    base_dir.mkdir(parents=True, exist_ok=True)

    safe_name = (filename or "file").replace("\\", "_").replace("/", "_").strip() or "file"
    stored_name = f"{uuid.uuid4().hex}_{safe_name}"
    file_path = base_dir / stored_name
    file_path.write_bytes(data)

    try:
        db.execute(
            text(
                """
                UPDATE public.maintenance_tickets
                SET attachment_name = :attachment_name,
                    attachment_path = :attachment_path,
                    attachment_mime = :attachment_mime,
                    attachment_size = :attachment_size,
                    updated_at = now()
                WHERE id = :id
                """
            ),
            {
                "id": ticket_id,
                "attachment_name": safe_name,
                "attachment_path": str(file_path),
                "attachment_mime": file.content_type or "",
                "attachment_size": len(data),
            },
        )
        db.commit()
    except Exception:
        db.rollback()

    return {
        "ok": True,
        "filename": safe_name,
        "stored": stored_name,
        "size": len(data),
        "content_type": file.content_type or "",
    }


@router.post("/{ticket_id}/complete", response_model=MaintenanceDetailOut)
def complete_maintenance(
    ticket_id: int,
    payload: MaintenanceCompleteIn,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    from .service import MaintenanceService
    return MaintenanceService(db).complete(ticket_id=ticket_id, actor_user_id=user_id, payload=payload)
