import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock, AsyncMock
from video_publisher import messages, mentions_browser as mentions, comment_actions as actions
from video_publisher import comment_actions_api as api, youtube_moderation as moderation
from video_publisher.core import VpubError, home, atomic_json, read_json, digest


class MentionTests(unittest.TestCase):
    def test_preview_time_change_dedup_and_no_false_stable_id(self):
        first=mentions.preview('instagram',{'text':'viewer\nmentioned you: Hello\n2h','links':['/p/abc/']})
        second=mentions.preview('instagram',{'text':'viewer\nmentioned you: Hello\n3h','links':['/p/abc/']})
        self.assertEqual(first['id'],second['id']);self.assertFalse(first['stable_source_id'])
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,VPUB_HOME=tmp):
            value={'identity':'owner','items':[first]}
            self.assertTrue(messages.merge('instagram','mentions',value)['baseline'])
            row=messages.listing()['items'][0];messages.mark(row['key'],'handled')
            self.assertFalse(row['stable_source_id'])
            self.assertEqual(0,messages.merge('instagram','mentions',{**value,'items':[second]})['newly_observed'])
            self.assertEqual([],messages.listing()['items'])

    def test_notification_not_identified_by_post_alone_and_external_url_removed(self):
        a=mentions.preview('instagram',{'text':'A mentioned you','links':['/p/abc/','https://example.com/private']})
        b=mentions.preview('instagram',{'text':'B mentioned you','links':['/p/abc/']})
        self.assertNotEqual(a['id'],b['id']);self.assertEqual('https://www.instagram.com/p/abc/',a['url'])
        self.assertIsNone(mentions.preview('douyin',{'text':'A','links':['https://www.douyin.com.evil.example/video/123']})['url'])


class ModerationQueueTests(unittest.TestCase):
    def setUp(self):
        self.service=MagicMock()
        self.thread={'id':'c','snippet':{'videoId':'abcdefghijk','totalReplyCount':0,
            'topLevelComment':{'id':'c','snippet':{'videoId':'abcdefghijk','textOriginal':'Hi','moderationStatus':'heldForReview'}}}}

    def test_held_root_omitted_from_comments_id_is_read_from_owner_queue(self):
        s=self.service;s.channels().list().execute.return_value={'items':[{'id':'owner'}]}
        s.videos().list().execute.return_value={'items':[{'snippet':{'channelId':'owner'}}]}
        s.comments().list().execute.return_value={'items':[]}
        s.commentThreads().list().execute.side_effect=[{'items':[]},{'items':[self.thread]}]
        r={'platform':'youtube','work_id':'abcdefghijk','identity':'owner','kind':'moderate','comment':{'id':'c','text':'Hi'}}
        with patch.object(api.adapters,'yt_service',return_value=s):result=api.execute(r,'status')
        self.assertEqual('heldForReview',result['moderation_status']);self.assertFalse(result['deleted'])
        s.comments().setModerationStatus.assert_not_called()

    def test_wrong_video_queue_is_not_accepted(self):
        self.thread['snippet']['videoId']='another'
        self.service.commentThreads().list().execute.return_value={'items':[self.thread]}
        with self.assertRaises(VpubError):moderation.root_in_queues(self.service,'abcdefghijk','c')

    def test_conflicting_or_absent_queue_membership_is_unknown(self):
        self.service.commentThreads().list().execute.return_value={'items':[self.thread]}
        self.assertIsNone(moderation.root_in_queues(self.service,'abcdefghijk','c'))
        self.service.commentThreads().list().execute.return_value={'items':[]}
        self.assertIsNone(moderation.root_in_queues(self.service,'abcdefghijk','c'))

    def test_held_child_does_not_prove_root_state(self):
        self.thread['snippet']['totalReplyCount']=1
        self.thread['snippet']['topLevelComment']['snippet'].pop('moderationStatus')
        self.service.commentThreads().list().execute.side_effect=[{'items':[]},{'items':[self.thread]}]
        self.assertIsNone(moderation.root_in_queues(self.service,'abcdefghijk','c')['status'])

    def test_root_and_reply_cursor_loops_stop(self):
        s=self.service;s.commentThreads().list().execute.return_value={'items':[],'nextPageToken':'again'}
        s.comments().list().execute.return_value={'items':[],'nextPageToken':'again'}
        with self.assertRaises(VpubError):moderation.root_in_queues(s,'abcdefghijk','c')
        with self.assertRaises(VpubError):moderation.state(s,'abcdefghijk','child','c')


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        env=patch.dict(os.environ,VPUB_HOME=tmp.name);env.start();self.addCleanup(env.stop)
        self.key='a'*32;self.path=home()/'operations/comment-actions'/(self.key+'.json')
        self.request={'platform':'x','identity':'owner','work_id':'12345678','comment':{'id':'98765432','text':'Hi'},'kind':'hide','desired':True}
        self.before={'identity':'owner','text':'Hi','hidden':False}
        self.saved={'key':self.key,'state':'COMMENT_UNVERIFIED','request':self.request,'before':self.before}
        atomic_json(self.path,self.saved)
        self.index=home()/'operations/comment-targets'/(digest(['x','owner','12345678','98765432'])+'.json')
        atomic_json(self.index,{'key':self.key})

    def test_already_applied_recovery_is_read_only(self):
        with patch.object(actions,'dispatch',return_value={**self.before,'hidden':True}) as dispatch:
            result=actions.resume(self.key)
        self.assertEqual('COMMENT_VERIFIED',result['state']);self.assertEqual(0,result['platform_writes']);self.assertEqual(1,dispatch.call_count)

    def test_unknown_or_changed_state_never_retries(self):
        for change in ({'hidden':None},{'text':'Changed'},{'identity':'other'}):
            with patch.object(actions,'dispatch',return_value={**self.before,**change}) as dispatch:
                with self.assertRaises(VpubError):actions.resume(self.key)
                self.assertEqual(1,dispatch.call_count)

    def test_stale_receipt_and_irreversible_action_are_rejected(self):
        atomic_json(self.index,{'key':'b'*32})
        with patch.object(actions,'dispatch') as dispatch:
            with self.assertRaises(VpubError):actions.resume(self.key)
            dispatch.assert_not_called()
        atomic_json(self.index,{'key':self.key});self.saved['request']['kind']='delete';atomic_json(self.path,self.saved)
        with self.assertRaises(VpubError):actions.resume(self.key)

    def test_explicit_recovery_keeps_receipt_and_one_mutation(self):
        def dispatch(r,operation='status',path=None):
            if operation=='set':actions.submitting(path);return {'state':'COMMENT_UNVERIFIED','platform_writes':1}
            return {**self.before,'hidden':bool(read_json(self.path).get('recovery_attempts'))}
        with patch.object(actions,'dispatch',side_effect=dispatch) as calls,patch.object(actions.ops,'record',side_effect=lambda *a:a[-1]):
            result=actions.resume(self.key)
        self.assertEqual('COMMENT_VERIFIED',result['state']);self.assertEqual(self.key,result['key'])
        self.assertEqual(1,len(result['recovery_attempts']));self.assertEqual(1,sum(len(c.args)>1 and c.args[1]=='set' for c in calls.call_args_list))


class MentionDOMTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from patchright.async_api import async_playwright
        self.pw=await async_playwright().start();self.browser=await self.pw.chromium.launch(channel='chrome',headless=True)
        self.page=await self.browser.new_page();await self.page.route('**/*',lambda r:r.abort())
    async def asyncTearDown(self):
        await self.browser.close();await self.pw.stop()

    async def test_ig_only_notification_panel_not_feed_or_follow_suggestions(self):
        await self.page.set_content('''<div id="panel"><section><a href="/viewer/">viewer</a><span>mentioned you in a comment: Hello</span></section><section><a href="/suggested/">Follow</a></section></div><main><a href="/unrelated/">unrelated</a><span>mentioned you</span></main>''')
        rows=await self.page.locator('#panel').evaluate(mentions.IG_ROWS)
        self.assertEqual(1,len(rows));self.assertIn('Hello',rows[0]['text']);self.assertNotIn('unrelated',str(rows))

    async def test_douyin_mention_body_preserved_but_relative_time_excluded(self):
        await self.page.set_content('''<div id="panel"><div><a href="/user/viewer"><div data-e2e="user-name-card">Viewer</div></a><pre>@owner Hello</pre><div><span>在评论中提到了你</span><span>刚刚</span></div></div><div>暂时没有更多了</div></div>''')
        rows=await self.page.locator('#panel').evaluate(mentions.DY_ROWS)
        self.assertEqual(1,len(rows));self.assertIn('@owner Hello',rows[0]['text']);self.assertNotIn('刚刚',rows[0]['text'])

    async def test_douyin_like_or_comment_is_not_a_mention(self):
        await self.page.set_content('''<div id="panel"><div><a href="/user/viewer"><div data-e2e="user-name-card">Viewer</div></a><pre>Hello @someone</pre><span>评论了你的作品</span></div></div>''')
        self.assertEqual([],await self.page.locator('#panel').evaluate(mentions.DY_ROWS))

    async def test_captcha_iframe_is_detected_even_with_blank_body_text(self):
        from video_publisher.browser_runtime import risk_check
        await self.page.set_content('<iframe src="https://example.test/verifycenter/captcha/v2"></iframe>')
        with self.assertRaisesRegex(VpubError,'human verification'):await risk_check(self.page)

    async def test_hidden_captcha_iframe_does_not_block_normal_page(self):
        from video_publisher.browser_runtime import risk_check
        await self.page.set_content('<iframe style="display:none" src="https://example.test/verifycenter/captcha/v2"></iframe>')
        await risk_check(self.page)

    async def test_x_first_hide_confirmation_without_testid(self):
        from video_publisher import comment_actions_x as x
        target={'id':'98765432','author':'viewer','text':'Hi','conversation_work_id':'12345678'}
        await self.page.set_content('''<main><article><a href="/viewer/status/98765432"><time>now</time></a><div data-testid="tweetText">Hi</div><button data-testid="caret" onclick="document.getElementById('menu').style.display='block'">More</button></article></main>
        <div id="menu" role="menu" style="display:none"><div role="menuitem" onclick="this.parentElement.style.display='none';document.getElementById('confirm').style.display='block'">Hide reply</div></div>
        <div id="confirm" role="dialog" style="display:none">Hiding replies to your posts<button onclick="document.body.dataset.confirmed='true';this.parentElement.remove()">Hide reply</button></div>''')
        with tempfile.TemporaryDirectory() as tmp:
            from pathlib import Path
            path=Path(tmp)/'receipt.json';atomic_json(path,{'state':'COMMENT_READY'})
            with patch.object(x,'open_work',new=AsyncMock()),patch.object(x,'comments',new=AsyncMock(return_value={'items':[target]})):
                result=await x.execute(self.page,'owner',{'request':{'identity':'owner','work_id':'12345678','comment':target,'kind':'hide','desired':True},'operation':'set','receipt_path':path})
        self.assertEqual('true',await self.page.locator('body').get_attribute('data-confirmed'));self.assertEqual(1,result['platform_writes'])
        self.assertEqual('COMMENT_UNVERIFIED',result['state'])

    async def test_x_hidden_reply_entry_in_owned_post_menu(self):
        from video_publisher import comment_actions_x as x
        target={'id':'98765432','author':'viewer','text':'Hi','conversation_work_id':'12345678'}
        await self.page.set_content('''<main><article><a href="/owner/status/12345678"><time>now</time></a><button data-testid="caret" onclick="document.getElementById('link').style.display='block'">More</button></article>
        <article id="reply" style="display:none"><a href="/viewer/status/98765432"><time>now</time></a><div data-testid="tweetText">Hi</div><button data-testid="caret" onclick="document.getElementById('menu').style.display='block'">More</button></article></main>
        <a id="link" style="display:none" href="/owner/status/12345678/hidden" onclick="event.preventDefault();document.getElementById('reply').style.display='block';this.style.display='none'">View hidden replies</a>
        <div id="menu" role="menu" style="display:none"><div role="menuitem">Unhide reply</div></div>''')
        with patch.object(x,'open_work',new=AsyncMock()),patch.object(x,'comments',new=AsyncMock(return_value={'items':[]})):
            result=await x.execute(self.page,'owner',{'request':{'identity':'owner','work_id':'12345678','comment':target},'operation':'status'})
        self.assertTrue(result['hidden']);self.assertEqual('Hi',result['text'])


class SourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_douyin_dm_restores_creator_and_rechecks_identity(self):
        from video_publisher.message_sources import execute
        page=MagicMock();creator=MagicMock();value={'identity':'owner','items':[]}
        with patch.object(mentions,'douyin_mentions',new=AsyncMock(return_value=value)),patch('video_publisher.browser_runtime.signed_in_page',new=AsyncMock(return_value=(creator,'owner'))) as signed,patch('video_publisher.direct_messages_browser.execute',new=AsyncMock(return_value=value)) as dm:
            result=await execute(page,'douyin','owner',{'sources':['mentions','dm_preview'],'limit':20})
        signed.assert_awaited_once_with(page.context,'douyin','owner');self.assertIs(creator,dm.await_args.args[0])
        self.assertEqual('OBSERVED',result['sources']['dm_preview']['state'])

    async def test_challenged_source_stops_without_alternate_route(self):
        from video_publisher.message_sources import execute
        with patch.object(mentions,'douyin_mentions',new=AsyncMock(side_effect=VpubError('human_verification_required','Stop'))),patch('video_publisher.direct_messages_browser.execute',new=AsyncMock()) as dm:
            result=await execute(MagicMock(),'douyin','owner',{'sources':['mentions','dm_preview'],'limit':20})
        dm.assert_not_awaited();self.assertEqual('NOT_CHECKED',result['sources']['dm_preview']['state'])
