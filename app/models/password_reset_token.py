# app/models/password_reset_token.py
from app import db
from app.utils.datetime_utils import now_local, to_local_timezone


class PasswordResetToken(db.Model):
    """
    Opaque token used to let a user set or reset their password without being
    logged in.  Two use-cases are tracked via `purpose`:

      'set_password'   — initial onboarding for students created through the
                         bulk-import flow; token is emailed at account creation.
      'reset_password' — standard "forgot password" flow if added in the future.

    A token is consumed (used_at is set) as soon as the password has been
    changed.  Expired or consumed tokens must never be accepted.
    """

    __tablename__ = 'password_reset_token'

    id = db.Column(db.Integer, primary_key=True)

    token = db.Column(
        db.String(128),
        unique=True,
        nullable=False,
        index=True,
    )  # opaque random token (e.g. secrets.token_urlsafe(64))

    user_id = db.Column(
        db.Integer,
        db.ForeignKey('user.id', ondelete='CASCADE'),
        nullable=False,
        index=True,
    )

    purpose = db.Column(
        db.String(40),
        nullable=False,
        default='set_password',
    )
    # Valid values: 'set_password', 'reset_password'

    expires_at = db.Column(db.DateTime, nullable=False)

    used_at = db.Column(db.DateTime, nullable=True)  # set when token is consumed

    created_at = db.Column(db.DateTime, default=now_local, nullable=False)

    created_by = db.Column(
        db.Integer,
        db.ForeignKey('user.id', ondelete='SET NULL'),
        nullable=True,
    )
    # The admin user who generated the token (e.g. the one who ran bulk import).

    # Relationships
    user = db.relationship(
        'User',
        foreign_keys=[user_id],
        backref='password_reset_tokens',
    )
    creator = db.relationship(
        'User',
        foreign_keys=[created_by],
    )

    # ------------------------------------------------------------------
    # Computed properties
    # ------------------------------------------------------------------

    @property
    def is_expired(self) -> bool:
        """
        True when the current time is past expires_at.

        `expires_at` is a naive `DATETIME` column, so a row read back from
        Postgres carries no tzinfo while `now_local()` is always aware —
        comparing them directly raises TypeError. It was written aware and in
        local time, so re-attaching LOCAL_TZ restores the original instant.
        Without this, every `get_token()` on a persisted row blew up with a 500
        and no account could ever set its password.
        """
        return now_local() > to_local_timezone(self.expires_at)

    @property
    def is_used(self) -> bool:
        """True when the token has already been consumed."""
        return self.used_at is not None

    @property
    def is_valid(self) -> bool:
        """True only if the token has not expired and has not been used."""
        return not self.is_expired and not self.is_used

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self):
        # Public payload: ids that name a User, a Submission or an Archive go
        # out as their UUID handle. Key names are unchanged on purpose.
        from app.models.user import User
        from app.services.public_id_service import uuid_for

        return {
            'id': self.id,
            'user_id': uuid_for(User, self.user_id),
            'purpose': self.purpose,
            'expires_at': self.expires_at.isoformat() if self.expires_at else None,
            'used_at': self.used_at.isoformat() if self.used_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'created_by': uuid_for(User, self.created_by),
            'is_valid': self.is_valid,
        }
