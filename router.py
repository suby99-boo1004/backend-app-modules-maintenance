from __future__ import annotations

from typing import List, Optional, Literal
from pathlib import Path
import uuid
import os

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from fastapi.responses import FileResponse
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


def _ensure_admin(db: Session, user_id: int) -> None:
    """관리자만 허용(roles.code == 'ADMIN')."""
    row = db.execute(
        text(
            """
            SELECT r.code::text AS role_code
            FROM public.users u
            JOIN public.roles r ON r.id = u.role_id
            WHERE u.id = :id
            """
        ),
        {"id": user_id},
    ).mappings().first()

    if not row or str(row.get("role_code") or "") != "ADMIN":
        raise HTTPException(status_code=403, detail="관리자만 사용할 수 있습니다.")


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


@router.get("/{ticket_id}/attachment/download")
def download_attachment(
    ticket_id: int,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """첨부파일 다운로드
    - 서버 파일 시스템 경로는 외부로 노출하지 않고, ticket_id 기반으로 조회 후 전송합니다.
    """
    row = db.execute(
        text(
            """
            SELECT attachment_name, attachment_path, attachment_mime
            FROM public.maintenance_tickets
            WHERE id = :id
            """
        ),
        {"id": ticket_id},
    ).mappings().first()

    if not row or not row.get("attachment_path"):
        raise HTTPException(status_code=404, detail="첨부파일이 없습니다.")

    raw_path = str(row["attachment_path"])
    # 보안: 업로드 루트 밖으로 나가면 차단
    uploads_root = (Path(__file__).resolve().parents[2] / "uploads").resolve()
    file_path = Path(raw_path).resolve()
    try:
        file_path.relative_to(uploads_root)
    except Exception:
        raise HTTPException(status_code=400, detail="잘못된 파일 경로입니다.")

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="첨부파일을 찾을 수 없습니다.")

    filename = row.get("attachment_name") or "attachment"
    media_type = row.get("attachment_mime") or "application/octet-stream"
    return FileResponse(path=str(file_path), media_type=media_type, filename=filename)


@router.delete("/{ticket_id}")
def delete_maintenance(
    ticket_id: int,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """유지보수 삭제 (관리자만)

    - maintenance_ticket_assignees 선삭제 후 maintenance_tickets 삭제
    - 첨부파일(uploads)은 이번 단계에서는 삭제하지 않음(요청 사항 아님)
    """
    _ensure_admin(db, user_id)

    try:
        db.execute(
            text("DELETE FROM public.maintenance_ticket_assignees WHERE ticket_id = :id"),
            {"id": ticket_id},
        )
        res = db.execute(
            text("DELETE FROM public.maintenance_tickets WHERE id = :id"),
            {"id": ticket_id},
        )
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="삭제에 실패했습니다.")

    # sqlalchemy Result.rowcount may be None depending on driver
    if getattr(res, "rowcount", 1) == 0:
        raise HTTPException(status_code=404, detail="유지보수 내역을 찾을 수 없습니다.")

    return {"ok": True}


@router.post("/{ticket_id}/complete", response_model=MaintenanceDetailOut)
def complete_maintenance(
    ticket_id: int,
    payload: MaintenanceCompleteIn,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    from .service import MaintenanceService
    return MaintenanceService(db).complete(ticket_id=ticket_id, actor_user_id=user_id, payload=payload)