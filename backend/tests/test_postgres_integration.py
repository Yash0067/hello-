import os
import sys
import unittest
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@unittest.skipUnless(os.environ.get('TEST_DATABASE_URL'),
                     'Set TEST_DATABASE_URL to run PostgreSQL integration tests')
class PostgresIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ['DATABASE_URL'] = os.environ['TEST_DATABASE_URL']
        os.environ['APP_ENV'] = 'test'
        from postgres_db import connection, init_schema
        from postgres_store import PostgresStore
        import auth
        init_schema()
        cls.connection = connection
        cls.store = PostgresStore()
        cls.auth = auth

    def test_customer_measurement_order_appointment_and_idempotency(self):
        phone = '98' + uuid.uuid4().hex[:8]
        client = self.store.create_client({'name': 'Integration Test', 'phone': phone})
        try:
            self.assertEqual(self.store.get_client(client['client_id'])['phone'], phone)
            self.store.save_measurement(client['client_id'], {
                'garment_type': 'Shirt', 'values': {'Chest': 36},
                'notes': '', 'unit_used': 'in'})
            self.assertEqual(len(self.store.list_history(client['client_id'])), 1)
            order = self.store.create_order({
                'client_id': client['client_id'], 'garment': 'Shirt',
                'notes': '', 'due_date': '', 'status': 'Pending'})
            self.assertEqual(self.store.update_order(
                order['order_id'], {'status': 'Ready'})['status'], 'Ready')
            row = ('integration-' + uuid.uuid4().hex, '2026-11-03 00:00:00',
                   'Integration Test', phone, 'Blouse stitching', '2099-01-01',
                   '10:00', '', 'Pending')
            self.assertFalse(self.store.create_appointment(row))
            self.assertTrue(self.store.create_appointment(row))
        finally:
            self.store.delete_client(client['client_id'])

    def test_session_revocation_persists(self):
        import tempfile
        from pathlib import Path
        self.auth.init_auth_db(Path(tempfile.gettempdir()) / 'unused-auth.sqlite3')
        issued = self.auth.issue_token('integration', ttl_hours=1)
        payload = self.auth.verify_token(issued['token'])
        self.assertIsNotNone(payload)
        self.auth.revoke_token(payload)
        self.assertIsNone(self.auth.verify_token(issued['token']))
