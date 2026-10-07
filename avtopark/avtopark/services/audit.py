"""Audit log: who created, changed or deleted what."""
from flask import has_request_context
from flask_login import current_user

from ..extensions import db
from ..models import AuditLog

ACTION_LABELS = {
    "create": "Създаване",
    "update": "Промяна",
    "delete": "Изтриване",
    "status": "Смяна на статус",
    "login": "Вход",
    "login_failed": "Неуспешен вход",
    "logout": "Изход",
    "check": "Проверка на обслужване",
}


def log_action(action: str, entity: str, entity_id: int | None = None, details: str = "") -> None:
    """Add an audit entry to the current transaction (committed together with the change)."""
    user = current_user._get_current_object() if has_request_context() else None
    authenticated = user is not None and user.is_authenticated
    db.session.add(
        AuditLog(
            user_id=user.id if authenticated else None,
            username=user.username if authenticated else None,
            action=action,
            entity=entity,
            entity_id=entity_id,
            details=(details or "")[:500],
        )
    )
