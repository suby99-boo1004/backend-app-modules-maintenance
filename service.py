from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session
from sqlalchemy import text, bindparam

from .schemas import (
    MaintenanceListItem,
    MaintenanceCreateIn,
    MaintenanceDetailOut,
    MaintenanceCompleteIn,
    AssigneeOut,
)

_KST = timezone(timedelta(hours=9))


def _year_range_kst_dt(year: int) -> Tuple[datetime, datetime]:
    start = datetime(year, 1, 1, 0, 0, 0, tzinfo=_KST)
    end = datetime(year + 1, 1, 1, 0, 0, 0, tzinfo=_KST)
    return start, end


def _normalize_q(q: Optional[str]) -> Optional[str]:
    if q is None:
        return None
    q = q.strip()
    return q if q else None


@dataclass
class MaintenanceService:
    db: Session

    def list(self, year: int, tab: str, q: Optional[str]) -> List[MaintenanceListItem]:
        qn = _normalize_q(q)
        statuses = ["CLOSED"] if tab == "completed" else ["OPEN", "IN_PROGRESS", "WAITING"]
        year_start, year_end = _year_range_kst_dt(year)

        sql_join = text(
            """
            SELECT
              t.id,
              t.requested_at,
              t.title,
              COALESCE(t.requester_name, '') AS requester_name,
              COALESCE(t.requester_org, '') AS requester_org,
              COALESCE(u.name, '') AS created_by_name
            FROM public.maintenance_tickets t
            LEFT JOIN public.users u ON u.id = t.created_by_user_id
            WHERE t.requested_at >= :year_start
              AND t.requested_at <  :year_end
              AND t.status IN :statuses
              AND (
                :q IS NULL OR
                t.title ILIKE :q_like OR
                COALESCE(t.requester_org,'') ILIKE :q_like OR
                COALESCE(u.name,'') ILIKE :q_like
              )
            ORDER BY t.requested_at DESC, t.id DESC
            """
        ).bindparams(bindparam("statuses", expanding=True))

        params = {
            "year_start": year_start,
            "year_end": year_end,
            "statuses": statuses,
            "q": qn,
            "q_like": f"%{qn}%" if qn else None,
        }

        try:
            rows = self.db.execute(sql_join, params).mappings().all()
        except Exception:
            sql_nojoin = text(
                """
                SELECT
                  t.id,
                  t.requested_at,
                  t.title,
                  COALESCE(t.requester_name, '') AS requester_name,
                  COALESCE(t.requester_org, '') AS requester_org,
                  '' AS created_by_name
                FROM public.maintenance_tickets t
                WHERE t.requested_at >= :year_start
                  AND t.requested_at <  :year_end
                  AND t.status IN :statuses
                  AND (
                    :q IS NULL OR
                    t.title ILIKE :q_like OR
                    COALESCE(t.requester_org,'') ILIKE :q_like
                  )
                ORDER BY t.requested_at DESC, t.id DESC
                """
            ).bindparams(bindparam("statuses", expanding=True))
            rows = self.db.execute(sql_nojoin, params).mappings().all()

        return [
            MaintenanceListItem(
                id=int(r["id"]),
                requested_at=str(r["requested_at"]),
                title=str(r["title"]),
                requester_name=str(r["requester_name"]),
                requester_org=str(r["requester_org"]),
                created_by_name=str(r.get("created_by_name") or ""),
            )
            for r in rows
        ]

    def create(self, created_by_user_id: Optional[int], payload: MaintenanceCreateIn) -> MaintenanceDetailOut:
        """
        신규 등록
        주의: 기존 DB 스키마에 ticket_no(text NOT NULL, UNIQUE), client_id(bigint NOT NULL)이 존재합니다.
        - ticket_no: id 시퀀스를 미리 확보(nextval)해서 'MT-<YYYY>-<ID>' 형식으로 생성
        - client_id: payload에 없으므로, 기본값으로 clients 테이블의 첫 번째 id를 사용(없으면 에러)
        """
        if not payload.title.strip():
            raise ValueError("유지보수명은 필수입니다.")
        if not payload.request_content.strip():
            raise ValueError("요청 내용은 필수입니다.")

        sql = text(
            """
            WITH new_id AS (
              SELECT nextval('public.maintenance_tickets_id_seq'::regclass) AS id
            ),
            base_client AS (
              SELECT id FROM public.clients ORDER BY id ASC LIMIT 1
            )
            INSERT INTO public.maintenance_tickets
              (id, ticket_no, client_id,
               title, status, priority,
               requested_at, created_at, updated_at,
               requester_name, requester_org, requester_phone,
               request_content, created_by_user_id)
            SELECT
              new_id.id,
              ('MT-' || to_char(now() AT TIME ZONE 'Asia/Seoul', 'YYYY') || '-' || lpad(new_id.id::text, 6, '0')) AS ticket_no,
              COALESCE((SELECT id FROM base_client), NULL) AS client_id,
              :title,
              'OPEN'::public.ticket_status,
              'MID'::public.ticket_priority,
              now(), now(), now(),
              :requester_name, :requester_org, :requester_phone,
              :request_content, :created_by_user_id
            FROM new_id
            RETURNING id
            """
        )

        try:
            new_id = self.db.execute(
                sql,
                {
                    "title": payload.title.strip(),
                    "requester_name": payload.requester_name.strip(),
                    "requester_org": payload.requester_org.strip(),
                    "requester_phone": payload.requester_phone.strip(),
                    "request_content": payload.request_content.strip(),
                    "created_by_user_id": created_by_user_id,
                },
            ).scalar_one()
            self.db.commit()
        except Exception as e:
            self.db.rollback()
            # clients가 비어있으면 client_id NOT NULL 위반이 날 수 있으니 메시지 가이드
            raise ValueError(
                "유지보수 신규등록 실패: clients 테이블에 최소 1개 행이 필요하거나, "
                "maintenance_tickets.client_id NOT NULL 제약이 활성 상태일 수 있습니다. "
                f"원인: {type(e).__name__}: {e}"
            )

        return self.get_detail(int(new_id))  # type: ignore

    def get_detail(self, ticket_id: int) -> Optional[MaintenanceDetailOut]:
        sql_ticket_join = text(
            """
            SELECT
              t.id,
              t.title,
              t.requested_at,
              t.closed_at,
              t.created_by_user_id,
              COALESCE(u.name, '') AS created_by_name,
              COALESCE(t.requester_name,'') AS requester_name,
              COALESCE(t.requester_org,'') AS requester_org,
              COALESCE(t.requester_phone,'') AS requester_phone,
              COALESCE(t.request_content,'') AS request_content,
              t.resolution_content,
              t.attachment_name,
              t.attachment_path,
              t.attachment_mime,
              t.attachment_size
            FROM public.maintenance_tickets t
            LEFT JOIN public.users u ON u.id = t.created_by_user_id
            WHERE t.id = :ticket_id
            """
        )
        try:
            t = self.db.execute(sql_ticket_join, {"ticket_id": ticket_id}).mappings().first()
        except Exception:
            sql_ticket = text(
                """
                SELECT
                  t.id,
                  t.title,
                  t.requested_at,
                  t.closed_at,
                  t.created_by_user_id,
                  '' AS created_by_name,
                  COALESCE(t.requester_name,'') AS requester_name,
                  COALESCE(t.requester_org,'') AS requester_org,
                  COALESCE(t.requester_phone,'') AS requester_phone,
                  COALESCE(t.request_content,'') AS request_content,
                  t.resolution_content,
                  t.attachment_name,
                  t.attachment_path,
                  t.attachment_mime,
                  t.attachment_size
                FROM public.maintenance_tickets t
                WHERE t.id = :ticket_id
                """
            )
            t = self.db.execute(sql_ticket, {"ticket_id": ticket_id}).mappings().first()

        if not t:
            return None

        assignees: List[AssigneeOut] = []
        sql_assignees_join = text(
            """
            SELECT a.user_id, COALESCE(u.name,'') AS name
            FROM public.maintenance_ticket_assignees a
            LEFT JOIN public.users u ON u.id = a.user_id
            WHERE a.ticket_id = :ticket_id
            ORDER BY a.id ASC
            """
        )
        try:
            rows = self.db.execute(sql_assignees_join, {"ticket_id": ticket_id}).mappings().all()
            assignees = [AssigneeOut(user_id=int(r["user_id"]), name=str(r.get("name") or "")) for r in rows]
        except Exception:
            sql_assignees = text(
                """
                SELECT a.user_id, '' AS name
                FROM public.maintenance_ticket_assignees a
                WHERE a.ticket_id = :ticket_id
                ORDER BY a.id ASC
                """
            )
            rows = self.db.execute(sql_assignees, {"ticket_id": ticket_id}).mappings().all()
            assignees = [AssigneeOut(user_id=int(r["user_id"]), name="") for r in rows]

        return MaintenanceDetailOut(
            id=int(t["id"]),
            title=str(t["title"]),
            requested_at=str(t["requested_at"]),
            closed_at=str(t["closed_at"]) if t.get("closed_at") is not None else None,
            created_by_user_id=int(t["created_by_user_id"]) if t.get("created_by_user_id") is not None else None,
            created_by_name=str(t.get("created_by_name") or ""),
            requester_name=str(t.get("requester_name") or ""),
            requester_org=str(t.get("requester_org") or ""),
            requester_phone=str(t.get("requester_phone") or ""),
            request_content=str(t.get("request_content") or ""),
            resolution_content=t.get("resolution_content"),
            assignees=assignees,

            # 첨부파일 메타 (경로는 응답에서 숨김)
            attachment_name=t.get("attachment_name"),
            attachment_path=None,
            attachment_mime=t.get("attachment_mime"),
            attachment_size=int(t["attachment_size"]) if t.get("attachment_size") is not None else None,
            attachment_download_url=(
                f"/api/maintenance/{int(t['id'])}/attachment/download"
                if t.get("attachment_name") and t.get("attachment_path") else None
            ),
        )

    def complete(self, ticket_id: int, actor_user_id: Optional[int], payload: MaintenanceCompleteIn) -> Optional[MaintenanceDetailOut]:
        res = payload.resolution_content.strip()
        if not res:
            raise ValueError("처리 내용은 필수입니다.")
        if not payload.assignee_user_ids:
            raise ValueError("처리 직원(참여자)을 1명 이상 선택해 주세요.")

        try:
            exists = self.db.execute(
                text("SELECT id FROM public.maintenance_tickets WHERE id=:id"),
                {"id": ticket_id},
            ).scalar()
            if not exists:
                return None

            self.db.execute(
                text(
                    """
                    UPDATE public.maintenance_tickets
                    SET status='CLOSED',
                        closed_at=now(),
                        updated_at=now(),
                        resolution_content=:resolution_content
                    WHERE id=:id
                    """
                ),
                {"id": ticket_id, "resolution_content": res},
            )

            ins = text(
                """
                INSERT INTO public.maintenance_ticket_assignees (ticket_id, user_id)
                VALUES (:ticket_id, :user_id)
                ON CONFLICT (ticket_id, user_id) DO NOTHING
                """
            )
            for uid in payload.assignee_user_ids:
                self.db.execute(ins, {"ticket_id": ticket_id, "user_id": int(uid)})

            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

        return self.get_detail(ticket_id)
