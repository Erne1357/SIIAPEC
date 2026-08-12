from app import db
from app.utils.datetime_utils import now_local
from datetime import datetime


class EmailQueue(db.Model):
    """
    Cola de correos pendientes de enviar.

    `html_content` is the rendered body. For password-reset and staff-activation
    mail that body contains the one-time token link, which is a live credential
    for the destination account. It must never be serialized to a client: the
    queue console is reachable by every holder of `admin_emails.api.manage`
    (program_admin included), so a readable body means any coordinator can take
    over any account whose mail is still in the table — the postgraduate_admin
    included. `to_dict()` is the delivery-metadata projection and deliberately
    omits the body; the only consumer of `html_content` is the sender
    (`EmailService._try_send_email`), which reads the attribute directly.
    """
    __tablename__ = 'email_queue'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False)
    notification_id = db.Column(db.Integer, db.ForeignKey('notification.id', ondelete='SET NULL'), nullable=True)
    
    recipient_email = db.Column(db.String(255), nullable=False)
    subject = db.Column(db.String(500), nullable=False)
    html_content = db.Column(db.Text, nullable=False)
    
    status = db.Column(db.String(20), nullable=False, default='pending')  # pending|sent|failed
    attempts = db.Column(db.Integer, nullable=False, default=0)
    max_attempts = db.Column(db.Integer, nullable=False, default=3)
    
    error_message = db.Column(db.Text, nullable=True)
    
    created_at = db.Column(db.DateTime, nullable=False, default=now_local)
    sent_at = db.Column(db.DateTime, nullable=True)
    next_retry_at = db.Column(db.DateTime, nullable=True)
    
    user = db.relationship('User', backref='email_queue')
    notification = db.relationship('Notification', backref='email_queue_item')
    
    def to_dict(self):
        """
        Delivery metadata only — never the body.

        Everything the queue console needs to do its job (what was sent, to
        whom, in what state, how many attempts, when, and why it failed) is
        here. `html_content` is intentionally absent and this method must not
        read the attribute either: callers defer that column at query time so
        the body does not leave PostgreSQL for a listing.
        """
        # Public payload: ids that name a User, a Submission or an Archive go
        # out as their UUID handle. Key names are unchanged on purpose.
        from app.models.user import User
        from app.services.public_id_service import uuid_for

        return {
            'id': self.id,
            'user_id': uuid_for(User, self.user_id),
            'notification_id': self.notification_id,
            'recipient_email': self.recipient_email,
            'subject': self.subject,
            'status': self.status,
            'attempts': self.attempts,
            'max_attempts': self.max_attempts,
            'error_message': self.error_message,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'sent_at': self.sent_at.isoformat() if self.sent_at else None,
            'next_retry_at': self.next_retry_at.isoformat() if self.next_retry_at else None
        }