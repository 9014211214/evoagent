from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from evoagent.campaigns.models import CampaignRecord, CampaignState, CampaignType
from evoagent.campaigns.repository import SQLiteCampaignRepository


class CampaignOperatorView:
    """Read-only query surface for operator tooling."""

    def __init__(self, repository: SQLiteCampaignRepository):
        self.repository = repository

    def list_campaigns(
        self,
        *,
        state: CampaignState | None = None,
        campaign_type: CampaignType | None = None,
    ) -> list[CampaignRecord]:
        clauses: list[str] = []
        values: list[str] = []
        if state is not None:
            clauses.append("state = ?")
            values.append(state.value)
        if campaign_type is not None:
            clauses.append("campaign_type = ?")
            values.append(campaign_type.value)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with closing(sqlite3.connect(Path(self.repository.path))) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT campaign_id FROM campaigns"
                + where
                + " ORDER BY updated_at DESC, campaign_id",
                values,
            ).fetchall()
        return [self.repository.get(row["campaign_id"]) for row in rows]
