"""Official-page edits using the existing CLI-owned session, not the host browser."""
from pathlib import Path
import re
from urllib.parse import urlsplit
from .core import VpubError, atomic_json, file_sha, now
from .browser_runtime import risk_check, unique_visible, visible
from .browser_backends import normalize, work_url


async def instagram_caption_editor(page, work_id):
    """Read the owning work's editor, never translated text or comments in body."""
    url=f'https://www.instagram.com/p/{work_id}/'
    await page.goto(url,wait_until='domcontentloaded',timeout=60000)
    more=page.locator('svg[aria-label="More options"]')
    await more.wait_for(state='visible',timeout=45000)
    current=urlsplit(page.url)
    if current.hostname not in {'www.instagram.com','instagram.com'} or current.path.rstrip('/') not in {f'/p/{work_id}',f'/reel/{work_id}'}:
        raise VpubError('wrong_work','Instagram redirected away from the requested work; no edit attempted.')
    await risk_check(page)
    await (await unique_visible(more,'work More options')).click()
    edit = page.get_by_role('button',name='Edit',exact=True)
    await edit.wait_for(state='visible',timeout=15000)
    await (await unique_visible(edit,'work Edit')).click()
    editor=page.get_by_role('textbox',name='Add a caption...',exact=True)
    await editor.wait_for(state='visible',timeout=45000)
    return await unique_visible(editor,'work caption editor')


async def execute(page, platform, identity, payload):
    patch=payload['patch'];receipt=payload['receipt'];work_id=receipt['id']
    output=Path(payload['edit_receipt_path'])
    def save(**fields):
        result={'id':work_id,'platform':platform,'identity':identity,'at':now(),**fields}
        atomic_json(output,result);return result
    async def submit(button):
        await risk_check(page)
        save(state='EDIT_SUBMITTING')
        await button.click(timeout=20000)
    if platform=='instagram':
        if not re.fullmatch(r'[A-Za-z0-9_-]+',work_id): raise VpubError('invalid_id','Invalid Instagram work ID.')
        url=f'https://www.instagram.com/p/{work_id}/'
        editor=await instagram_caption_editor(page,work_id)
        before=await editor.inner_text()
        evidence={'url':url,'verification_source':'work_caption_editor','cover_modified':False}
        if normalize(before)==normalize(patch['post_text']):
            return save(state='EDIT_VERIFIED',unchanged=True,caption=before,platform_writes=0,**evidence)
        if payload.get('verify_only'):
            return save(state='EDIT_MISMATCH',caption=before,platform_writes=0,
                        reason='The saved caption differs; no write attempted.',**evidence)
        await editor.fill(patch['post_text'])
        if normalize(await editor.inner_text())!=normalize(patch['post_text']): raise VpubError('metadata_mismatch','Instagram editor did not retain the intended text.')
        save(state='EDIT_PREPARED',before=before)
        await submit(await unique_visible(page.get_by_role('button',name='Done',exact=True),'save caption'))
        await editor.wait_for(state='hidden',timeout=45000)
        saved_editor=await instagram_caption_editor(page,work_id)
        after=await saved_editor.inner_text()
        matched=normalize(after)==normalize(patch['post_text'])
        return save(state='EDIT_VERIFIED' if matched else 'EDIT_SUBMITTED_UNVERIFIED',
                    before=before,caption=after,platform_writes=1,**evidence)
    if platform=='x':
        url=work_url('x',identity,work_id)
        await page.goto(url,wait_until='domcontentloaded',timeout=60000)
        post=page.locator('article').filter(has=page.locator(f'a[href="/{identity}/status/{work_id}"]'))
        await post.first.wait_for(state='visible',timeout=60000)
        if payload.get('verify_only'):
            matched=normalize(patch['post_text']) in normalize(await post.first.inner_text())
            return save(state='EDIT_VERIFIED' if matched else 'EDIT_SUBMITTED_UNVERIFIED',url=url,platform_writes=0)
        await (await unique_visible(post.get_by_role('button',name='More',exact=True),'post More')).click()
        edit=await visible(page.get_by_role('menuitem',name=re.compile(r'^Edit( post)?$')))
        if edit is None:
            return save(state='EDIT_UNAVAILABLE',url=url,reason='The original post menu has no Edit action; no deletion/repost/reply was attempted.')
        await edit.click()
        editor=page.get_by_role('textbox',name='Post text',exact=True)
        await editor.fill(patch['post_text'])
        update=page.get_by_role('button',name='Update',exact=True)
        if not await update.is_enabled(): raise VpubError('edit_not_ready','X cannot save this text; inspect its account/length constraints.')
        await submit(update)
        await editor.wait_for(state='hidden',timeout=30000)
        await page.reload(wait_until='domcontentloaded')
        await page.get_by_text(patch['title'],exact=False).first.wait_for(timeout=45000)
        ok=normalize(patch['post_text']) in normalize(await page.locator('body').inner_text())
        return save(state='EDIT_VERIFIED' if ok else 'EDIT_SUBMITTED_UNVERIFIED',url=url)
    if platform=='douyin':
        if not str(work_id).isdigit(): raise VpubError('invalid_id','Invalid Douyin ID.')
        if 'portrait' in patch:
            url=f'https://creator.douyin.com/creator-micro/content/post/video?mid={work_id}&enter_from=edit_item'
            await page.goto(url,wait_until='domcontentloaded',timeout=60000)
            title=page.locator('input[placeholder*="填写作品标题"]')
            await title.wait_for(timeout=45000)
            if await title.input_value()!=receipt['title']: raise VpubError('wrong_work','Existing title differs from the receipt; inspect before applying a cover.')
            control=page.locator('[class^="coverControl-"]').filter(has=page.get_by_text('竖封面3:4',exact=True))
            if payload.get('verify_only'):
                return await capture_cover(page,control,output,patch,save,url)
            await control.locator('[class^="cover-"]:not([class^="cover-tip"])').click()
            await page.locator('[role=dialog]').first.wait_for(state='visible',timeout=20000)
            dialog=await unique_visible(page.locator('[role=dialog]'),'cover editor')
            area=await unique_visible(dialog.locator('.semi-upload').filter(has_text='点击上传文件或拖拽文件到这里'),'cover file picker')
            if file_sha(patch['portrait'])!=patch['portrait_sha256']: raise VpubError('source_changed','Shared portrait changed before upload.')
            await area.locator('input.semi-upload-hidden-input').set_input_files(patch['portrait'])
            await page.wait_for_timeout(1500)
            await dialog.get_by_role('button',name='完成',exact=True).click()
            await dialog.wait_for(state='hidden',timeout=30000)
            save_button=await visible(page.get_by_role('button',name=re.compile(r'^(保存|保存修改|提交修改|发布)$')))
            if save_button is None: raise VpubError('layout_changed','Existing-work save button is unavailable; no video was uploaded.')
            await submit(save_button)
            await page.wait_for_url(re.compile('/content/manage'),timeout=60000)
            await page.goto(url,wait_until='domcontentloaded')
            await title.wait_for(timeout=30000)
            return await capture_cover(page,control,output,patch,save,url)
        if 'chapters' in patch:
            return await douyin_chapters(page,receipt,patch,save,submit,verify_only=payload.get('verify_only',False))
        raise VpubError('unsupported_edit','No supported Douyin correction fields.')
    raise VpubError('unsupported_edit','Unsupported platform.')


async def capture_cover(page,control,output,patch,save,url):
    from .browser_backends import wait_until
    async def loaded():
        return await control.locator('img,div').evaluate_all('es=>es.filter(e=>(e.tagName==="IMG"&&e.complete&&e.naturalWidth>0)||getComputedStyle(e).backgroundImage.startsWith("url(")).map(e=>({tag:e.tagName,width:e.naturalWidth||e.clientWidth,height:e.naturalHeight||e.clientHeight}))')
    try: preview=await wait_until(page,loaded,seconds=30,label='saved portrait preview')
    except VpubError as exc:
        if exc.code!='processing_timeout': raise
        preview=[]
    proof=output.with_suffix('.png')
    try: await page.screenshot(path=str(proof),timeout=5000)
    except Exception: proof=None
    return save(state='EDIT_SUBMITTED_UNVERIFIED',url=url,readback_ready=bool(preview),
                portrait_sha256=patch['portrait_sha256'],saved_preview=preview,screenshot=str(proof) if proof else None,
                note='Inspect the saved preview visually. A loaded thumbnail or local source hash does not establish image equality. Never repeat Save merely because verification is pending.')


async def douyin_chapters(page, receipt, patch, save, submit, verify_only=False):
    from .browser_backends import MANAGE, works
    chapters=patch['chapters']
    found=await works(page,'douyin',receipt.get('identity',''))
    if normalize(receipt['title']) not in normalize(found.get(str(receipt['id']),{}).get('text','')):
        raise VpubError('wrong_work','The intended chapter work ID and title could not be read from the account management page.')
    await page.goto(MANAGE,wait_until='domcontentloaded',timeout=60000)
    card=page.locator('[class^="video-card-content-"]').filter(has_text=receipt['title'])
    await card.wait_for(state='visible',timeout=30000)
    await card.get_by_text('编辑作品',exact=True).hover()
    await page.get_by_text(re.compile('^(添加章节|编辑章节)$')).first.wait_for(state='visible',timeout=15000)
    menu=await unique_visible(page.get_by_text(re.compile('^(添加章节|编辑章节)$')),'chapter menu')
    await menu.click()
    await page.get_by_role('button',name='手动添加',exact=True).wait_for(timeout=30000)
    if verify_only:
        text=await page.locator('body').inner_text()
        toggle=await unique_visible(page.locator('input[type=checkbox]'),'external chapter progress bar')
        matched=chapter_text_matches(text,chapters) and await toggle.is_checked()
        return save(state='EDIT_VERIFIED' if matched else 'EDIT_SUBMITTED_UNVERIFIED',chapters=chapters,
                    timing_readback=matched,platform_writes=0)
    if await visible(page.get_by_text('清空',exact=True)):
        # Clear only the local editor; the original work is unchanged until Save.
        await page.get_by_text('清空',exact=True).click()
        await page.get_by_role('button',name='confirm',exact=True).click()
    for chapter in chapters:
        player=await unique_visible(page.locator('video'),'chapter video preview')
        await player.evaluate('(v,t)=>{v.pause();v.currentTime=t;}',chapter['start_seconds'])
        await page.wait_for_timeout(400)
        await page.get_by_role('button',name='手动添加',exact=True).click()
        name=page.get_by_placeholder('输入章节名',exact=True)
        await name.fill(chapter['title'])
        confirm=await unique_visible(page.get_by_text('确定',exact=True),'local chapter confirmation')
        await confirm.click()
        await name.wait_for(state='hidden',timeout=10000)
    toggle=await unique_visible(page.locator('input[type=checkbox]'),'external chapter progress bar')
    await toggle.check()
    text=await page.locator('body').inner_text()
    if not chapter_text_matches(text,chapters): raise VpubError('chapters_mismatch','Chapter titles or their paired times do not match before saving.')
    save(state='EDIT_PREPARED',chapters=chapters)
    await submit(page.get_by_role('button',name='保存',exact=True))
    await page.get_by_role('button',name='保存',exact=True).wait_for(state='hidden',timeout=45000)
    await page.reload(wait_until='domcontentloaded')
    await card.wait_for(state='visible',timeout=30000)
    await card.get_by_text('编辑作品',exact=True).hover()
    await page.get_by_text(re.compile('^(添加章节|编辑章节)$')).first.wait_for(state='visible',timeout=15000)
    await (await unique_visible(page.get_by_text(re.compile('^(添加章节|编辑章节)$')),'chapter menu')).click()
    await page.get_by_role('button',name='手动添加',exact=True).wait_for(timeout=30000)
    text=await page.locator('body').inner_text()
    matched=chapter_text_matches(text,chapters) and await toggle.is_checked()
    return save(state='EDIT_VERIFIED' if matched else 'EDIT_SUBMITTED_UNVERIFIED',chapters=chapters,
                timing_readback=matched,review_pending='审核' in text[-1500:],
                note='Saved chapter controls were reopened. Mobile presentation is a separate check.')


def chapter_text_matches(text,chapters):
    expected=[f"{int(c['start_seconds'])//60:02d}:{int(c['start_seconds'])%60:02d}" for c in chapters]
    return (f'章节数：{len(chapters)}' in text and
            all(re.search(re.escape(t)+r'\s+'+re.escape(c['title'])+r'(?:\s|$)',text)
                for c,t in zip(chapters,expected)))
