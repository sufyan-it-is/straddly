"""app/routers/ledger.py

Ledger endpoints for wallet statement.

- GET /ledger
  - normal user: sees own ledger
  - admin: may pass ?user_id=<uuid> to see any user's ledger

Returns a unified view of:
  1. Wallet movements (deposits, withdrawals, fees) from ledger_entries
  2. Realized P&L directly from paper_positions (closed positions)
     — so historical P&L always shows regardless of whether the charge
       scheduler has processed those positions.
"""

import logging
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.database import get_pool
from app.dependencies import CurrentUser, get_current_user

log = logging.getLogger(__name__)

router = APIRouter(prefix="/ledger", tags=["Ledger"])


@router.get("")
async def get_ledger(
    current_user: CurrentUser = Depends(get_current_user),
    user_id:   Optional[str] = Query(None, description="Admin override: target user UUID"),
    from_date: Optional[str] = Query(None, description="Start date YYYY-MM-DD (inclusive)"),
    to_date:   Optional[str] = Query(None, description="End date   YYYY-MM-DD (inclusive)"),
    limit: int = Query(1000, ge=1, le=5000),
    offset: int = Query(0, ge=0),
):
    """
    Returns unified wallet and P&L statement filtered by date range.

    - Wallet entries : deposits, withdrawals, fees  (from ledger_entries)
    - P&L entries    : realized gains/losses from every closed position in
                       paper_positions — always present, no scheduler dependency.
    """
    pool = get_pool()

    target_user_id = user_id or current_user.id
    if user_id and user_id != current_user.id and current_user.role not in ("ADMIN", "SUPER_ADMIN"):
        raise HTTPException(status_code=403, detail="Admin access required for other users' ledgers.")

    # ── Date range ────────────────────────────────────────────────────────────
    today = date.today()
    try:
        fd = date.fromisoformat(from_date) if from_date else today - timedelta(days=30)
        td = date.fromisoformat(to_date)   if to_date   else today
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

    # ── 1. Get opening balance ────────────────────────────────────────────────
    # Strategy:
    # a) If there are ledger entries before the date range → use the last one's balance_after
    # b) If no entries before date range but user has "Opening Balance" entry → use that
    # c) Otherwise → 0 (user has no balance set)
    
    # Check for any ledger entry before the date range
    opening_balance_row = await pool.fetchrow(
        """
        SELECT balance_after
        FROM   ledger_entries
        WHERE  user_id     = $1::uuid
          AND  created_at <  $2::date
          AND  description NOT LIKE '%Realized P&L%'
        ORDER BY created_at DESC
        LIMIT 1
        """,
        target_user_id, fd,
    )

    has_prior_transactions = opening_balance_row is not None
    
    if opening_balance_row and opening_balance_row["balance_after"] is not None:
        opening_balance_value = float(opening_balance_row["balance_after"])
    else:
        # No ledger entries before date range - check if user has "Opening Balance" entry
        opening_entry = await pool.fetchrow(
            """
            SELECT balance_after, created_at
            FROM   ledger_entries
            WHERE  user_id      = $1::uuid
              AND  ref_type     = 'OPENING_BALANCE'
            LIMIT 1
            """,
            target_user_id,
        )
        if opening_entry:
            has_prior_transactions = True
            ob_date = opening_entry["created_at"].date()
            if ob_date >= fd:
                # The Opening Balance entry falls within (or after) the date range.
                # It will be included in wallet_rows and processed as a credit there.
                # Setting opening_balance_value = 0 prevents double-counting.
                opening_balance_value = 0.0
            else:
                # Entry is before the from-date (shouldn't normally reach here since the first
                # query would have caught it, but handle gracefully just in case).
                opening_balance_value = float(opening_entry["balance_after"] or 0)
        else:
            opening_balance_value = 0.0

    # ── 2. Wallet entries (deposits, withdrawals, fees — NOT P&L rows) ────────
    wallet_rows = await pool.fetch(
        """
        SELECT created_at, description, debit, credit, balance_after
        FROM   ledger_entries
        WHERE  user_id     = $1::uuid
          AND  created_at >= $2::date
          AND  created_at <  $3::date + INTERVAL '1 day'
          AND  description NOT LIKE '%Realized P&L%'
        ORDER BY created_at ASC
        LIMIT $4 OFFSET $5
        """,
        target_user_id, fd, td, limit, offset,
    )

    # ── 3. Realized P&L — pulled directly from closed positions ──────────────
    #   This ensures coverage for all historical positions regardless of
    #   whether the charge scheduler has processed them.
    pnl_rows = await pool.fetch(
        """
        SELECT
            pp.position_id,
            pp.closed_at,
            pp.symbol,
            pp.exchange_segment,
            pp.product_type,
            COALESCE(pp.realized_pnl,    0) AS realized_pnl,
            COALESCE(pp.total_charges,   0) AS total_charges,
            COALESCE(pp.platform_cost,   0) AS platform_cost,
            COALESCE(pp.trade_expense,   0) AS trade_expense,
            pp.avg_price,
            pp.quantity
        FROM paper_positions pp
        WHERE pp.user_id  = $1::uuid
          AND pp.status   = 'CLOSED'
          AND pp.closed_at IS NOT NULL
          AND pp.closed_at >= $2::date
          AND pp.closed_at <  $3::date + INTERVAL '1 day'
        ORDER BY pp.closed_at ASC
        LIMIT $4 OFFSET $5
        """,
        target_user_id, fd, td, limit, offset,
    )

    # ── 4. Build unified response ─────────────────────────────────────────────
    data = []
    seq = 0  # Sequence number to preserve insertion order for same-timestamp entries

    # Add opening balance entry ONLY if:
    # 1. The actual "Opening Balance" entry is not already in the fetched rows, AND
    # 2. There are transactions to display (show starting point)
    showing_opening_entry = any(
        r["description"] == "Opening Balance" for r in wallet_rows
    )
    
    # Show opening balance if there are transactions OR user had explicit opening balance set
    should_show_opening = (len(wallet_rows) > 0 or len(pnl_rows) > 0 or has_prior_transactions)
    
    if should_show_opening and not showing_opening_entry:
        data.append({
            "_seq":        seq,
            "date":        fd.isoformat(),
            "type":        "wallet",
            "description": "Opening balance",
            "debit":       None,
            "credit":      None,  # Don't add as credit, just display the balance
            "balance":     round(opening_balance_value, 2),
        })
        seq += 1

    for r in wallet_rows:
        created_at = r["created_at"]
        debit  = r["debit"]
        credit = r["credit"]
        bal    = r["balance_after"]
        data.append({
            "_seq":        seq,
            "date":        created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at),
            "type":        "wallet",
            "description": r["description"],
            "debit":       float(debit)  if debit  is not None else None,
            "credit":      float(credit) if credit is not None else None,
            "balance":     float(bal)    if bal    is not None else None,
        })
        seq += 1

    for r in pnl_rows:
        created_at   = r["closed_at"]
        realized_pnl = float(r["realized_pnl"]  or 0)
        total_charges= float(r["total_charges"]  or 0)
        net_pnl      = realized_pnl - total_charges

        desc = "Realized profit/loss"

        data.append({
            "_seq":        seq,
            "date":        created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at),
            "type":        "trade_pnl",
            "description": desc,
            "debit":       round(abs(net_pnl), 2) if net_pnl < 0  else None,
            "credit":      round(net_pnl,      2) if net_pnl >= 0 else None,
            "balance":     None,   # position P&L rows don't carry a running wallet balance
            # Extra fields for richer UI display
            "realized_pnl":   round(realized_pnl,   2),
            "total_charges":  round(total_charges,   2),
            "net_pnl":        round(net_pnl,         2),
        })
        seq += 1

    # Sort merged list newest-first, using _seq as tiebreaker for same timestamps
    # (entries are now in chronological order, so higher _seq = newer)
    data.sort(key=lambda x: (x["date"], x["_seq"]), reverse=True)

    # ── 5. Calculate running wallet balance including P&L entries ──────────────
    # Single forward pass (oldest → newest).
    # Calculate running balance for ALL entries (wallet + P&L) by processing
    # credits/debits sequentially. Use _seq as tiebreaker for same timestamps.
    data_sorted_asc = sorted(data, key=lambda x: (x["date"], x["_seq"]))
    running_balance = opening_balance_value

    for entry in data_sorted_asc:
        # Apply the transaction to running balance
        if entry["credit"] is not None:
            running_balance += float(entry["credit"])
        elif entry["debit"] is not None:
            running_balance -= float(entry["debit"])
        
        # Update balance for this entry
        entry["balance"] = round(running_balance, 2)

    # Return newest-first, using _seq as tiebreaker for same timestamps
    # Remove _seq before sending to frontend
    data.sort(key=lambda x: (x["date"], x["_seq"]), reverse=True)
    for entry in data:
        del entry["_seq"]
    
    return {"data": data}
