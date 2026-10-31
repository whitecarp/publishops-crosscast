import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from video_publisher import direct_messages as dm, comment_actions as actions, comment_actions_api as api
from video_publisher.direct_messages_browser import new_outgoing, MESSAGE_ROWS
from video_publisher.comment_actions_douyin import observed
from video_publisher.core import VpubError, atomic_json, home, read_json
from video_publisher import cli, operations


class MessageTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        env=patch.dict(os.environ,VPUB_HOME=tmp.name);env.start();self.addCleanup(env.stop)
        auth=patch.object(dm,'identity',return_value='owner');auth.start();self.addCleanup(auth.stop)
        self.before={'identity':'owner','recipient':'recipient','can_send':True,'items':[]}

    def worker(self,p,action,expected,**kw):
        if action=='read':return self.before
        if action=='send':
            dm.submitting(kw['receipt_path'])
            raise TimeoutError('connection lost after submit')
        return {'identity':'owner','recipient':'recipient','matching_outgoing_message':True}

    def test_uncertain_send_blocks_changed_text_and_only_verify_can_finish(self):
        with patch.object(dm,'dispatch',side_effect=self.worker) as worker:
            first=dm.send('instagram','recipient','Hello','en',test_id='unit')
            self.assertEqual('DM_UNVERIFIED',first['state'])
            again=dm.send('instagram','recipient','Changed','en')
            self.assertEqual(first['key'],again['key']);self.assertEqual(2,worker.call_count)
            verified=dm.verify(first['key']);self.assertEqual('DM_VISIBLE',verified['state'])
            duplicate=dm.send('instagram','recipient','Hello','en')
            self.assertTrue(duplicate['reused']);self.assertEqual(3,worker.call_count)

    def test_wrong_recipient_stops_before_send(self):
        self.before['recipient']='someone_else'
        with patch.object(dm,'dispatch',return_value=self.before) as worker:
            with self.assertRaises(VpubError):dm.send('instagram','recipient','Hello','en')
        self.assertEqual(1,worker.call_count)

    def test_verification_wrong_account_never_completes(self):
        with patch.object(dm,'dispatch',side_effect=self.worker):first=dm.send('instagram','recipient','Hello','en')
        with patch.object(dm,'dispatch',return_value={'identity':'other','recipient':'recipient','matching_outgoing_message':True}):
            self.assertEqual('DM_UNVERIFIED',dm.verify(first['key'])['state'])

    def test_dry_run_sends_nothing_and_has_no_receipt(self):
        with patch.object(dm,'dispatch',return_value=self.before) as worker:
            self.assertEqual('DRY_RUN',dm.send('instagram','recipient','Hello','en',dry_run=True)['state'])
        self.assertEqual(1,worker.call_count)
        self.assertFalse((home()/'operations/dm-actions').exists())

    def test_missing_language_and_bad_target_rejected(self):
        for p,who in [('youtube','channel'),('instagram','../another'),('x','--file')]:
            with self.assertRaises(VpubError):dm.recipient(p,who)
        for lang in ('','auto','en zh'):
            with self.assertRaises(VpubError):dm.validate_text('Hi',lang)

    def test_report_event_excludes_private_body_and_contact(self):
        with patch.object(dm,'dispatch',side_effect=self.worker):dm.send('instagram','recipient','PRIVATE MESSAGE','en')
        events=list((home()/'operations/events').glob('*.json'))
        self.assertEqual(1,len(events));text=events[0].read_text(encoding='utf-8')
        self.assertNotIn('PRIVATE MESSAGE',text);self.assertNotIn('recipient',text)

    def test_incoming_or_old_same_text_is_not_success(self):
        old={'items':[{'sender':'owner','text':'Hi'}]}
        self.assertFalse(new_outgoing([{'sender':'viewer','text':'Hi'}],{},'Hi','owner'))
        self.assertFalse(new_outgoing(old['items'],old,'Hi','owner'))
        self.assertTrue(new_outgoing(old['items'],{'items':[]},'Hi','owner'))
        self.assertFalse(new_outgoing(old['items'],{'visible_text':'Hi'},'Hi','owner'))

    def test_parser_keeps_pin_hide_and_dm_explicit(self):
        parser=cli.parser()
        self.assertTrue(parser.parse_args(['comments','pin','bilibili','BV1234567890','1','--remove']).remove)
        self.assertEqual('hide',parser.parse_args(['comments','hide','douyin','123456789','1']).action)
        self.assertEqual('en',parser.parse_args(['dm','send','instagram','recipient','--text','Hi','--language','en']).language)

    def test_unsupported_hiding_does_not_touch_network(self):
        with patch.object(actions,'dispatch') as network:
            with self.assertRaises(VpubError):actions.mutate('douyin','123456789','1','hide')
        network.assert_not_called()

    def test_reports_dedupe_and_exclude_message_tests(self):
        events=[{'action':'direct_message','platform':'instagram','work_id':None,'at':'2026-10-30T00:00:00Z','event_id':str(i),
                 'result':{'key':key,'state':state,'test':test}} for i,(key,state,test) in enumerate([
                    ('one','DM_UNVERIFIED',False),('one','DM_VISIBLE',False),('test','DM_VISIBLE',True),('failed','DM_FAILED',False)])]
        for language in ('zh','en'):
            result=operations.report('2026-10-29T00:00:00Z',language,_events=events)
            self.assertEqual(1,result['platforms']['instagram']['direct_messages_visible'])
            self.assertEqual(0,result['platforms']['instagram']['direct_messages_pending'])
            self.assertEqual(1,result['platforms']['instagram']['direct_messages_failed'])


class DouyinStateTests(unittest.TestCase):
    def setUp(self):
        self.r={'work_id':'123456789','comment':{'id':'42','text':'Hello'},'kind':'delete','verification':True}
    def test_absence_in_partial_page_is_not_deletion(self):
        with self.assertRaises(VpubError):observed({'status_code':0,'comments':[],'has_more':True},self.r,'owner')
    def test_complete_page_absence_is_verified_only_in_verification(self):
        data={'status_code':0,'comments':[],'has_more':False}
        self.assertTrue(observed(data,self.r,'owner')['deleted'])
        with self.assertRaises(VpubError):observed(data,{**self.r,'verification':False},'owner')
    def test_wrong_work_or_changed_text_rejected(self):
        data={'status_code':0,'comments':[{'cid':'42','aweme_id':'different','text':'Hello'}]}
        with self.assertRaises(VpubError):observed(data,self.r,'owner')
    def test_unknown_like_value_does_not_become_false(self):
        c={'cid':'42','aweme_id':'123456789','text':'Hello','user_digged':None}
        self.assertIsNone(observed({'status_code':0,'comments':[c]},self.r,'owner')['liked'])


class MessageDOMTests(unittest.IsolatedAsyncioTestCase):
    async def test_sender_is_scoped_to_one_message_not_adjacent_message(self):
        from patchright.async_api import async_playwright
        async with async_playwright() as pw:
            browser=await pw.chromium.launch(channel='chrome',headless=True)
            try:
                page=await browser.new_page();await page.route('**/*',lambda r:r.abort())
                await page.set_content('''<div id="list"><div><div role="article" aria-roledescription="message">Hello</div><button aria-label="Reply to message from viewer"></button></div>
                <div><div role="article" aria-roledescription="message">Bye</div><button aria-label="Reply to message from owner"></button></div></div>''')
                items=await page.locator('#list').evaluate(MESSAGE_ROWS)
                self.assertEqual([{'sender':'viewer','text':'Hello'},{'sender':'owner','text':'Bye'}],items)
                self.assertFalse(new_outgoing(items,{},'Hello','owner'))
            finally:await browser.close()


class PinAndRecipientTests(unittest.TestCase):
    def test_pin_does_not_replace_another_comment(self):
        from video_publisher import comment_actions_api as api
        r={'platform':'bilibili','identity':'42','work_id':'BV1234567890','comment':{'id':'7','text':'Hi'},'kind':'pin','desired':True}
        replies={'root':{'rpid':7,'content':{'message':'Hi'},'action':0}}
        with patch.object(api.adapters,'auth_status',return_value={'identity':'42'}), patch.object(api.adapters,'bili',side_effect=[{'owner':{'mid':42}},replies,{'upper':{'top':{'rpid':8}}}]) as calls:
            with self.assertRaisesRegex(VpubError,'Another comment'):api.execute(r,'set')
        self.assertEqual(3,calls.call_count)
        self.assertFalse(any(c.args[0][:2]==['comment','pin'] for c in calls.call_args_list))

    def test_pin_read_has_explicit_unknown_schema_and_root_guards(self):
        from video_publisher import comment_actions_api as api
        r={'platform':'bilibili','identity':'42','work_id':'BV1234567890','comment':{'id':'7','text':'Hi'},'kind':'pin','desired':True}
        replies={'root':{'rpid':7,'content':{'message':'Hi'},'action':0}}
        with patch.object(api.adapters,'auth_status',return_value={'identity':'42'}), patch.object(api.adapters,'bili',side_effect=[{'owner':{'mid':42}},replies,{}]):
            with self.assertRaisesRegex(VpubError,'state unavailable'):api.execute(r,'status')
        r['comment']['parent_id']='7'
        with patch.object(api.adapters,'auth_status',return_value={'identity':'42'}), patch.object(api.adapters,'bili',side_effect=[{'owner':{'mid':42}},replies]):
            with self.assertRaisesRegex(VpubError,'top-level'):api.execute(r,'status')

    def test_x_conversation_must_match_selected_numeric_user(self):
        from video_publisher.direct_messages_x import conversation_id
        self.assertEqual('123-456',conversation_id('https://x.com/i/chat/123-456','456'))
        for url in ('https://x.com/i/chat/123-789','https://x.com/i/chat/pin/verify','https://x.com/i/chat/123-456-789'):
            with self.assertRaises(VpubError):conversation_id(url,'456')

    def test_douyin_requires_observed_profile_id_not_nickname(self):
        with self.assertRaises(VpubError):dm.recipient('douyin','some_nickname')
        self.assertEqual('MS4wAbcDEf123456',dm.recipient('douyin','MS4wAbcDEf123456'))

    def test_truncated_item_list_does_not_reduce_preflight_count(self):
        from video_publisher.direct_messages_browser import outgoing_counts
        rows=[{'sender':'owner','text':'Hi'}]*25
        before={'items':rows[:20],'visible_outgoing_counts':outgoing_counts(rows,'owner')}
        self.assertFalse(new_outgoing(rows,before,'Hi','owner'))
        self.assertTrue(new_outgoing(rows+[rows[0]],before,'Hi','owner'))

    def test_unlock_is_an_explicit_x_only_command(self):
        parser=cli.parser()
        self.assertEqual('unlock',parser.parse_args(['dm','unlock','x']).action)
        with self.assertRaises(SystemExit):parser.parse_args(['dm','unlock','instagram'])


class AdditionalMessageDOMTests(unittest.IsolatedAsyncioTestCase):
    async def test_platform_ownership_and_sent_status(self):
        from patchright.async_api import async_playwright
        from video_publisher.direct_messages_browser import INBOX_SUMMARIES
        from video_publisher.direct_messages_x import MESSAGE_ROWS as X_ROWS
        from video_publisher.direct_messages_douyin import MESSAGE_ROWS as DY_ROWS
        async with async_playwright() as pw:
            browser=await pw.chromium.launch(channel='chrome',headless=True)
            try:
                page=await browser.new_page();await page.route('**/*',lambda r:r.abort())
                await page.set_content('''<div id="x"><div data-dm-message-row="one" data-send-status="sent"><span hidden id="one-sender">You</span><div role="article" aria-labelledby="one-sender"><div data-testid="message-text-one"><span dir="auto">Hello</span><span>10:00</span></div></div></div>
                <div data-dm-message-row="two" data-send-status="failed"><span hidden id="two-sender">You</span><div role="article" aria-labelledby="two-sender"><div data-testid="message-text-two"><span dir="auto">Fail</span></div></div></div></div>
                <div id="dy"><div class="messageMessageBoxcontentBox messageMessageBoxisFromMe"><span class="TextMessageTexttextInnerContent">Sent</span></div><div class="messageMessageBoxcontentBox"><span class="TextMessageTexttextInnerContent">Incoming</span></div></div>''')
                rows=await page.locator('#x').evaluate(X_ROWS,'owner')
                self.assertEqual({'id':'one','text':'Hello','sender':'owner'},rows[0]);self.assertIsNone(rows[1]['sender'])
                rows=await page.locator('#dy').evaluate(DY_ROWS,'owner')
                self.assertEqual([{'text':'Sent','sender':'owner'},{'text':'Incoming','sender':None}],rows)
                await page.set_content('<div role="presentation"><div role="button">note<br>Your note</div></div><div role="button">Contact<br>Message</div>')
                self.assertEqual(['Contact\nMessage'],await page.locator('[role=button]').evaluate_all(INBOX_SUMMARIES))
            finally:await browser.close()
