"""Official-page edits using the existing CLI-owned session, not the host browser."""
from pathlib import Path
import re
from urllib.parse import urlsplit
from . import x_pages
from .core import VpubError, atomic_json, file_sha, now, stop_check
from .browser_runtime import failure_record, look_until, risk_check, unique_visible, visible
from .browser_backends import choose_file, douyin_card_words, douyin_editor_address, douyin_editor_boxes, douyin_type_caption, normalize, work_url
from .progress import stage

READ_BACKS = 4
READ_BACK_WAIT = 10000


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


async def x_post_says(post,words):
    """This post's own words, so the same words elsewhere on the page prove nothing."""
    fields=await x_pages.post_fields(post.first)
    return bool(fields) and normalize(fields['text'])==normalize(words)


async def execute(page, platform, identity, payload):
    patch=payload['patch'];receipt=payload['receipt'];work_id=receipt['id']
    output=Path(payload['edit_receipt_path'])
    def save(**fields):
        result={'id':work_id,'platform':platform,'identity':identity,'at':now(),**fields}
        # A check only reads. What it means for a save already tried is for edits.send to record, so a check cut off here leaves that save on record.
        if not payload.get('verify_only'): atomic_json(output,result)
        return result
    async def submit(button,still_holds=None):
        await risk_check(page)
        stop_check(platform)
        if still_holds: await still_holds()  # What was read before that wait may not be true after it.
        save(state='EDIT_SUBMITTING')
        stage('pressing')
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
        # Instagram can show the old words for a while after a save, so the read-back looks a few times. It never saves again.
        for attempt in range(READ_BACKS):
            if attempt: await page.wait_for_timeout(READ_BACK_WAIT)
            saved_editor=await instagram_caption_editor(page,work_id)
            after=await saved_editor.inner_text()
            matched=normalize(after)==normalize(patch['post_text'])
            if matched: break
        return save(state='EDIT_VERIFIED' if matched else 'EDIT_SUBMITTED_UNVERIFIED',
                    before=before,caption=after,platform_writes=1,**evidence)
    if platform=='x':
        url=work_url('x',identity,work_id)
        await page.goto(url,wait_until='domcontentloaded',timeout=60000)
        post=x_pages.post_row(page,work_id)
        await post.first.wait_for(state='visible',timeout=60000)
        if await x_post_says(post,patch['post_text']):
            return save(state='EDIT_VERIFIED',unchanged=True,url=url,platform_writes=0)
        if payload.get('verify_only'):
            return save(state='EDIT_SUBMITTED_UNVERIFIED',url=url,platform_writes=0)
        await x_pages.open_menu(page,await unique_visible(post.locator('button[aria-label="More"], '+x_pages.MORE),'post More'))
        edit=await visible(page.get_by_role('menuitem',name=x_pages.EDIT_POST))
        if edit is None:
            await page.keyboard.press('Escape')
            return save(state='EDIT_UNAVAILABLE',url=url,reason='The original post menu has no Edit action; no deletion/repost/reply was attempted.')
        await edit.click()
        from .operations_browser import fill_x_text
        windows=page.locator(x_pages.COMPOSER)
        await windows.first.wait_for(state='visible',timeout=15000)
        window=await unique_visible(windows,'post edit window')
        editor=await unique_visible(window.locator(x_pages.TEXT_BOX),'post text')
        await fill_x_text(page,editor,patch['post_text'])
        update=await unique_visible(window.get_by_role('button',name=x_pages.UPDATE_POST),'post Update')
        if not await update.is_enabled(): raise VpubError('edit_not_ready','X cannot save this text; inspect its account/length constraints.')
        await submit(update)
        await editor.wait_for(state='hidden',timeout=30000)
        # A page can show the old words for a while after a save, so the read-back looks a few times. It never saves again.
        for attempt in range(READ_BACKS):
            if attempt: await page.wait_for_timeout(READ_BACK_WAIT)
            await page.reload(wait_until='domcontentloaded')
            await post.first.wait_for(state='visible',timeout=45000)
            ok=await x_post_says(post,patch['post_text'])
            if ok: break
        return save(state='EDIT_VERIFIED' if ok else 'EDIT_SUBMITTED_UNVERIFIED',url=url,platform_writes=1)
    if platform=='douyin':
        if not str(work_id).isdigit(): raise VpubError('invalid_id','Invalid Douyin ID.')
        if 'portrait' in patch:
            url=douyin_editor_address(work_id)
            # A field run found the title box showing empty for a moment, so this waits for its title as the text route does.
            title,_=await douyin_fields(page,url,work_id)
            if receipt.get('title') and await title.input_value()!=receipt['title']: raise VpubError('wrong_work','Existing title differs from the receipt; inspect before applying a cover.')
            control=page.locator('[class^="coverControl-"]').filter(has=page.get_by_text('竖封面3:4',exact=True))
            if payload.get('verify_only'):
                return await capture_cover(page,control,output,patch,save,url)
            if await douyin_says_held(page): raise douyin_held()
            await control.locator('[class^="cover-"]:not([class^="cover-tip"])').click()
            await page.locator('[role=dialog]').first.wait_for(state='visible',timeout=20000)
            dialog=await unique_visible(page.locator('[role=dialog]'),'cover editor')
            area=await unique_visible(dialog.locator('.semi-upload').filter(has_text='点击上传文件或拖拽文件到这里'),'cover file picker')
            if file_sha(patch['portrait'])!=patch['portrait_sha256']: raise VpubError('source_changed','Shared portrait changed before upload.')
            await choose_file(page,area.locator('input.semi-upload-hidden-input'),patch['portrait'])
            fonts=await douyin_cover_settles(page)
            # That wait can be long, and a review found 完成 pressed with a stop in place.
            await risk_check(page)
            stop_check(platform)
            await dialog.get_by_role('button',name='完成',exact=True).click()
            from patchright.async_api import TimeoutError as BrowserTimeout
            try:
                await dialog.wait_for(state='hidden',timeout=DOUYIN_COVER_SECONDS*1000)
            except BrowserTimeout:
                # A field run saw the window greyed out and working for over three minutes after 完成. Why isn't known, and nothing has been saved at this point.
                raise VpubError('cover_not_ready',f"Douyin's cover window was still working {DOUYIN_COVER_SECONDS} seconds after 完成, so the work was not saved. Its fonts were {fonts} at the press and are {await douyin_fonts(page)} now.",'Nothing changed. Try again later, or change the cover on Douyin\'s own page.') from None
            save_button=await visible(page.get_by_role('button',name=re.compile(r'^(保存|保存修改|提交修改|发布)$')))
            if save_button is None: raise VpubError('layout_changed','Existing-work save button is unavailable; no video was uploaded.')
            await douyin_can_press(page,save_button)
            pressed=normalize(await save_button.inner_text())
            await submit(save_button,still_holds=lambda: douyin_can_press(page,save_button))
            stayed=await douyin_moved_on(page,pressed,output)
            if stayed: return save(state='EDIT_SUBMITTED_UNVERIFIED',platform_writes=1,url=url,after_press=stayed,note='Pressed once, and the page did not go back to the works list. See after_press; do not press again.')
            await douyin_fields(page,url,work_id)
            return await capture_cover(page,control,output,patch,save,url)
        if 'chapters' in patch:
            return await douyin_chapters(page,receipt,patch,save,submit,verify_only=payload.get('verify_only',False))
        if 'caption' in patch:
            return await douyin_text(page,work_id,patch,save,submit,output,payload.get('verify_only',False))
        raise VpubError('unsupported_edit','No supported Douyin correction fields.')
    if platform=='youtube':
        from .youtube_studio import edit
        return await edit(page,payload,save,submit)
    raise VpubError('unsupported_edit','Unsupported platform.')


DOUYIN_SAVE_SECONDS=60
DOUYIN_COVER_SECONDS=180
DOUYIN_PICTURE_SECONDS=45
DOUYIN_FONT_SECONDS=60
DOUYIN_COVER_SETTLE_SECONDS=75


async def douyin_cover_settles(page):
    """In field runs 完成 hung when pressed about 11 seconds after the file and went through at about 73, on a page whose fonts take about 25. Why isn't known, and if it isn't this, the wait costs little. Says how the fonts stood."""
    await page.wait_for_timeout(DOUYIN_COVER_SETTLE_SECONDS*1000)
    await look_until(page,lambda: douyin_fonts_loaded(page),DOUYIN_FONT_SECONDS*1000,step=1000)
    return await douyin_fonts(page)


async def douyin_fonts(page):
    """How far this page has got with its fonts, which a stop reports too. It holds no words from the page."""
    try:
        return await page.evaluate('document.fonts.status')
    except Exception:
        return 'unknown'


async def douyin_fonts_loaded(page):
    return await douyin_fonts(page)=='loaded'


async def douyin_says_held(page):
    """Whether a work's edit page says it can't be changed for now. A field run saw 作品修改审核中，暂不支持修改 in its own place above the final button. Only these fixed words count, because a person's text can say anything."""
    for tip in await page.locator('[class^="modify-tip-"]').all():
        if await tip.is_visible() and '暂不支持修改' in normalize(await tip.inner_text()):
            return True
    return False


def douyin_held(what_happened='nothing was typed or pressed'):
    return VpubError('edit_held_by_review',f'Douyin is not allowing changes to this work while a review runs, so {what_happened}.','Try the same edit again once the review has finished.')


async def douyin_greyed(button):
    """That same run's final button was grey only through a class. It reported itself enabled and could be clicked, and the click did nothing."""
    return bool(re.search(r'(^|\s)disabled-',await button.get_attribute('class') or ''))


async def not_greyed(button):
    return not await douyin_greyed(button)


async def douyin_can_press(page,button):
    """Raises unless this button can do something now. A field read found it grey on a work with nothing held and no change yet, so it gets a moment to turn once the new words are in."""
    if await douyin_says_held(page): raise douyin_held('nothing was saved')
    if await douyin_greyed(button) and not await look_until(page,lambda: not_greyed(button),5000):
        raise VpubError('edit_not_ready','Douyin kept its save button grey, so it was not pressed and nothing was saved.')


async def douyin_moved_on(page,pressed,output):
    """None once a press has taken the page back to the works list. Otherwise what can be said about it, because a press is never repeated.
    A field run pressed save and the page stayed, with the old words still saved and nothing to say why, so the usual failure record is kept. A review showed a page's dialogs can quote anyone's words, and that record keeps a page's words only with page_diagnostics on."""
    from patchright.async_api import Error as BrowserError
    try:
        await page.wait_for_url(re.compile('/content/manage'),timeout=DOUYIN_SAVE_SECONDS*1000)
    except BrowserError:
        record=output.with_suffix('.error.json')
        try:
            await failure_record(page,VpubError('edit_stayed','The page did not go back to the works list after the press.'),record,picture=output.with_suffix('.error.png'))
        except Exception:
            pass  # A page that can't be read must not replace what happened.
        return {'pressed':pressed,'still_on_edit_page':'/content/post/video' in urlsplit(page.url).path,'says_held':await douyin_says_held(page),'record':record.name}
    return None


async def douyin_fields(page,url,work_id):
    await page.goto(url,wait_until='domcontentloaded',timeout=60000)
    return await douyin_editor_boxes(page,work_id)


async def douyin_text(page,work_id,patch,save,submit,output,verify_only=False):
    """Title and description on the work's own edit page, the one its cover uses. Saved once, then read back from a fresh load."""
    url=douyin_editor_address(work_id)
    wanted=normalize(patch['title']),douyin_card_words(patch['caption'])
    async def current(title,editor): return normalize(await title.input_value()),douyin_card_words(await editor.inner_text())
    title,editor=await douyin_fields(page,url,work_id)
    if await current(title,editor)==wanted:
        return save(state='EDIT_VERIFIED',unchanged=True,platform_writes=0,url=url)
    if verify_only:
        return save(state='EDIT_MISMATCH',platform_writes=0,url=url,reason='The saved text differs; no write attempted.')
    if await douyin_says_held(page): raise douyin_held()
    await title.fill(patch['title'])
    await douyin_type_caption(page,editor,patch['caption'])
    if await current(title,editor)!=wanted:
        raise VpubError('metadata_mismatch','Douyin\'s form did not keep the intended text; nothing was saved.')
    button=await visible(page.get_by_role('button',name=re.compile(r'^(保存|保存修改|提交修改|发布)$')))
    if button is None: raise VpubError('layout_changed','Existing-work save button is unavailable; nothing was saved.')
    await douyin_can_press(page,button)
    pressed=normalize(await button.inner_text())
    await submit(button,still_holds=lambda: douyin_can_press(page,button))
    stayed=await douyin_moved_on(page,pressed,output)
    if stayed: return save(state='EDIT_SUBMITTED_UNVERIFIED',platform_writes=1,url=url,after_press=stayed,note='Pressed once, and the page did not go back to the works list. See after_press; do not press again.')
    title,editor=await douyin_fields(page,url,work_id)
    matched=await current(title,editor)==wanted
    return save(state='EDIT_VERIFIED' if matched else 'EDIT_SUBMITTED_UNVERIFIED',platform_writes=1,url=url,note='Douyin may review the change, and the old text may show until that finishes.')


async def capture_cover(page,control,output,patch,save,url):
    from .browser_backends import wait_until
    async def loaded():
        return await control.locator('img,div').evaluate_all('es=>es.filter(e=>(e.tagName==="IMG"&&e.complete&&e.naturalWidth>0)||getComputedStyle(e).backgroundImage.startsWith("url(")).map(e=>({tag:e.tagName,width:e.naturalWidth||e.clientWidth,height:e.naturalHeight||e.clientHeight}))')
    try: preview=await wait_until(page,loaded,seconds=30,label='saved portrait preview')
    except VpubError as exc:
        if exc.code!='processing_timeout': raise
        preview=[]
    proof=output.with_suffix('.png')
    # A picture waits for the page's fonts, and a field read found them taking about 25 seconds on this fresh load. Only the cover's own element, because a review found a picture of the whole editor kept its words with diagnostics off.
    try: await (await control.element_handle(timeout=5000)).screenshot(path=str(proof),timeout=DOUYIN_PICTURE_SECONDS*1000)
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
