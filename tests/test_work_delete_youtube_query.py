"""Default Studio URL parameters must not conceal a filtered list or account drift."""
import unittest
from urllib.parse import urlencode
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace
from video_publisher import work_delete_youtube as route
from video_publisher.core import VpubError


class StudioQueryTests(unittest.IsolatedAsyncioTestCase):
    def query(self, **changes):
        return urlencode({'filter': '[]', 'sort': '{"columnType":"date","sortOrder":"DESCENDING"}', **changes})

    def test_default_only(self):
        self.assertTrue(route.unfiltered_query(''))
        self.assertTrue(route.unfiltered_query(self.query()))
        for query in (self.query(filter='["private"]'), self.query(filter='null'),
                      self.query(filter='{}'), self.query(filter=''), self.query(sort='broken'),
                      self.query(sort='{}'), self.query(search='other'), self.query()+'&filter=[]',
                      'filter=[]', 'search=', self.query()+'&sort=', 'other=1'):
            with self.subTest(query=query):
                self.assertFalse(route.unfiltered_query(query))

    async def test_url_and_current_identity_stay_required(self):
        channel='UC'+'a'*22
        target={'identity':channel}
        exact='https://studio.youtube.com/channel/'+channel+'/videos/upload'
        with patch.object(route.rt,'identity',new_callable=AsyncMock,return_value=channel) as identity:
            await route.owner(SimpleNamespace(url=exact+'?'+self.query()),target,'upload')
            for url in (exact+'?'+self.query(filter='[1]'),exact+'#other',exact.replace(channel,'UC'+'b'*22),
                        exact.replace('studio.youtube.com','studio.youtube.com.evil.test'),
                        exact.replace('/upload','/short'),exact.replace('https:','http:')):
                with self.subTest(url=url), self.assertRaises(VpubError):
                    await route.owner(SimpleNamespace(url=url),target,'upload')
            identity.return_value='UC'+'b'*22
            with self.assertRaises(VpubError):
                await route.owner(SimpleNamespace(url=exact+'?'+self.query()),target,'upload')
