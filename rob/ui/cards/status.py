from __future__ import annotations

from rob.models import MaintenanceState, QueueStatus
from rob.ui.components import make_card, render
from rob.ui.copy import STATUS_FOOTER
from rob.ui.render import CardSection
from rob.ui.theme import COLOR_DANGER, COLOR_SUCCESS, COLOR_WARNING


def status_card(*, bot_name: str, database_ok: bool, maintenance: MaintenanceState, queue: QueueStatus):
    color = COLOR_WARNING if maintenance.enabled else COLOR_SUCCESS
    sections = [CardSection(title="Database", text="Healthy" if database_ok else "Unavailable", inline=True), CardSection(title="Maintenance", text="On" if maintenance.enabled else "Off", inline=True), CardSection(title="Queue", text=f"Pending: {queue.pending}\nQueued: {queue.queued_maintenance}\nPosted: {queue.posted}\nFailed: {queue.failed}")]
    if maintenance.reason:
        sections.append(CardSection(title="Reason", text=maintenance.reason))
    return render(make_card(title=f"{bot_name} | Status", body="Shared PostgreSQL health and queue state.", color=color if database_ok else COLOR_DANGER, footer=STATUS_FOOTER, sections=sections, variant="status", eyebrow="Status"))
