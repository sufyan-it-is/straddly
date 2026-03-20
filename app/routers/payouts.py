"""app/routers/payouts.py

Payout requests (wallet withdrawal).

Frontend currently uses:
  GET /payouts

We also expose:
  POST /payouts           (create request)
  PATCH /payouts/{id}     (admin status update)
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field

from app.database import get_pool
from app.dependencies import CurrentUser, get_current_user, get_admin_user, get_super_admin_user

log = logging.getLogger(__name__)

router = APIRouter(prefix="/payouts", tags=["Payouts"])


class CreatePayoutRequest(BaseModel):
    amount: float = Field(..., gt=0)
    mode: Optional[str] = None
    note: Optional[str] = None


class UpdatePayoutRequest(BaseModel):
    status: str
    note: Optional[str] = None


ALLOWED_PAYOUT_STATUSES = {
    "APPROVED",
    "REJECTED",
    "HOLD",
    "PENDING",
    "PAID",
    # legacy
    "COMPLETED",
}


@router.get("")
@router.get("/")
async def list_payouts(user: CurrentUser = Depends(get_admin_user)):
    """
    Returns payout requests.
    - Admin/Super-admin only: returns all users' payouts.

    Output shape matches the current UI:
      { data: [ { date, user_id, user_name, amount, mode, status } ] }
    """
    pool = get_pool()

    if user.role in ("ADMIN", "SUPER_ADMIN"):
        rows = await pool.fetch(
            """
            SELECT pr.payout_id,
                   pr.requested_at,
                   pr.user_id,
                   u.name AS user_name,
                   pr.amount,
                   pr.mode,
                   pr.status
            FROM payout_requests pr
            JOIN users u ON u.id = pr.user_id
            ORDER BY pr.requested_at DESC
            LIMIT 500
            """
        )
    else:
        rows = await pool.fetch(
            """
            SELECT pr.payout_id,
                   pr.requested_at,
                   pr.user_id,
                   u.name AS user_name,
                   pr.amount,
                   pr.mode,
                   pr.status
            FROM payout_requests pr
            JOIN users u ON u.id = pr.user_id
            WHERE pr.user_id = $1::uuid
            ORDER BY pr.requested_at DESC
            LIMIT 500
            """,
            user.id,
        )

    data = []
    for r in rows:
        dt = r["requested_at"]
        status = (r["status"] or "").upper().strip()
        if status == "COMPLETED":
            status = "PAID"
        data.append(
            {
                "id": str(r["payout_id"]),
                "date": dt.isoformat() if hasattr(dt, "isoformat") else str(dt),
                "user_id": str(r["user_id"]),
                "user_name": r["user_name"],
                "amount": float(r["amount"] or 0),
                "mode": r["mode"],
                "status": status,
            }
        )
    return {"data": data}


@router.post("", status_code=201)
@router.post("/", status_code=201)
async def create_payout(req: CreatePayoutRequest, user: CurrentUser = Depends(get_current_user)):
    """Create a payout request for the logged-in user."""
    pool = get_pool()

    # BLOCKED users are explicitly allowed to request payout; others also allowed.
    bal = await pool.fetchval(
        "SELECT balance FROM paper_accounts WHERE user_id=$1::uuid",
        user.id,
    )
    bal = float(bal or 0)
    if req.amount > bal:
        raise HTTPException(status_code=400, detail="Insufficient wallet balance for payout request.")

    row = await pool.fetchrow(
        """
        INSERT INTO payout_requests (user_id, amount, mode, status, note, created_by)
        VALUES ($1::uuid, $2, $3, 'PENDING', $4, $5::uuid)
        RETURNING payout_id, requested_at
        """,
        user.id,
        float(req.amount),
        req.mode,
        req.note,
        user.id,
    )

    return {
        "success": True,
        "id": str(row["payout_id"]),
        "requested_at": row["requested_at"].isoformat(),
    }


@router.patch("/{payout_id}")
@router.patch("/{payout_id}/")
async def update_payout(
    req: UpdatePayoutRequest,
    payout_id: str = Path(...),
    admin: CurrentUser = Depends(get_admin_user),
):
    """Admin updates payout status."""
    pool = get_pool()

    status = (req.status or "").upper().strip()
    if status not in ALLOWED_PAYOUT_STATUSES:
        raise HTTPException(
            status_code=400,
            detail="Invalid status. Use APPROVED/REJECTED/HOLD/PENDING/PAID.",
        )
    if status == "COMPLETED":
        status = "PAID"

    row = await pool.fetchrow(
        "SELECT user_id, amount, status FROM payout_requests WHERE payout_id=$1::uuid",
        payout_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Payout request not found.")

    await pool.execute(
        """
        UPDATE payout_requests
        SET status=$2::text,
            note=COALESCE($3::text, note),
            processed_at=CASE WHEN $2::text IN ('PAID','REJECTED') THEN NOW() ELSE NULL END,
            updated_by=$4::uuid
        WHERE payout_id=$1::uuid
        """,
        payout_id,
        status,
        req.note,
        admin.id,
    )

    return {"success": True}
