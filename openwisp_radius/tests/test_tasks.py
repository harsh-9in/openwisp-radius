from datetime import timedelta

from celery import Celery
from celery.contrib.testing.worker import start_worker
from django.contrib.auth import get_user_model
from django.core import management
from django.utils.timezone import now

from openwisp_radius import tasks
from openwisp_utils.tests import capture_any_output, capture_stdout

from ..utils import load_model
from . import _RADACCT, FileMixin
from .mixins import BaseTestCase

User = get_user_model()
RadiusAccounting = load_model('RadiusAccounting')
RadiusBatch = load_model('RadiusBatch')
RadiusPostAuth = load_model('RadiusPostAuth')
RegisteredUser = load_model('RegisteredUser')


class TestCelery(FileMixin, BaseTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        app = Celery('openwisp2')
        app.config_from_object('django.conf:settings', namespace='CELERY')
        app.autodiscover_tasks()
        cls.celery_worker = start_worker(app)

    def _get_expired_user_from_radius_batch(self):
        reader = [['sample', 'admin', 'user@openwisp.com', 'SampleName', 'Lastname']]
        batch = self._create_radius_batch(
            name='test',
            strategy='csv',
            csvfile=self._get_csvfile(reader),
            expiration_date='1998-01-28',
        )
        batch.add(reader)
        return batch.users.first()

    @capture_stdout()
    def test_cleanup_stale_radacct(self):
        options = _RADACCT.copy()
        options['unique_id'] = '118'
        self._create_radius_accounting(**options)
        result = tasks.cleanup_stale_radacct.delay(30)
        self.assertTrue(result.successful())
        session = RadiusAccounting.objects.get(unique_id='118')
        self.assertNotEqual(session.stop_time, None)
        self.assertNotEqual(session.session_time, None)
        self.assertEqual(session.update_time, session.stop_time)

    @capture_stdout()
    def test_deactivate_expired_users(self):
        user = self._get_expired_user_from_radius_batch()
        self.assertTrue(user.is_active)
        result = tasks.deactivate_expired_users.delay()
        self.assertTrue(result.successful())
        user.refresh_from_db()
        self.assertFalse(user.is_active)

    @capture_stdout()
    def test_delete_old_users(self):
        self._get_expired_user_from_radius_batch()
        self.assertEqual(User.objects.all().count(), 1)
        result = tasks.delete_old_users.delay(1)
        self.assertTrue(result.successful())
        self.assertEqual(User.objects.all().count(), 0)

    @capture_any_output()
    def test_delete_old_postauth(self):
        options = dict(username='steve', password='jones', reply='value2')
        self._create_radius_postauth(**options)
        RadiusPostAuth.objects.filter(username='steve').update(date='2018-01-31')
        result = tasks.delete_old_postauth.delay(3)
        self.assertTrue(result.successful())
        self.assertEqual(RadiusPostAuth.objects.filter(username='steve').count(), 0)

    @capture_stdout()
    def test_delete_old_radacct(self):
        options = _RADACCT.copy()
        options['stop_time'] = '2017-06-10 11:50:00'
        options['update_time'] = '2017-03-10 11:50:00'
        options['unique_id'] = '666'
        self._create_radius_accounting(**options)
        result = tasks.delete_old_radacct.delay(3)
        self.assertTrue(result.successful())
        self.assertEqual(RadiusAccounting.objects.filter(unique_id='666').count(), 0)

    @capture_stdout()
    def test_delete_unverified_users(self):
        path = self._get_path('static/test_batch.csv')
        options = dict(organization=self.default_org.slug, file=path, name='test',)
        management.call_command('batch_add_users', **options)
        User.objects.update(date_joined=now() - timedelta(days=3))
        for user in User.objects.all():
            RegisteredUser.objects.create(user=user, method='email', is_verified=False)
        self.assertEqual(User.objects.count(), 3)
        tasks.delete_unverified_users.delay(older_than_days=2)
        self.assertEqual(User.objects.count(), 0)
