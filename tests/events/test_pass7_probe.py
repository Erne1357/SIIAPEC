"""
PASS 7 PROBE — is `GET /api/v1/events/<id>/{slots,hosts,images}` and
`/events/public/<id>` still an existence oracle for an event the caller could
NEVER have seen in any listing?

Pass 6 closed the management family (404 for both absence and denial) and kept
403 on the participation family, justified by "the object came to the caller in
a listing they legitimately received". That justification holds for a PUBLIC,
PUBLISHED event — which is exactly the only case its test covers
(`TestParticipationReadsStay403` uses `ev_b`, public + published).

It does not hold for a DRAFT or PRIVATE event of another programme. That row
never appears in `/api/v1/events/public`, never in `/api/v1/events` (the caller
here has no management permission at all), and never in the dashboard widget.
This probe asks whether a plain applicant can still tell "this id exists" from
"this id does not exist" by the status code alone.
"""

import unittest
from datetime import datetime, timedelta

from app import create_app, db
from app.models.event import Event
from tests.events.conftest import (
    make_test_config, make_role, make_user, make_program, enroll_in_program,
    login,
)

ABSENT_ID = 987654


class Pass7ExistenceProbe(unittest.TestCase):
    def setUp(self):
        self.app = create_app(make_test_config())
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        self.role_admin = make_role('program_admin')
        self.role_applicant = make_role('applicant')

        self.coord_b = make_user(self.role_admin, suffix='_b')
        db.session.flush()
        self.prog_b = make_program(self.coord_b, slug='prog-b')

        self.prog_a_coord = make_user(self.role_admin, suffix='_a')
        db.session.flush()
        self.prog_a = make_program(self.prog_a_coord, slug='prog-a')

        # A plain applicant of programme A. No permission whatsoever.
        self.applicant = make_user(self.role_applicant, suffix='_x')
        db.session.flush()
        enroll_in_program(self.applicant, self.prog_a, admission_status='in_progress')

        soon = datetime.now() + timedelta(days=5)

        # Draft event of programme B — invisible to everybody but B's staff.
        self.ev_draft = Event(
            program_id=self.prog_b.id, type='workshop', title='SECRETO BORRADOR',
            created_by=self.coord_b.id, status='draft', visibility='private',
            visible_to_students=False, capacity_type='multiple', max_capacity=10,
            event_date=soon,
        )
        # Published-but-private event of programme B.
        self.ev_private = Event(
            program_id=self.prog_b.id, type='workshop', title='SECRETO PRIVADO',
            created_by=self.coord_b.id, status='published', visibility='private',
            visible_to_students=True, capacity_type='multiple', max_capacity=10,
            event_date=soon,
        )
        db.session.add_all([self.ev_draft, self.ev_private])
        db.session.commit()

        self.client = self.app.test_client()
        login(self.client, self.applicant)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _codes(self, event_id):
        out = {}
        for path in ('slots', 'hosts', 'images'):
            out[path] = self.client.get(
                f'/api/v1/events/{event_id}/{path}').status_code
        out['public'] = self.client.get(
            f'/api/v1/events/public/{event_id}').status_code
        return out

    def test_absent_vs_draft_of_other_program(self):
        absent = self._codes(ABSENT_ID)
        draft = self._codes(self.ev_draft.id)
        private = self._codes(self.ev_private.id)
        print('\nABSENT :', absent)
        print('DRAFT/B:', draft)
        print('PRIV/B :', private)
        self.assertEqual(
            absent, draft,
            'ORACLE: an applicant distinguishes an absent id from a DRAFT '
            'event of another programme by status code alone.',
        )
        self.assertEqual(
            absent, private,
            'ORACLE: an applicant distinguishes an absent id from a PRIVATE '
            'event of another programme by status code alone.',
        )

    def test_public_detail_error_messages_differ(self):
        """Even inside 403, does the message separate 'not published' from 'not yours'?"""
        r_draft = self.client.get(f'/api/v1/events/public/{self.ev_draft.id}')
        r_priv = self.client.get(f'/api/v1/events/public/{self.ev_private.id}')
        print('\nDRAFT body  :', r_draft.status_code, r_draft.get_json())
        print('PRIVATE body:', r_priv.status_code, r_priv.get_json())
        self.assertEqual(
            (r_draft.get_json() or {}).get('error'),
            (r_priv.get_json() or {}).get('error'),
            'ORACLE: the 403 message distinguishes a draft from a private event.',
        )


if __name__ == '__main__':
    unittest.main()
