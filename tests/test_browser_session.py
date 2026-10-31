import unittest
from unittest.mock import patch
from video_publisher.browser_session import reuse,current,Session
from video_publisher.core import VpubError


class SessionTests(unittest.TestCase):
    @patch.object(Session,'close')
    def test_scoped_reuse_does_not_spawn_until_a_command_and_closes(self,close):
        with reuse('instagram') as session:
            self.assertIsNone(session.process)
            with reuse('instagram') as nested:self.assertIs(session,nested)
            self.assertIs(current(),session)
            with self.assertRaises(VpubError):
                with reuse('x'):pass
        self.assertIsNone(current());close.assert_called_once()

    def test_scope_cannot_change_platform_account_or_action(self):
        session=Session('instagram','default')
        for p,a,action in [('x','default','collections'),('instagram','other','collections'),('instagram','default','publish')]:
            with self.assertRaises(VpubError):session.call({'platform':p,'account':a,'action':action})
        self.assertIsNone(session.process)

    @patch.object(Session,'close')
    def test_exception_closes_session_without_retry(self,close):
        with self.assertRaises(RuntimeError):
            with reuse('instagram'):raise RuntimeError('interrupt')
        close.assert_called_once();self.assertIsNone(current())
