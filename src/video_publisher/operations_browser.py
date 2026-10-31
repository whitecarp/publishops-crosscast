"""Bounded operations on official pages using CrossCast's own signed-in profile."""
from pathlib import Path
from datetime import datetime, timedelta
import re
from urllib.parse import urlsplit

from .core import VpubError, atomic_json, digest, read_json, stop_check
from .browser_runtime import risk_check, unique_visible
from .operations_api import metric
from .browser_backends import normalize


def number(text):
    text=str(text or '').strip().replace(',','')
    m=re.fullmatch(r'([0-9]+(?:\.[0-9]+)?)\s*([KkMm万亿]?)\s*%?',text)
    if not m: return None
    value=float(m[1])*{'':1,'k':1000,'m':1000000,'万':10000,'亿':100000000}[m[2].lower()]
    return int(value) if value.is_integer() else value


def douyin_metrics(text):
    lines=[x.strip() for x in text.splitlines() if x.strip()]
    mapping={'views':'播放量','likes':'点赞量','comments':'评论量','shares':'分享量',
             'favorites':'收藏量','danmaku':'弹幕量','completion_percent':'完播率',
             'bounce_2s_percent':'2s跳出率','followers_gained':'涨粉量','followers_lost':'脱粉量'}
    result={}
    for key,label in mapping.items():
        occurrences=[i for i,t in enumerate(lines) if t==label]
        raw=lines[occurrences[0]+1] if len(occurrences)==1 and occurrences[0]+1<len(lines) else None
        result[key]={**metric(number(raw),label,'percent' if key.endswith('_percent') else 'count'),
                     'display':raw,'approximate':bool(raw and re.search('[KkMm万亿]',raw))}
    return result


async def open_work(page, platform, identity, work, public_comments=False, reuse_page=False):
    urls={'instagram':f'https://www.instagram.com/p/{work}/',
          'x':f'https://x.com/{identity}/status/{work}',
          'douyin':f'https://creator.douyin.com/creator-micro/work-management/work-detail/{work}'}
    if platform=='douyin' and public_comments: urls['douyin']=f'https://www.douyin.com/video/{work}'
    same = page.url.rstrip('/') == urls[platform].rstrip('/')
    if not (reuse_page and same):
        await page.goto(urls[platform],wait_until='domcontentloaded',timeout=60000)
    if platform=='instagram':
        await page.locator('svg[aria-label="More options"]').wait_for(state='visible',timeout=45000)
    elif platform=='x':
        await page.locator(f'article a[href="/{identity}/status/{work}"]').first.wait_for(timeout=45000)
    elif public_comments:
        await page.get_by_text('全部评论',exact=True).wait_for(timeout=45000)
    else:
        await page.get_by_text('评论管理',exact=True).wait_for(timeout=45000)
    await risk_check(page)
    current=urlsplit(page.url);allowed={urlsplit(urls[platform]).path.rstrip('/')}
    if platform=='instagram': allowed.add('/reel/'+work)
    if current.hostname!=urlsplit(urls[platform]).hostname or current.path.rstrip('/') not in allowed:
        raise VpubError('wrong_work','The browser left the requested work; no operation attempted.')
    return urls[platform]


async def x_root(page,identity,work):
    return await unique_visible(page.locator('article').filter(has=page.locator(f'a[href="/{identity}/status/{work}"]')),'original post')


async def x_metrics(root):
    values={}
    for key,testid in [('comments','reply'),('reposts','retweet'),('likes','like'),('bookmarks','bookmark')]:
        control=root.locator(f'[data-testid="{testid}"], [data-testid="un{testid}"]')
        raw=await control.first.get_attribute('aria-label') if await control.count() else None
        m=re.match(r'([\d.,]+\s*[KkMm]?)\s',raw or '')
        display=m[1] if m else None
        values[key]={**metric(number(display),'X '+key),'display':raw,
                     'approximate':bool(display and re.search('[KkMm]',display))}
    body=await root.inner_text()
    m=re.search(r'(?:^|\n)([\d.,]+\s*[KkMm]?)\s*\n?\s*Views\b',body,re.I)
    values['post_views']={**metric(number(m[1]) if m else None,'X post views, not video playback'),
                          'display':m[0] if m else None,'approximate':bool(m and re.search('[KkMm]',m[1]))}
    values['video_plays']=metric(None,'Not exposed on the post page')
    return values


async def fill_x_text(page,editor,text):
    # Draft.js can duplicate composed CJK input when filled directly.
    await editor.click()
    await editor.press('ControlOrMeta+A');await editor.press('Backspace')
    await page.keyboard.insert_text(text)
    if normalize(await editor.inner_text())!=normalize(text):
        raise VpubError('reply_text_mismatch','The composer text does not match the supplied reply; nothing sent.')


# Rows are identified from visible Reply controls, account links and timestamps,
# not hidden app stores, private endpoints or text in unrelated page regions.
COMMENT_ROWS=r'''({platform,limit}) => {
 const label=platform==='douyin'?'回复':'Reply';const result=[];const used=new Set();
 const candidates=[...document.querySelectorAll('button,[role=button],span')].filter(e=>e.textContent.trim()===label && e.getClientRects().length);
 for(const button of candidates){
  let row=button.parentElement;
  for(let depth=0;row && depth<8;depth++,row=row.parentElement){
   const author=row.querySelector(platform==='douyin'?'a[href*="/user/"]':'a[href]');
   const time=row.querySelector('time')||row.querySelector('a[href*="/c/"],[class*="time"],[class*="Time"]');
   const replies=[...row.querySelectorAll('button,[role=button],span')].filter(e=>e.textContent.trim()===label);
   if(!author || !time || replies.length>3) continue;
   const raw=row.innerText.trim();if(!raw || raw.length>4000) break;
   const key=author.getAttribute('href')+'|'+(time.getAttribute('datetime')||time.textContent)+'|'+raw;
   if(used.has(key)) break;used.add(key);
   const href=row.querySelector('a[href*="/c/"]')?.getAttribute('href');
   const content=[...row.querySelectorAll('span[dir=auto]')].filter(e=>!e.closest('a,button,[role=button]') && !e.querySelector('time,span[dir=auto]') && e.textContent.trim()!==author.textContent.trim());
   const text=content.map(e=>e.innerText.trim()).filter(Boolean).join('\n');
   result.push({raw,author:author.textContent.trim(),author_href:author.getAttribute('href'),
    text:text||raw,time:time.getAttribute('datetime')||time.textContent,permalink:href||null,index:result.length});
   row.setAttribute('data-crosscast-comment-row',String(result.length-1));
   break;
  }
  if(result.length>=limit)break;
 }
 return result;
}'''

DOUYIN_PUBLIC_ROWS=r'''limit => [...document.querySelectorAll('[data-e2e="comment-item"]')].slice(0,limit).map((row,index)=>{
 const info=row.querySelector('.comment-item-info-wrap');const author=info?.querySelector('a[href*="/user/"]');
 const marker=row.querySelector('[data-e2e="video-comment-more"] [id^="tooltip_"]');
 const id=marker?.id?.match(/^tooltip_(\d+)$/)?.[1];const body=info?.nextElementSibling;
 if(!id || !author || !body) return null;
 row.setAttribute('data-crosscast-comment-row',String(index));
 const owner=[...info.querySelectorAll('.comment-item-tag-text')].some(e=>e.textContent.trim()==='作者');
 const parent=row.parentElement?.closest('[data-e2e="comment-item"]');
 const parentId=parent?.querySelector('[data-e2e="video-comment-more"] [id^="tooltip_"]')?.id?.match(/^tooltip_(\d+)$/)?.[1]||null;
 return {id,text:body.innerText.trim(),author:author.querySelector('[data-click-from="title"]')?.textContent?.trim()||author.textContent.trim(),
 author_id:author.getAttribute('href'),is_owner:owner,parent_id:parentId,parent_relationship:parentId?'dom_nested_thread':null,owner_replied:null,ui_index:index,
 published_at:null,time_label:body.nextElementSibling?.innerText?.trim(),text_scope:'Visible comment text',source:'douyin_public_comment_dom'};
}).filter(Boolean)'''


async def comments(page,platform,identity,work,limit,expand_replies=True):
    if platform=='douyin' and urlsplit(page.url).hostname=='www.douyin.com':
        await page.wait_for_function("() => document.querySelector('[data-e2e=comment-item]') || /暂无评论|暂时没有更多评论/.test(document.body.innerText)",timeout=20000)
        expand=page.locator('[data-e2e="comment-list"]').get_by_text(re.compile(r'^展开\s*\d+\s*条回复'))
        for button in (await expand.all())[:10 if expand_replies else 0]:
            if await button.is_visible(): await button.click()
        rows=await page.evaluate(DOUYIN_PUBLIC_ROWS,limit)
        body=await page.locator('[data-e2e="comment-list"]').inner_text() if await page.locator('[data-e2e="comment-list"]').count() else ''
        if not rows and not re.search('暂无评论|暂时没有更多评论',body):
            raise VpubError('comments_layout_unverified','The public comment list has no verified rows or empty state.')
        return {'items':rows,'complete':False,'scope':'Visible public comment rows; collapsed replies are not a complete export.','reported_total':None}
    if platform=='x':
        root=await x_root(page,identity,work);metrics=await x_metrics(root)
        # Timeline entries may be ranked, collapsed or incomplete. Never call a
        # bounded DOM collection a complete API conversation export.
        items=[]
        for article in await page.locator('main article').all():
            if await article.evaluate('''e => [...document.querySelectorAll('main h2, main span')].some(n=>['Discover more','More posts','More for you'].includes(n.textContent.trim()) && Boolean(n.compareDocumentPosition(e)&Node.DOCUMENT_POSITION_FOLLOWING))'''):
                break
            anchor=article.locator('a[href*="/status/"]').filter(has=page.locator('time'))
            if await anchor.count()!=1: continue
            href=await anchor.get_attribute('href');m=re.fullmatch(r'/([A-Za-z0-9_]+)/status/(\d+)',href or '')
            if not m or m[2]==work: continue
            body=article.locator('[data-testid="tweetText"]')
            if await body.count()!=1 or 'Promoted' in await article.inner_text(): continue
            items.append({'id':m[2],'author':m[1],'author_id':m[1],
                          'text':await body.inner_text(),'is_owner':m[1].lower()==identity.lower(),
                          'parent_id':None,'conversation_work_id':work,'owner_replied':None,'url':'https://x.com'+href,
                          'published_at':await anchor.locator('time').get_attribute('datetime'),
                          'relationship':'visible conversation entry; direct parent not exposed'})
            if len(items)>=limit:break
        return {'items':items,'complete':False,'scope':'Visible conversation only; ranked/collapsed replies may be absent.',
                'reported_total':metrics['comments']['value']}
    if platform=='douyin':
        for attempt in range(3):
            await page.get_by_text('评论管理',exact=True).click()
            try:
                await page.get_by_text('全部评论',exact=True).wait_for(timeout=5000)
                break
            except Exception:
                if attempt==2: raise
        # The tab shell appears before its asynchronous comment result.
        await page.wait_for_function("() => document.body.innerText.includes('暂无更多评论') || [...document.querySelectorAll('button,[role=button],span')].some(e=>e.getClientRects().length && e.textContent.trim()==='回复')",timeout=30000)
    await risk_check(page)
    body=await page.locator('body').inner_text()
    empty=('No comments yet.' in body) if platform=='instagram' else ('暂无更多评论' in body and '全部评论' in body)
    raw=await page.evaluate(COMMENT_ROWS,{'platform':platform,'limit':limit})
    items=[]
    for row in raw:
        match=re.search(r'/c/(\d+)',row['permalink'] or '')
        fingerprint=digest([row['author_href'],row['time'],normalize(row['text'])])
        items.append({'id':match[1] if match else 'ui-'+fingerprint[:32],'parent_id':None,
                      'text':row['text'],'author':row['author'],'author_id':row['author_href'],
                      'published_at':row['time'],'is_owner':row['author_href'].strip('/')==identity,
                      'owner_replied':None,'ui_fingerprint':fingerprint,'ui_index':row['index'],
                      'text_scope':'Visible comment text' if row['text']!=row['raw'] else 'Visible row; extraction incomplete'})
    if not items and not empty:
        raise VpubError('comments_layout_unverified','No identifiable comment rows or explicit empty state; not reported as zero comments.')
    return {'items':items,'complete':False,'scope':'Visible rows only; no automatic scrolling or complete history claim.',
            'reported_total':0 if empty else None}


async def execute(page,platform,identity,payload):
    work=payload['work_id'];action=payload['operation']
    url=await open_work(page,platform,identity,work,public_comments=platform=='douyin' and action not in {'stats','test_seed','reply'},reuse_page=payload.get('reuse_page',False))
    if platform=='douyin' and action=='reply':
        from .operations_douyin import reply as creator_reply
        return await creator_reply(page,identity,payload)
    if action=='stats':
        if platform=='douyin':
            await page.get_by_text('播放量',exact=True).wait_for(timeout=30000)
            metrics=douyin_metrics(await page.locator('body').inner_text())
        elif platform=='x': metrics=await x_metrics(await x_root(page,identity,work))
        else:
            body=(await page.locator('body').inner_text()).split('More posts from',1)[0]
            m=re.search(r'(?:^|\n)([\d,.]+(?:[KM])?)\s+likes\b',body,re.I)
            others=re.search(r'Liked by .+? and ([\d,]+) others',body)
            likes=number(m[1]) if m else (number(others[1])+1 if others else None)
            metrics={'likes':metric(likes,'Visible Instagram likes'),
                     'comments':metric(0 if 'No comments yet.' in body else None,'Explicit empty comment state'),
                     'views':metric(None,'Not exposed on this post surface'),
                     'reach':metric(None,'Not collected from account insights')}
        return {'state':'OBSERVED','identity':identity,'url':url,'source':'official_page_dom','metrics':metrics}
    if action=='comments':
        if payload.get('cursor'): raise VpubError('cursor_unsupported','Browser comments expose the visible page only; no fabricated page cursor.')
        if 'pages' in payload:
            from .comment_collection import collect
            return {'state':'OBSERVED','identity':identity,'url':url,
                    **await collect(page,platform,identity,work,payload['limit'],payload['pages'],payload.get('reply_pages',1))}
        return {'state':'OBSERVED','identity':identity,'url':url,'source':'official_page_dom',
                **await comments(page,platform,identity,work,payload['limit'])}
    if action in {'test_seed','test_seed_verify'}:
        test=payload['test_request']
        if identity!=test['identity']: raise VpubError('wrong_account','Test account changed.')
        if action=='test_seed':
            if platform=='x':
                root=await x_root(page,identity,work)
                await root.locator('[data-testid="reply"]').click()
                await page.locator('[role="dialog"][aria-modal="true"]').wait_for(state='visible',timeout=15000)
                scope=await unique_visible(page.locator('[role="dialog"][aria-modal="true"]'),'test reply composer')
                editor=await unique_visible(scope.locator('[data-testid="tweetTextarea_0"]'),'test text')
                button=await unique_visible(scope.locator('[data-testid="tweetButton"]'),'test send')
            elif platform=='instagram':
                editor=await unique_visible(page.locator('textarea[aria-label="Add a comment…"]'),'test comment')
                button=None
            else:
                await comments(page,platform,identity,work,100)
                editor=await unique_visible(page.locator('textarea:visible,[contenteditable=true]:visible'),'test comment')
                button=await unique_visible(page.get_by_role('button',name='发送',exact=True),'test send')
            if platform=='x': await fill_x_text(page,editor,'你好')
            else: await editor.fill('你好')
            await risk_check(page);stop_check(platform)
            if platform=='instagram': button=await unique_visible(page.get_by_text('Post',exact=True),'test send')
            if not await button.is_enabled(): raise VpubError('reply_not_ready','Test submission is disabled.')
            await button.click(trial=True,timeout=10000)
            atomic_json(Path(payload['test_receipt_path']),{**test,'state':'TEST_SEED_SUBMITTING'})
            await button.click(timeout=20000)
            saved={**test,'state':'TEST_SEED_UNVERIFIED'}
            atomic_json(Path(payload['test_receipt_path']),saved)
            await page.wait_for_timeout(1500)
            if platform=='douyin': await open_work(page,platform,identity,work,public_comments=True)
        actual=await comments(page,platform,identity,work,100)
        matches=[c for c in actual['items'] if c['id'] not in test['before_ids'] and c['is_owner']
                 and '你好' in c['text'].splitlines()]
        return {'state':'TEST_SEED_VERIFIED' if len(matches)==1 else 'TEST_SEED_UNVERIFIED',
                'comment_id':matches[0]['id'] if len(matches)==1 else test.get('comment_id')}
    request=payload['reply_request'];target=request['comment']
    if identity!=request['expected_identity']: raise VpubError('wrong_account','Account changed since comment collection.')
    if action=='verify_reply':
        # Read only; no reply controls or submit buttons are clicked.
        actual=await comments(page,platform,identity,work,100)
        saved=payload.get('existing_reply',{})
        texts={normalize(saved.get('submitted_text') or request['text'])}
        if not saved.get('submitted_text') and platform=='instagram':
            texts.add(normalize('@'+target['author']+' '+request['text']))
        before=set(saved.get('before_reply_ids',[target['id']]))
        cutoff=datetime.fromisoformat(saved['at'])-timedelta(seconds=2) if saved.get('at') else None
        matches=[]
        for c in actual['items']:
            if not c['is_owner'] or normalize(c['text']) not in texts or c['id'] in before: continue
            if platform=='douyin' and c.get('parent_id')!=(target.get('parent_id') or target['id']):continue
            try: recent=cutoff and datetime.fromisoformat((c.get('published_at') or '').replace('Z','+00:00'))>=cutoff
            except (ValueError,KeyError,TypeError): recent=False
            if recent or (platform=='douyin' and saved.get('before_reply_ids') and saved.get('submitted_text')): matches.append(c)
        rid=saved.get('reply_id')
        if rid: matches=[c for c in matches if c['id']==rid]
        # These browser rows do not expose an API parent relationship. Verification
        # combines the recorded targeted Reply action with a fresh owner/text/ID;
        # its evidence label must retain that narrower boundary.
        verified=len(matches)==1 and saved.get('state') in {'REPLY_UNVERIFIED','REPLY_VERIFIED'}
        evidence='creator_reply_action_and_fresh_nested_comment' if platform=='douyin' else 'targeted_reply_action_and_fresh_author_text_permalink'
        return {'state':'REPLY_VERIFIED' if verified else 'REPLY_UNVERIFIED','reply_id':matches[0]['id'] if verified else rid,
                'platform_writes':0,'verification_source':evidence if verified else 'parent_relationship_not_verified',
                'hint':None if verified else 'No confirmed reply read-back; do not resend.',
                'parent_evidence':'Fresh nested DOM row identifies the parent comment; not an official API integration.' if platform=='douyin' and verified else 'Clicked Reply on the exact collected comment; browser DOM does not expose an API parent ID.'}
    if action!='reply': raise VpubError('unsupported_operation','Unsupported browser operation.')
    current=await comments(page,platform,identity,work,100)
    matches=[c for c in current['items'] if c['id']==target['id'] and normalize(c['text'])==normalize(target['text'])]
    if len(matches)!=1: raise VpubError('comment_changed','The exact comment is not visible or changed; collect it again.')
    actual=matches[0]
    if platform=='x':
        row=await unique_visible(page.locator('main article').filter(has=page.locator(f'a[href="/{actual["author"]}/status/{actual["id"]}"]')),'target reply')
        await row.locator('[data-testid="reply"]').click()
        await page.locator('[role="dialog"][aria-modal="true"]').wait_for(state='visible',timeout=15000)
        dialog=await unique_visible(page.locator('[role="dialog"][aria-modal="true"]'),'reply composer')
        editor=await unique_visible(dialog.locator('[data-testid="tweetTextarea_0"]'),'reply text')
        await fill_x_text(page,editor,request['text'])
        button=await unique_visible(dialog.locator('[data-testid="tweetButton"]'),'reply submit')
    else:
        row=page.locator(f'[data-crosscast-comment-row="{actual["ui_index"]}"]')
        await (await unique_visible(row.get_by_text('回复' if platform=='douyin' else 'Reply',exact=True),'comment Reply')).click()
        if platform=='instagram':
            editor=await unique_visible(page.locator('textarea[aria-label="Add a comment…"]'),'reply editor')
        else:
            editor=await unique_visible(page.locator('textarea:visible,[contenteditable=true]:visible'),'reply editor')
        # Do not replace a pre-filled @mention; only append the single-language text.
        prefix=(await editor.input_value() if await editor.evaluate('e=>e.tagName')=='TEXTAREA' else await editor.inner_text()).strip()
        if platform=='douyin' and urlsplit(page.url).hostname=='www.douyin.com':
            await fill_x_text(page,editor,request['text'])
            container=page.locator('.comment-input-inner-container').filter(has=editor)
            button=await unique_visible(container.locator('.commentInput-right-ct span').filter(has=page.locator('svg path[fill="#FE2C55"]')),'reply send arrow')
        else:
            await editor.fill(prefix+(' ' if prefix else '')+request['text'])
            button=await unique_visible(page.get_by_text('发送' if platform=='douyin' else 'Post',exact=True),'reply submit')
    await risk_check(page);stop_check(platform)
    if not await button.is_enabled(): raise VpubError('reply_not_ready','The platform has not enabled reply submission.')
    path=Path(payload['reply_receipt_path']);saved=read_json(path)
    saved['before_reply_ids']=[c['id'] for c in current['items']]
    saved['submitted_text']=await editor.input_value() if await editor.evaluate('e=>e.tagName')=='TEXTAREA' else await editor.inner_text()
    await button.click(trial=True,timeout=10000)
    saved['state']='REPLY_SUBMITTING';atomic_json(path,saved)
    await button.click(timeout=20000)
    # A successful click alone is not confirmed visibility. Keep this durable
    # uncertainty; verify-reply reads the original conversation without resending.
    saved['state']='REPLY_UNVERIFIED';atomic_json(path,saved)
    # Keep the page alive for the asynchronous send. Closing immediately after a
    # successful click can cancel the request before the platform finishes it.
    await page.wait_for_timeout(3000)
    await risk_check(page)
    return {'state':'REPLY_UNVERIFIED','platform_writes':1,'reply_id':None,
            'hint':'Submitted once; the platform has not supplied a verified reply ID/parent relationship. Inspect the existing reply; no automatic resend.'}
