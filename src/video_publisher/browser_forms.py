"""Official upload forms driven by the CLI's own persistent browser."""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

from .categories import STUDIO_CATEGORIES
from .core import VpubError, douyin_caption, stop_check
from .browser_runtime import risk_check, unique_visible, visible
from .progress import stage
from .browser_backends import (STUDIO_DATES, STUDIO_TIMES, normalize, studio_offsets, studio_read, studio_style,
                               upload_file, wait_until, work_url, works, verify, verify_scheduled_x)

X_CLOCK = re.compile(r'\d{1,2}:\d{2}\s*[AP]M', re.I)
YT_DIALOG = 'ytcp-uploads-dialog'
YT_PLAYLISTS = 'ytcp-playlist-dialog'
YT_TICK_MS = 5000
# Each playlist row's checkbox, its own lines of text and whether it is ticked.
YT_PLAYLIST_ROWS = """boxes => boxes.map(box => {
    const row = box.closest('li, label, ytcp-ve') || box.parentElement;
    const lines = (row ? row.innerText : '').split('\\n').map(s => s.replace(/\\s+/g, ' ').trim()).filter(Boolean);
    return {lines, checked: box.getAttribute('aria-checked') === 'true'};
})"""


async def enabled(locator):
    return await locator.is_visible() and await locator.is_enabled()


async def finish(page, tx, before):
    """Read own account after one submit; never click Post/Share again."""
    await page.wait_for_timeout(5000)
    for attempt in range(8):
        found = await works(page, tx.platform, tx.identity)
        if tx.platform == 'douyin':
            cards = page.locator('[class^="video-card-content-"]').filter(has_text=tx.options['title'])
            if await cards.count() == 1 and '审核中' in await cards.inner_text():
                return tx.save(state='SUBMITTED_UNVERIFIED', phase='platform_review', platform_status='UNDER_REVIEW',
                               error=None, review_url=page.url, note='Matching submitted work is under review; verify later, never reupload.')
        fresh = [v for k, v in found.items() if k not in before]
        matches = [v for v in fresh if tx.options['title'] in v.get('text', '')]
        candidates = matches
        if len(candidates) == 1:
            item = candidates[0]
            receipt = tx.save(state='SUBMITTED_UNVERIFIED', phase='readback', id=item['id'], url=item['url'])
            result = await verify(page, tx.platform, tx.identity, receipt)
            if result['state'] == 'SUBMISSION_VERIFIED':
                return tx.save(**{**result, 'phase':'complete', 'error':None})
        await asyncio.sleep(5)
    return tx.save(state='SUBMITTED_UNVERIFIED', phase='readback_pending',
                   hint='The submit was clicked once. Verify this existing attempt; do not reupload.')


async def x_schedule(page, when):
    """Fill Studio's own calendar and clock and read them back. Nothing is saved here. Studio uses this computer's time zone."""
    local = when.astimezone()
    unconfirmed = 'The X schedule was not confirmed. Nothing was posted.'
    opener = await unique_visible(page.locator('button[type="button"]').filter(has_text=re.compile(r'^\s*Schedule\s*$')), 'X Schedule')
    await opener.click(timeout=15000)
    month = page.locator('fieldset.CalendarNavigation-monthFormSelect select')
    year = page.locator('fieldset.CalendarNavigation-yearFormSelect select')
    try:
        await month.wait_for(state='visible', timeout=15000)
        await year.select_option(label=str(local.year), timeout=5000)
        await month.select_option(value=str(local.month - 1), timeout=5000)
    except Exception:
        raise VpubError('schedule_unverified', 'X Studio did not offer this month. Nothing was posted.') from None
    await page.wait_for_timeout(500)
    # Days of the next month sit in the same grid with the same numbers, so only this month's count.
    days = page.locator('.Calendar-day.is-selectable:not(.is-adjacentMonth)').filter(has=page.locator('.Calendar-dayText', has_text=re.compile(rf'^\s*{local.day}\s*$')))
    if await days.count() != 1:
        raise VpubError('schedule_unverified', 'X Studio did not offer this day. Nothing was posted.')
    await days.first.click(timeout=15000)
    # The clock has no label of its own; it is the one box showing a time.
    clocks = [box for box in await page.locator('input[type="text"]').all() if await box.is_visible() and X_CLOCK.fullmatch((await box.input_value()).strip())]
    if len(clocks) != 1:
        raise VpubError('schedule_unverified', 'X Studio did not show one time to set. Nothing was posted.')
    stamp = f"{local.hour % 12 or 12}:{local.minute:02d} {'AM' if local.hour < 12 else 'PM'}"
    await clocks[0].fill(stamp)
    await clocks[0].press('Tab')
    await page.wait_for_timeout(500)
    chosen = page.locator('.Calendar-day.is-rangeStart:not(.is-adjacentMonth) .Calendar-dayText')
    if (await year.evaluate('e => e.options[e.selectedIndex].text') != str(local.year) or await month.input_value() != str(local.month - 1)
            or await chosen.count() != 1 or (await chosen.inner_text()).strip() != str(local.day) or (await clocks[0].input_value()).strip() != stamp):
        raise VpubError('schedule_unverified', unconfirmed)
    return {'at': when.isoformat(), 'local': local.isoformat(), 'date': [local.year, local.month, local.day], 'time': [local.hour, local.minute], 'timezone_basis': 'computer'}


async def x_send_scheduled(page, tx):
    """With a time chosen the composer's final button becomes Save. One click, then Studio's schedule page says whether it is there."""
    when = datetime.fromisoformat(tx.options['schedule'].replace('Z', '+00:00'))
    tx.save(schedule=await x_schedule(page, when))
    save = await unique_visible(page.locator('button[type="button"]').filter(has_text=re.compile(r'^\s*Save\s*$')), 'X schedule Save')
    await wait_until(page, lambda: enabled(save), seconds=30, label='X schedule Save enabled')
    await tx.submit(page, save)
    await page.wait_for_timeout(3000)
    return tx.save(**await verify_scheduled_x(page, tx.identity, tx.receipt))


async def publish_x(page, tx):
    before = tx.receipt.get('before_ids')
    if before is None:
        before = await works(page, 'x', tx.identity)
    tx.save(before_ids=list(before), expected_text=tx.options['post_text'])
    await page.goto('https://studio.x.com/library', wait_until='domcontentloaded', timeout=60000)
    try:
        await page.locator('button').filter(has_text=re.compile(r'^\s*Upload Media\s*$')).wait_for(timeout=30000)
    except Exception:
        raise VpubError('studio_access_required', 'CrossCast X publishing supports subscribed accounts with actual Media Studio access only. No video was uploaded.', 'Confirm the connected account can open Media Studio and upload media. Payment or login alone does not prove access; free-account publishing is not supported by this backend.')
    # A managed Studio account can differ from the x.com navigation account.
    account_link = page.locator('a[href*="analytics.twitter.com/user/"]')
    hrefs = await account_link.evaluate_all('es => es.map(e=>e.href)')
    if not any('/user/'+tx.identity.casefold()+'/' in h.casefold() for h in hrefs):
        raise VpubError('wrong_account', 'Media Studio account does not match the signed-in publishing account.')
    if tx.receipt.get('media_url'):
        if not re.fullmatch(r'https://studio\.x\.com/library/\d+_\d+/edit', tx.receipt['media_url']):
            raise VpubError('invalid_media_url', 'Resume requires the original Media Studio asset URL.')
        await page.goto(tx.receipt['media_url'], wait_until='domcontentloaded')
    else:
        if not tx.receipt['media_selected']:
            media = tx.media()
            await upload_file(page, media, accept='video/')
            tx.save(phase='uploading')
        item = page.get_by_text(tx.receipt['upload_filename'], exact=True)
        await item.wait_for(state='visible', timeout=600000)
        await item.click()
        await page.wait_for_url(re.compile(r'https://studio\.x\.com/library/\d+_\d+/edit'), timeout=30000)
        tx.save(phase='opening_media', media_url=page.url)
    change = page.locator('button').filter(has_text=re.compile(r'^\s*Change thumbnail\s*$'))
    await change.wait_for(state='visible', timeout=300000)
    tx.save(phase='media_ready', media_url=page.url)
    if tx.cover() and not tx.receipt.get('cover'):
        await change.click()
        # Distinguish the image-only thumbnail input from the mixed media input.
        inp = page.locator('input[type=file][accept="image/jpeg,image/jpg,image/png"]')
        if await inp.count() != 1:
            raise VpubError('layout_changed', 'Media Studio thumbnail input is ambiguous.')
        await inp.set_input_files(tx.cover())
        choose = page.locator('button').filter(has_text=re.compile(r'^\s*Choose\s*$'))
        # Uploaded images are saved immediately; Choose is for a video frame.
        await choose.wait_for(state='hidden', timeout=30000)
        await page.wait_for_timeout(2000)
        tx.save(phase='cover_selected', cover={'source':'production_artwork', 'state':'UI_ACCEPTED', 'remote_pixels_verified':False})
    media_title = page.get_by_placeholder('Title', exact=True)
    if await visible(media_title):
        if len(tx.options['title']) > 70:
            raise VpubError('title_too_long', 'X Studio title must fit 70 characters; do not silently truncate.')
        await media_title.fill(tx.options['title'])
        await media_title.press('Tab')
        description = page.get_by_placeholder('Type a brief description here', exact=True)
        if len(tx.options['description']) <= 200:
            await description.fill(tx.options['description'])
            await description.press('Tab')
        await page.wait_for_timeout(1500)
    # The non-icon detail Post button opens the composer; it does not publish.
    compose = await unique_visible(page.locator('button[type="button"]').filter(has_text=re.compile('^Post$')), 'Studio compose button')
    await compose.click()
    caption = page.get_by_placeholder("What's happening?", exact=True)
    await caption.wait_for(timeout=20000)
    await caption.fill(tx.options['post_text'])
    if await caption.input_value() != tx.options['post_text']:
        raise VpubError('metadata_mismatch', 'X post text differs from the manifest.')
    tx.save(phase='preview', review_url=page.url, ai_disclosure='caption_text', metadata_readback=True)
    if tx.options.get('submission_mode') == 'draft_only':
        return tx.save(state='PREVIEW_READY', note='Media saved in Studio; composer preview is not a confirmed persistent post draft.')
    if tx.options.get('schedule'):
        return await x_send_scheduled(page, tx)
    post = await unique_visible(page.locator('button[type="button"]').filter(has_text=re.compile('^Post$')), 'final Post')
    await wait_until(page, lambda: enabled(post), seconds=90, label='X final Post enabled')
    await tx.submit(page, post)
    return await finish(page, tx, before)


async def publish_instagram(page, tx):
    before = await works(page, 'instagram', tx.identity)
    caption = tx.options['post_text']
    if len(caption) > 2200:
        raise VpubError('description_too_long', 'Instagram caption exceeds 2200 characters; no upload started.')
    tx.save(before_ids=list(before), expected_text=caption)
    # Sidebar New post; account remains the one verified before navigation.
    await page.locator('svg[aria-label="New post"]').click()
    await page.get_by_role('button', name='Select from computer', exact=True).wait_for(timeout=20000)
    await upload_file(page, tx.media(), accept='video/')
    tx.save(phase='uploading')
    async def crop_ready():
        notice = await visible(page.get_by_text('Video posts are now shared as reels', exact=True))
        if notice:
            await page.get_by_role('button', name='OK', exact=True).click()
        return await visible(page.get_by_role('button', name='Next', exact=True))
    await wait_until(page, crop_ready, seconds=600, label='Instagram crop step')
    tx.save(phase='crop')
    crop = page.locator('svg[aria-label="Select crop"]')
    await crop.click()
    await page.get_by_text('Original', exact=True).click()
    tx.save(crop='original', video_modified=False)
    await page.get_by_role('button', name='Next', exact=True).click()
    tx.save(phase='cover')
    if tx.cover():
        cover_button = page.get_by_text('Select from computer', exact=True)
        await cover_button.wait_for(state='visible', timeout=60000)
        async with page.expect_file_chooser(timeout=15000) as chosen:
            await cover_button.click()
        await (await chosen.value).set_files(tx.cover())
        await page.wait_for_timeout(2000)
        tx.save(cover={'state':'UI_ACCEPTED', 'source':'production_artwork', 'remote_pixels_verified':False})
    await page.get_by_role('button', name='Next', exact=True).click()
    tx.save(phase='caption')
    editor = await unique_visible(page.locator('textarea,[contenteditable=true]'), 'Instagram caption')
    await editor.fill(caption)
    actual = await editor.input_value() if await editor.evaluate('e=>e.tagName')=='TEXTAREA' else await editor.inner_text()
    if normalize(actual) != normalize(caption):
        raise VpubError('metadata_mismatch', 'Instagram caption differs from the manifest.')
    # The native AI control is required when requested; never silently omit it.
    if tx.options['ai_generated']:
        ai = await visible(page.get_by_text('Add AI label', exact=True))
        if ai is None:
            advanced = await visible(page.get_by_text('Advanced settings', exact=True))
            if advanced:
                await advanced.click()
            ai = await visible(page.get_by_text('Add AI label', exact=True))
        if ai is None:
            raise VpubError('declaration_unverified', 'Instagram AI-label control is unavailable; upload retained, Share not clicked.')
        row = ai.locator('..')
        switch = await visible(row.locator('[role=switch],input[type=checkbox]'))
        if switch is None:
            switch = await visible(row.locator('..').locator('[role=switch],input[type=checkbox]'))
        if switch is None:
            raise VpubError('declaration_unverified', 'Instagram AI-label switch could not be identified.')
        checked = await switch.get_attribute('aria-checked')
        if checked != 'true':
            await switch.click()
        if await switch.get_attribute('aria-checked') != 'true' and not await switch.is_checked():
            raise VpubError('declaration_unverified', 'Instagram AI label was not confirmed.')
    tx.save(phase='preview', metadata_readback=True, ai_disclosure='native_control')
    if tx.options.get('submission_mode') == 'draft_only':
        return tx.save(state='PREVIEW_READY', note='Reached unpublished preview; this web composer may not persist after the browser closes.')
    post = await unique_visible(page.get_by_role('button', name='Share', exact=True), 'Instagram final Share')
    await tx.submit(page, post)
    await page.get_by_text(re.compile(r'(Your (post|reel) has been shared|Post shared)', re.I)).wait_for(timeout=600000)
    return await finish(page, tx, before)


async def publish_douyin(page, tx):
    before = await works(page, 'douyin', tx.identity)
    caption = douyin_caption(tx.options['description'], tx.options['tags'])
    if len(caption.encode('utf-16-le')) // 2 > 1000:
        raise VpubError('description_too_long', 'Douyin description plus hashtags exceeds 1000 UTF-16 units. No video uploaded.')
    tx.save(before_ids=list(before), expected_text=tx.options['title'])
    await page.goto('https://creator.douyin.com/creator-micro/content/upload', wait_until='domcontentloaded', timeout=60000)
    await page.locator('input[type=file]').wait_for(state='attached', timeout=20000)
    # Selecting a video here could replace a draft the person kept, and only they can say whether it matters.
    if await douyin_offers_draft(page, 3):
        raise VpubError('existing_draft_requires_attention', "Douyin's upload page is offering a kept draft, and uploading now could replace it. No video was selected.",
                        "Continue or discard that draft on Douyin's upload page yourself, then run this again.")
    tx.save(draft_offered_before=False)
    await upload_file(page, tx.media(), accept='video/')
    tx.save(phase='uploading')
    await page.wait_for_url(re.compile(r'/content/(publish|post/video)'), timeout=120000)
    tx.save(phase='editing', draft_url=page.url)
    title = page.locator('input[placeholder*="填写作品标题"]')
    await title.fill(tx.options['title'])
    editor = await unique_visible(page.locator('[contenteditable=true]'), 'Douyin description')
    await editor.fill(caption)
    await page.get_by_text('重新上传', exact=True).wait_for(state='visible', timeout=900000)
    tx.save(phase='uploaded')
    return await finish_douyin_form(page, tx, before)


async def douyin_schedule(page, when):
    """Fill Creator Center's own date and time box and read it back. Nothing is published here. The box follows this computer's clock."""
    local = when.astimezone()
    stamp = local.strftime('%Y-%m-%d %H:%M')
    # The word sits in a label that takes the click, so the label is what gets pressed.
    choice = await unique_visible(page.locator('label').filter(has_text=re.compile(r'^\s*定时发布\s*$')), 'Douyin schedule choice')
    await choice.click(timeout=15000)
    boxes = page.locator('input[placeholder="日期和时间"]')
    try:
        await boxes.first.wait_for(state='visible', timeout=15000)
    except Exception:
        raise VpubError('schedule_unverified', 'Douyin did not show its date and time box. Nothing was published.') from None
    box = await unique_visible(boxes, 'Douyin date and time')
    await box.click(timeout=10000)
    await box.select_text(timeout=5000)
    await page.keyboard.type(stamp)
    await page.keyboard.press('Enter')
    await page.wait_for_timeout(1000)
    if (await box.input_value()).strip() != stamp:
        raise VpubError('schedule_unverified', 'Douyin did not keep this date and time. Nothing was published.', 'Douyin may limit how soon or how far ahead a post can be scheduled. Choose another time and make a new plan.')
    return {'at': when.isoformat(), 'local': local.isoformat(), 'stamp': stamp, 'date': [local.year, local.month, local.day], 'time': [local.hour, local.minute], 'timezone_basis': 'computer'}


async def douyin_collection(page, title):
    """Choose one of the account's own collections by its exact title and read the choice back. Nothing is published here."""
    trigger = await unique_visible(page.locator('[class*="select-collection-"] .semi-select-selection'), 'Douyin collection list')
    await trigger.click(timeout=15000)
    options = page.locator('.semi-select-option').filter(has=page.locator('[class*="option-title-"]', has_text=re.compile(r'^\s*' + re.escape(title) + r'\s*$')))
    try:
        await options.first.wait_for(state='visible', timeout=15000)
    except Exception:
        raise VpubError('collection_not_found', 'Douyin did not offer a collection with exactly this title. Nothing was published.') from None
    await (await unique_visible(options, 'Douyin collection')).click(timeout=10000)
    await page.wait_for_timeout(800)
    if normalize(await trigger.inner_text()) != normalize(title):
        raise VpubError('collection_unverified', 'Douyin did not keep this collection. Nothing was published.')
    return {'title': title, 'state': 'UI_ACCEPTED'}


async def douyin_offers_draft(page, seconds):
    """Whether the upload page offers a kept draft. Opening one can change what is on offer, so this only looks. It can't say whose draft it is, and an old one can show late."""
    offer = page.get_by_text('继续编辑', exact=True)
    for _ in range(seconds):
        if await visible(offer):
            return True
        await page.wait_for_timeout(1000)
    return bool(await visible(offer))


async def douyin_keep_draft(page, tx):
    """Douyin's own 暂存离开 keeps the form for later and publishes nothing. It shows no list of drafts, but its upload page then offers one."""
    leave = await unique_visible(page.get_by_role('button', name='暂存离开', exact=True), 'Douyin 暂存离开')
    await risk_check(page)
    stop_check('douyin')
    tx.save(phase='saving_draft')
    stage('pressing')
    await leave.click(timeout=15000)
    await page.wait_for_timeout(3000)
    await risk_check(page)
    tx.save(phase='draft_saved')
    await page.goto('https://creator.douyin.com/creator-micro/content/upload', wait_until='domcontentloaded', timeout=60000)
    await risk_check(page)
    seen = await douyin_offers_draft(page, 10) and tx.receipt.get('draft_offered_before') is False
    note = 'Its upload page now offers a draft under 继续编辑 that it did not offer before, which may be this one.' if seen else 'Its upload page offers no draft that is new since this upload began.'
    return tx.save(state='PREVIEW_READY', phase='draft_saved', draft_offer_seen=seen, note='Pressed Douyin\'s 暂存离开 once; nothing was published. This is not read back as this draft. ' + note)


async def finish_douyin_form(page, tx, before):
    """Complete this already-uploaded form; never select the video again."""
    caption = douyin_caption(tx.options['description'], tx.options['tags'])
    title = page.locator('input[placeholder*="填写作品标题"]')
    editor = await unique_visible(page.locator('[contenteditable=true]'), 'Douyin description')
    for role, label in [('landscape', '横封面4:3'), ('portrait', '竖封面3:4')]:
        path = tx.cover(role)
        if not path or tx.receipt.get(role+'_cover'):
            continue
        control = page.locator('[class^="coverControl-"]').filter(has=page.get_by_text(label, exact=True))
        await control.locator('[class^="cover-"]:not([class^="cover-tip"])').click()
        await page.locator('[role=dialog]').first.wait_for(state='visible', timeout=20000)
        dialog = await unique_visible(page.locator('[role=dialog]'), 'Douyin cover editor')
        await dialog.get_by_text('上传封面', exact=True).wait_for(timeout=20000)
        area = await unique_visible(dialog.locator('.semi-upload').filter(has_text='点击上传文件或拖拽文件到这里'), 'Douyin cover upload area')
        await area.locator('input.semi-upload-hidden-input').set_input_files(path)
        await page.wait_for_timeout(1500)
        done = dialog.get_by_role('button', name='完成', exact=True)
        await wait_until(page, lambda: enabled(done), seconds=90, label='Douyin cover ready')
        await done.click()
        # First landscape save may offer a portrait upsell. Close it so the
        # explicit portrait asset below is applied through its own control.
        await page.wait_for_timeout(700)
        later = await visible(page.get_by_role('button', name='暂不设置', exact=True))
        if later:
            await later.click()
        await dialog.wait_for(state='hidden', timeout=30000)
        saved = await control.locator('img').count() > 0
        if not saved:
            raise VpubError('cover_unverified', 'Cover editor closed without a saved cover preview.')
        tx.save(**{role+'_cover': {'state':'UI_ACCEPTED', 'remote_pixels_verified':False}})
    # Ordinary videos are published without a self-declaration.
    if tx.options['ai_generated']:
        await page.get_by_text('自主声明', exact=True).click()
        declaration = page.locator('.semi-modal-content').filter(has_text='请选择声明类型')
        await declaration.wait_for(state='visible')
        option = declaration.locator('label.semi-radio').filter(has_text='内容由AI生成')
        await option.click()
        # Semi UI exposes checked state on the rendered label; its hidden native
        # input remains unchecked in this version of Creator Center.
        await option.locator('span.semi-radio-inner-checked').wait_for(state='attached')
        if not await visible(declaration.get_by_text('作者声明：内容由AI生成', exact=True)):
            raise VpubError('declaration_unverified', 'The declaration preview does not match AI-generated content.')
        await declaration.get_by_role('button', name='确定', exact=True).click()
        await declaration.wait_for(state='hidden')
    radio = page.get_by_role('checkbox', name='公开' if tx.options['visibility']=='public' else '仅自己可见', exact=True)
    await radio.check()
    if not await radio.is_checked():
        raise VpubError('visibility_unverified', 'Could not verify the requested visibility.')
    # Chosen on every pass: a form that was reloaded has forgotten them, and publishing it then would go out now and outside its collection.
    if tx.options.get('collection_title'):
        tx.save(collection=await douyin_collection(page, tx.options['collection_title']))
    if tx.options.get('schedule'):
        tx.save(schedule=await douyin_schedule(page, datetime.fromisoformat(tx.options['schedule'].replace('Z', '+00:00'))))
    if await title.input_value() != tx.options['title'] or normalize(await editor.inner_text()).replace('\u200b','') != normalize(caption):
        raise VpubError('metadata_mismatch', 'Douyin title or description differs from the manifest.')
    tx.save(phase='preview', metadata_readback=True, ai_disclosure='native_control', visibility=tx.options['visibility'])
    if tx.options.get('submission_mode') == 'draft_only':
        return await douyin_keep_draft(page, tx)
    post = await unique_visible(page.get_by_role('button', name='发布', exact=True), 'Douyin final publish')
    await tx.submit(page, post)
    await page.wait_for_url(re.compile(r'/content/manage'), timeout=60000)
    return await finish(page, tx, before)


def youtube_link_id(href):
    """The video ID from a link Studio shows, or None for anything else."""
    address = urlsplit(href or '')
    if address.hostname == 'youtu.be':
        candidate = address.path.strip('/')
    elif address.hostname in {'www.youtube.com', 'youtube.com'} and address.path == '/watch':
        candidate = parse_qs(address.query).get('v', [''])[0]
    else:
        return None
    return candidate if re.fullmatch(r'[\w-]{11}', candidate) else None


async def yt_video_id(page):
    for href in await page.locator(f'{YT_DIALOG} a[href]').evaluate_all('es => es.map(e => e.href)'):
        found = youtube_link_id(href)
        if found:
            return found
    return None


async def yt_button_ready(button):
    return await button.is_visible() and await button.evaluate(
        "e => !(e.hasAttribute('disabled') || e.getAttribute('aria-disabled') === 'true' || e.querySelector('button')?.disabled)")


async def yt_radio(page, name, code, label):
    """Choose one of Studio's named options; these names do not change with the interface language."""
    target = page.locator(f'{YT_DIALOG} tp-yt-paper-radio-button[name="{name}"]')
    for _ in range(20):
        radio = await visible(target)
        if radio:
            await radio.click(timeout=15000)
            for _ in range(10):
                if await radio.get_attribute('aria-checked') == 'true':
                    return
                await asyncio.sleep(0.5)
            break
        await asyncio.sleep(0.5)
    raise VpubError(code, f'The YouTube {label} choice could not be confirmed. The upload is kept as a draft; nothing was published.')


async def yt_category(page, category_id):
    trigger = await unique_visible(page.locator(f'{YT_DIALOG} #category ytcp-dropdown-trigger'), 'YouTube category list')
    await trigger.click(timeout=15000)
    options = page.locator(f'tp-yt-paper-item[test-id="CREATOR_VIDEO_CATEGORY_{STUDIO_CATEGORIES[category_id]}"]')
    for _ in range(10):
        item = await visible(options)
        if item:
            break
        await asyncio.sleep(0.5)
    if item is None:
        raise VpubError('category_unverified', 'The YouTube category could not be selected. The upload is kept as a draft; nothing was published.')
    chosen = normalize(await item.inner_text())
    await item.click(timeout=15000)
    await page.wait_for_timeout(500)
    if chosen not in normalize(await trigger.inner_text()):
        raise VpubError('category_unverified', 'The YouTube category was not confirmed. The upload is kept as a draft; nothing was published.')


async def yt_tick(page, box):
    """One playlist box. A field check found the real box covered by its own container, and the ytcp-checkbox-lit around them took the click.
    If nothing can, the keyboard takes over, as it would for a person."""
    holder = box.locator('xpath=ancestor-or-self::ytcp-checkbox-lit[1]')
    try:
        await (holder if await holder.count() else box).click(timeout=YT_TICK_MS)
    except Exception:
        await box.focus(timeout=YT_TICK_MS)
        await page.keyboard.press('Space')


async def yt_playlists(page, titles):
    """Tick exactly the planned playlists by their titles; Studio's picker shows no IDs."""
    trigger = await unique_visible(page.locator(f'{YT_DIALOG} ytcp-video-metadata-playlists ytcp-dropdown-trigger'), 'YouTube playlist list')
    await trigger.click(timeout=15000)
    dialog = page.locator(YT_PLAYLISTS)
    # Only this picker's own Done; the upload dialog's final Save also uses a done-button.
    # The picker's outer element has no size, so this button is what shows it has opened.
    finish = dialog.locator('.done-button')
    await wait_until(page, lambda: visible(finish), seconds=30, label='YouTube playlist picker')
    boxes = dialog.locator('[role="checkbox"]')
    wanted = {normalize(title) for title in titles}
    rows = await boxes.evaluate_all(YT_PLAYLIST_ROWS)
    for title in wanted:
        found = [i for i, row in enumerate(rows) if title in row['lines']]
        if len(found) != 1:
            raise VpubError('playlist_not_found' if not found else 'playlist_ambiguous',
                            f'YouTube shows {len(found)} playlists named "{title}". The upload is kept as a draft; nothing was published.',
                            'Use the exact title of one of this channel\'s playlists, and give playlists different titles.')
    for i, row in enumerate(rows):
        if row['checked'] != bool(wanted & set(row['lines'])):
            await yt_tick(page, boxes.nth(i))
    rows = await boxes.evaluate_all(YT_PLAYLIST_ROWS)
    if any(row['checked'] != bool(wanted & set(row['lines'])) for row in rows):
        raise VpubError('playlist_unverified', 'The YouTube playlists could not be ticked. The upload is kept as a draft; nothing was published.')
    done = await unique_visible(finish, 'YouTube playlist Done')
    await done.click(timeout=15000)
    await finish.first.wait_for(state='hidden', timeout=15000)
    listed = normalize(await trigger.inner_text())
    if any(title not in listed for title in wanted):
        raise VpubError('playlist_unverified', 'The YouTube playlists were not confirmed. The upload is kept as a draft; nothing was published.')


async def yt_schedule(page, when):
    """Fill Studio's own schedule and read the date and time back. Nothing is saved here."""
    for selector in ('#second-container-expand-button', '#schedule-radio-button'):
        opener = await visible(page.locator(f'{YT_DIALOG} {selector}'))
        if opener:
            break
    else:
        raise VpubError('schedule_unverified', 'YouTube Studio offered no schedule option. The upload is kept as a draft; nothing was published.')
    await opener.click(timeout=15000)
    day = page.locator(f'{YT_DIALOG} #datepicker-trigger')
    await wait_until(page, lambda: visible(day), seconds=30, label='YouTube schedule date')
    day = await unique_visible(day, 'YouTube schedule date')
    clock = await unique_visible(page.locator(f'{YT_DIALOG} #time-of-day-container input'), 'YouTube schedule time')
    scheduler = page.locator(f'{YT_DIALOG} ytcp-visibility-scheduler')
    offsets = studio_offsets(await scheduler.first.inner_text() if await scheduler.count() else '')
    if len(offsets) > 1:
        raise VpubError('schedule_unverified', 'YouTube Studio named more than one time zone. The upload is kept as a draft; nothing was published.')
    # Studio's own time zone when it names one, otherwise this computer's, which the browser shares.
    local = when.astimezone(timezone(timedelta(minutes=next(iter(offsets)))) if offsets else None)
    date_style = studio_style(STUDIO_DATES, await day.inner_text())
    time_style = studio_style(STUDIO_TIMES, await clock.input_value())
    if not date_style or not time_style:
        raise VpubError('schedule_unverified', 'YouTube Studio wrote its date or time in a style CrossCast cannot read. The upload is kept as a draft; nothing was published.')
    await day.click(timeout=15000)
    box = await unique_visible(page.locator('ytcp-date-picker tp-yt-paper-input input'), 'YouTube schedule date box')
    await box.fill(date_style[2](local))
    await box.press('Enter')
    await page.wait_for_timeout(1000)
    await clock.fill(time_style[2](local))
    await clock.press('Enter')
    await page.wait_for_timeout(1000)
    if (studio_read(STUDIO_DATES, await day.inner_text()) != (local.year, local.month, local.day)
            or studio_read(STUDIO_TIMES, await clock.input_value()) != (local.hour, local.minute)):
        raise VpubError('schedule_unverified', 'The YouTube schedule was not confirmed. The upload is kept as a draft; nothing was published.')
    return {'at': when.isoformat(), 'local': local.isoformat(), 'date': [local.year, local.month, local.day],
            'time': [local.hour, local.minute], 'timezone_basis': 'studio' if offsets else 'computer'}


async def yt_upload_finished(page):
    # The upload stops when the page closes, so it has to finish before this command can leave.
    progress = page.locator(f'{YT_DIALOG} ytcp-video-upload-progress')
    return await progress.count() > 0 and await progress.first.get_attribute('uploading') is None


async def publish_youtube(page, tx):
    """One upload through Studio's own form. Studio shows the video's ID as soon as the upload starts, so it is saved first."""
    options = tx.options
    tx.save(expected_text=options['description'], visibility=options['visibility'])
    if options.get('create_missing_playlists'):
        from .youtube_studio import ensure_playlists
        tx.save(created_playlists=await ensure_playlists(page, tx.identity, options['playlist_titles']))
    await page.goto(f'https://studio.youtube.com/channel/{tx.identity}/videos/upload?d=ud', wait_until='domcontentloaded', timeout=60000)
    picker = page.locator('ytcp-uploads-file-picker input[type="file"]')
    await wait_until(page, picker.count, seconds=60, label='YouTube upload dialog')
    await picker.first.set_input_files(str(tx.media()), timeout=60000)
    tx.save(phase='uploading')
    title_box = page.locator(f'{YT_DIALOG} #title-textarea #textbox')
    await wait_until(page, lambda: visible(title_box), seconds=180, label='YouTube details form')
    video_id = await wait_until(page, lambda: yt_video_id(page), seconds=180, label='YouTube video ID')
    tx.save(phase='editing', draft_id=video_id, draft_url=f'https://studio.youtube.com/video/{video_id}/edit')
    title = await unique_visible(title_box, 'YouTube title')
    await title.fill(options['title'])
    description = await unique_visible(page.locator(f'{YT_DIALOG} #description-textarea #textbox'), 'YouTube description')
    await description.fill(options['description'])
    if normalize(await title.inner_text()) != normalize(options['title']) or normalize(await description.inner_text()) != normalize(options['description']):
        raise VpubError('metadata_mismatch', 'YouTube title or description differs from the manifest.')
    if tx.cover():
        if not await page.locator(f'{YT_DIALOG} input#file-loader').count():
            raise VpubError('cover_unavailable', 'YouTube offers no custom thumbnail on this channel yet. The upload is kept as a draft; nothing was published.',
                            'Custom thumbnails need a verified channel. Verify it in YouTube Studio, or plan without a cover.')
        await page.locator(f'{YT_DIALOG} input#file-loader').first.set_input_files(tx.cover(), timeout=60000)
        await page.wait_for_timeout(2000)
        tx.save(cover={'state': 'UI_ACCEPTED', 'source': 'production_artwork', 'remote_pixels_verified': False})
    await yt_radio(page, 'VIDEO_MADE_FOR_KIDS_MFK' if options['made_for_kids'] else 'VIDEO_MADE_FOR_KIDS_NOT_MFK', 'audience_unverified', 'audience')
    more = await visible(page.locator(f'{YT_DIALOG} #toggle-button'))
    if more:
        await more.click(timeout=15000)
    # The native AI control is required either way; never silently omit it.
    await yt_radio(page, 'VIDEO_HAS_ALTERED_CONTENT_YES' if options['ai_generated'] else 'VIDEO_HAS_ALTERED_CONTENT_NO', 'declaration_unverified', 'altered-content')
    if options['tags']:
        box = await unique_visible(page.locator(f'{YT_DIALOG} #tags-container input'), 'YouTube tags box')
        await box.press_sequentially(','.join(options['tags']) + ',')
        if await page.locator(f'{YT_DIALOG} #tags-container ytcp-chip').count() != len(set(options['tags'])):
            raise VpubError('metadata_mismatch', 'YouTube tags differ from the manifest.')
    await yt_category(page, options['category_id'])
    if options.get('playlist_titles'):
        await yt_playlists(page, options['playlist_titles'])
        tx.save(playlists=options['playlist_titles'])
    tx.save(phase='details_ready', metadata_readback=True, ai_disclosure='native_control')
    # Details, then video elements, then checks, then visibility.
    for _ in range(3):
        following = await unique_visible(page.locator(f'{YT_DIALOG} #next-button'), 'YouTube Next')
        await wait_until(page, lambda: yt_button_ready(following), seconds=120, label='YouTube Next enabled')
        await following.click(timeout=15000)
        await page.wait_for_timeout(1000)
    if options.get('schedule'):
        # A scheduled video stays private until Studio publishes it at that time.
        when = datetime.fromisoformat(options['schedule'].replace('Z', '+00:00'))
        tx.save(schedule=await yt_schedule(page, when))
    else:
        await yt_radio(page, options['visibility'].upper(), 'visibility_unverified', 'visibility')
    await wait_until(page, lambda: yt_upload_finished(page), seconds=1500, label='YouTube upload')
    tx.save(phase='preview', review_url=page.url)
    if options.get('submission_mode') == 'draft_only':
        return tx.save(state='PREVIEW_READY', note='Uploaded as an unpublished draft in YouTube Studio; nothing was published.')
    done = await unique_visible(page.locator(f'{YT_DIALOG} #done-button'), 'YouTube final Save')
    await wait_until(page, lambda: yt_button_ready(done), seconds=120, label='YouTube final Save enabled')
    tx.save(id=video_id, url=work_url('youtube', tx.identity, video_id))
    await tx.submit(page, done)
    try:
        await done.wait_for(state='hidden', timeout=60000)
    except Exception:
        pass
    result = {}
    for attempt in range(8):
        result = await verify(page, 'youtube', tx.identity, tx.receipt)
        if result['state'] == 'SUBMISSION_VERIFIED':
            break
        await asyncio.sleep(5)
    return tx.save(**result)
