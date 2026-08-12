# app/models/log.py
"""
System / security audit log.

This is the destination for **authentication** events (login success, login
failure, account lockout). It is deliberately *not* ``user_history``:

* ``user_history`` is the **staff-facing** record. It is rendered in the
  coordinator and admin consoles, returned verbatim by
  ``GET /api/v1/coordinator/students/<id>/history`` (gated only by
  ``coordinator.api.list_students``) and by ``/api/v1/admin/users/<id>/history``,
  and it is eagerly serialized by ``User.to_dict(include_sensitive=True)``
  through the ``User.histories`` relationship. Anything written there is
  readable by every ``program_admin``.
* ``user_history.admin_id`` carries a meaning — "an administrator did this to
  you" — and ``UserHistoryService.log_action`` fills it from ``current_user``
  when it is left as ``None``. A login event is recorded *after*
  ``login_user()``, so it would stamp every user as their own administrator.
* ``user_history`` rows come from authenticated flows. An authentication event
  comes from an **unauthenticated** one, i.e. its volume is attacker-controlled.

``log`` has none of those properties: no ``admin_id`` column, no API that reads
it, and — on purpose — **no relationship back from ``User``**. Do not add one:
a ``User.logs`` relationship would put these rows straight back into
``User.to_dict(include_sensitive=True)``, which is the exact disclosure this
table exists to avoid. Query it with ``Log.query.filter_by(user_id=...)``.

If this table is ever exposed through an API, gate it behind its own permission
(``audit.api.*``) and re-review what ``description`` is allowed to contain
first — see ``app/services/auth_audit_service.py`` for the current rules on
client addresses and User-Agent strings.
"""

from app import db
from app.utils.datetime_utils import now_local


class Log(db.Model):
    __tablename__ = 'log'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    action = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)
    created_at = db.Column(db.DateTime, nullable=False, default=now_local)

    # The two access paths an audit actually uses: "what happened to this
    # account" and "show me every failed login in this period". Without them a
    # table that now receives one row per successful login is a sequential scan.
    # Created by migration j5e6f7g8h9i0.
    __table_args__ = (
        db.Index('ix_log_user_id_created_at', 'user_id', 'created_at'),
        db.Index('ix_log_action_created_at', 'action', 'created_at'),
    )

    def to_dict(self):
        # Public payload: ids that name a User, a Submission or an Archive go
        # out as their UUID handle. Key names are unchanged on purpose.
        from app.models.user import User
        from app.services.public_id_service import uuid_for

        return {
            'id': self.id,
            'user_id': uuid_for(User, self.user_id),
            'action': self.action,
            'description': self.description,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }

    def __repr__(self):
        return f'<Log {self.action} user_id={self.user_id} at {self.created_at}>'
