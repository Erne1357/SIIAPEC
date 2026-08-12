from app import db

class RetentionPolicy(db.Model):
    __tablename__ = 'retention_policy'

    id = db.Column(db.Integer, primary_key=True)
    archive_id = db.Column(db.Integer, db.ForeignKey('archive.id', ondelete='CASCADE', onupdate='CASCADE'), nullable=False)

    keep_years = db.Column(db.Integer)                 # ej. 4
    keep_forever = db.Column(db.Boolean, default=False, nullable=False)
    apply_after = db.Column(db.String(20), default='graduated')  # graduated|dropped|enrollment
    
    def to_dict(self):
        # Public payload: ids that name a User, a Submission or an Archive go
        # out as their UUID handle. Key names are unchanged on purpose.
        from app.models.archive import Archive
        from app.services.public_id_service import uuid_for

        return {
            'id': self.id,
            'archive_id': uuid_for(Archive, self.archive_id),
            'keep_years': self.keep_years,
            'keep_forever': self.keep_forever,
            'apply_after': self.apply_after
        }
