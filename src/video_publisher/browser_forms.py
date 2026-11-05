"""Official upload forms driven by the CLI's own persistent browser."""
from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .categories import STUDIO_CATEGORIES
from .core import VpubError, douyin_caption, stop_check
from .browser_runtime import failure_message, look_until, risk_check, unique_visible, visible
from .progress import stage
from .browser_backends import (STUDIO_DATES, STUDIO_TIMES, X_CHOSEN_TIME, YT_DONE_WINDOW, YT_EDIT_DRAFT, choose_file, choose_in_picker, normalize, studio_offsets, studio_read, studio_style, studio_zone_offsets_agree,
                               upload_file, wait_until, work_url, works, verify, verify_scheduled_x, x_chosen_time_says, x_new_works, x_new_posts, x_schedule_baseline, x_schedule_moment, douyin_card_stamp, douyin_card_status, douyin_new_works, douyin_card_words, douyin_cards_at, douyin_cards_titled, douyin_scheduled_card, douyin_type_caption, DOUYIN_SCHEDULED)

X_CLOCK = re.compile(r'\d{1,2}:\d{2}\s*[AP]M', re.I)
YT_DIALOG = 'ytcp-uploads-dialog'
YT_PLAYLISTS = 'ytcp-playlist-dialog'
YT_TICK_MS = 5000
YT_UPLOAD_SECONDS = 1500
YT_LINK_SECONDS = 600
YT_CHECKS_WARNING = 'ytcp-prechecks-warning-dialog'
YT_CHECKS_DONE = re.compile('检查完毕|檢查完畢|Checks complete', re.I)
YT_CHECKS_SECONDS = 600
YT_PRESS_ANSWER_SECONDS = 60
YT_DONE_LOCATOR = 'ytcp-dialog > tp-yt-paper-dialog'  # Studio's own window after a Save, which a field run found outside the upload window.
# The final Save lights up only once Studio has finished processing in the background;
# a 75-second clip on a real channel was still grey after two minutes.
YT_SAVE_SECONDS = 600
# What's open on the page after Publish, for a press that neither closed the form nor brought the warning.
YT_OPEN_DIALOGS = """() => [...document.querySelectorAll('tp-yt-paper-dialog, [role="dialog"]')].filter(e => e.getClientRects().length).map(e => ({
    tag: e.tagName, parent: e.parentElement?.tagName, text: (e.innerText || '').slice(0, 300),
    buttons: [...e.querySelectorAll('button')].filter(b => b.getClientRects().length).map(b => b.getAttribute('aria-label') || b.innerText).slice(0, 8)}))"""
# What's open when something covers the final button: windows, menus and lists, each by kind and its first few words.
YT_ON_TOP = """() => [...document.querySelectorAll('tp-yt-iron-dropdown, tp-yt-paper-dialog, ytcp-dialog, [role="dialog"], [role="alertdialog"], [role="listbox"]')]
    .filter(e => e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden')
    .map(e => ({tag: e.tagName.toLowerCase(), id: e.id || null, opened: e.hasAttribute('opened'), text: (e.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 80)}))"""
# Each playlist row's checkbox, its own lines of text and whether it is ticked.
YT_PLAYLIST_ROWS = """boxes => boxes.map(box => {
    const row = box.closest('li, label, ytcp-ve') || box.parentElement;
    const lines = (row ? row.innerText : '').split('\\n').map(s => s.replace(/\\s+/g, ' ').trim()).filter(Boolean);
    return {lines, checked: box.getAttribute('aria-checked') === 'true'};
})"""


X_MEDIA_PAGE = re.compile(r'/library/(\d+_\d+)(/.*)?')
X_OPEN_SECONDS = 15
X_FORM_SECONDS = 300
X_FIRST_CLICK_SECONDS = 120
X_UPLOAD_BYTES_PER_SECOND = 2_000_000
X_DONE_SECONDS = 60
X_VIDEO_SECONDS = 30
X_CHANGE_THUMBNAIL = re.compile(r'^\s*Change thumbnail\s*$')


async def enabled(locator):
    return await locator.is_visible() and await locator.is_enabled()


async def finish(page, tx, before):
    """Read own account after one submit; never click Post/Share again."""
    await page.wait_for_timeout(5000)
    for attempt in range(8):
        found = await works(page, tx.platform, tx.identity)
        if tx.platform == 'douyin':
            cards = page.locator('[class^="video-card-content-"]').filter(has_text=tx.options['title'])
            if await cards.count() == 1 and '审核中' in await douyin_card_status(cards):
                return tx.save(state='SUBMITTED_UNVERIFIED', phase='platform_review', platform_status='UNDER_REVIEW',
                               error=None, review_url=page.url, note='Matching submitted work is under review; verify later, never reupload.')
            if await douyin_scheduled_card(page, tx.receipt):
                return tx.save(**DOUYIN_SCHEDULED)
        if tx.platform == 'x':
            candidates = await x_new_posts(page, tx.identity, tx.receipt)
        else:
            fresh = [v for k, v in found.items() if k not in before]
            matches = [v for v in fresh if tx.options['title'] in v.get('text', '')]
            candidates = douyin_new_works(tx.platform, tx.receipt, matches)
        if len(candidates) == 1:
            item = candidates[0]
            identified = {'id': item['id'], 'url': item['url']}
            if tx.platform == 'x':
                identified['discovery_source'] = item['discovery_source']
            receipt = tx.save(state='SUBMITTED_UNVERIFIED', phase='readback', **identified)
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
    return x_schedule_moment(when)


X_FILE_VIDEOS = """form => [...form.querySelectorAll('video')].map(video => video.currentSrc || video.src)"""
X_VIDEO_LENGTH = """form => { const video = form.querySelector('video'); return video && video.readyState >= 1 && Number.isFinite(video.duration) ? video.duration : null; }"""
X_COMPOSER_VIDEOS = """(form, nodes) => {
  if (!form.isConnected || !nodes.every(node => form.contains(node))) return null;
  let around = nodes[0];
  while (!around.contains(nodes[1])) around = around.parentElement;
  if (around === form) return null;
  return [...around.querySelectorAll('video')].map(video => video.currentSrc || video.src);
}"""


async def x_video_is_whole(page, form, planned):
    """A plan that says how long its video is gets that checked before anything is posted, within a second. A field run's form showed a source while a video could still have been processing. Its size is no use here: a 1920x1080 film's preview played at 480x270."""
    length = await look_until(page, lambda: form.evaluate(X_VIDEO_LENGTH), X_VIDEO_SECONDS * 1000)
    if length is None or abs(length - planned) > 1:
        raise VpubError('video_unverified', f"This file's video shows {length} seconds, not the {planned} the plan says, so nothing was posted.", 'Media Studio may still be processing it. vpub resume looks again without uploading.')


async def x_composer_is_this_files(form, video, caption, final, *others):
    """A field read found the composer sits inside the file's own form, which stays on the page though its selector stops matching once the composer opens, and that it shows the same video as the form did. So these all have to be inside that form, in something smaller than the form itself, around that video.
    Reviews showed being the only one on the page, or sharing some wider element, proves nothing about whose they are, and that with no video from the form to compare, any video or none would pass. So no video on record means no final press."""
    if not video:
        return False
    handles = [await control.element_handle(timeout=5000) for control in (caption, final, *others)]
    return await form.evaluate(X_COMPOSER_VIDEOS, handles) == [video]


async def x_send_scheduled(page, tx, form, video, still_same_media=None):
    """The calendar's own Save only closes it, a field run found, and nothing is sent. The composer's final button then reads Done, and the button that opened the calendar shows the date and time.
    Done is the press that schedules, so it alone counts as the final submit, once what it is about to send reads back. If that button still says Post it would post at once, so anything else stops here with nothing posted.
    They have to be this file's composer's, and the time and Done have to sit side by side."""
    from patchright.async_api import Error as BrowserError
    unconfirmed = 'X Studio did not show this schedule ready to save. Nothing was posted.'
    when = datetime.fromisoformat(tx.options['schedule'].replace('Z', '+00:00'))
    caption = page.get_by_placeholder("What's happening?", exact=True)
    any_final = page.locator('button.tweetbtn:visible')
    schedule = await x_schedule(page, when)
    tx.save(schedule=schedule)
    save = await unique_visible(page.locator('button[type="button"]').filter(has_text=re.compile(r'^\s*Save\s*$')), 'X calendar Save')
    await wait_until(page, lambda: enabled(save), seconds=30, label='X calendar Save enabled')
    # The press itself asks for the word Done, because a review showed the label could change between looking and pressing.
    done = any_final.filter(has_text=re.compile(r'^\s*Done\s*$'))
    chosen = page.locator('button[type="button"]:visible').filter(has_text=X_CHOSEN_TIME)
    row = any_final.locator('xpath=..')  # A field run's read of the page has the time and the final button side by side under one parent.

    async def not_ready():
        """A field run stopped here with Done on the page and one button greyed out, and nothing to say whose, so a stop says what these checks saw. It holds no words from the page."""
        async def read(look):
            try:
                return await look()
            except BrowserError:
                return None

        async def time_is_this_schedule():
            return x_chosen_time_says(await chosen.inner_text(timeout=2000), schedule)

        async def words_are_the_plans():
            return await caption.input_value(timeout=2000) == tx.options['post_text']

        async def tied():
            return await x_composer_is_this_files(form, video, caption, any_final)

        # A review showed a class can hold anything, and this message is kept, so it only says whether the class says so.
        async def class_says_off():
            return bool(re.search('disabled', await any_final.get_attribute('class', timeout=2000) or '', re.I))

        async def aria_says_off():
            return await any_final.get_attribute('aria-disabled', timeout=2000) == 'true'
        seen = {'finals_showing': await read(any_final.count), 'finals': await read(page.locator('button.tweetbtn').count), 'says_done': await read(done.count),
                'final_enabled': await read(lambda: enabled(any_final)), 'final_class_says_off': await read(class_says_off), 'final_aria_says_off': await read(aria_says_off),
                'calendar_open': await read(page.locator('fieldset.CalendarNavigation-monthFormSelect select:visible').count), 'times_showing': await read(chosen.count),
                'time_is_this_schedule': await read(time_is_this_schedule), 'buttons_beside_final': await read(row.locator('button:visible').filter(has_text=re.compile(r'\S')).count),
                'captions': await read(caption.count), 'words_are_the_plans': await read(words_are_the_plans), 'words_length': len(tx.options['post_text']), 'tied_to_the_file': await read(tied)}
        return VpubError('schedule_unverified', f'{unconfirmed} Last look: {seen}.')
    if not await x_composer_is_this_files(form, video, caption, any_final, save):
        raise await not_ready()
    if still_same_media:
        still_same_media()
    await save.click(timeout=15000)

    async def says_done():
        return (await any_final.count() == 1 and await done.count() == 1 and await enabled(done)
                and not await page.locator('fieldset.CalendarNavigation-monthFormSelect select:visible').count())

    async def ready_to_send():
        if (not await says_done() or await chosen.count() != 1 or not x_chosen_time_says(await chosen.inner_text(), schedule)
                or await row.locator('button[type="button"]:visible').filter(has_text=X_CHOSEN_TIME).count() != 1
                or await row.locator('button:visible').filter(has_text=re.compile(r'\S')).count() != 2
                or await page.locator('button.tweetbtn').count() != 1 or await caption.count() != 1 or not await x_composer_is_this_files(form, video, caption, any_final, chosen)):
            raise await not_ready()
        if await caption.input_value() != tx.options['post_text']:
            raise VpubError('metadata_mismatch', 'X post text differs from the manifest. Nothing was posted.')
        if still_same_media:
            still_same_media()
    if not await look_until(page, says_done, 15000):
        raise await not_ready()
    await ready_to_send()
    await tx.submit(page, done, still_holds=ready_to_send)
    # Leaving the page before Done has finished could cut it off, so while its composer is open this stays put, and says so if it never closes.
    try:
        await any_final.wait_for(state='hidden', timeout=X_DONE_SECONDS * 1000)
    except BrowserError:
        return tx.save(state='SUBMITTED_UNVERIFIED', phase='readback_pending',
                       note="Done was pressed once and its composer stayed open for a minute, so nothing here says whether it was scheduled. Check Studio's schedule page; do not post again.")
    await page.wait_for_timeout(3000)
    return tx.save(**await verify_scheduled_x(page, tx.identity, tx.receipt))


def x_studio_path(address):
    """The path of an address on Media Studio itself, or an empty one. A review found a Studio address quoted inside another could be taken for the page."""
    parts = urlsplit(address)
    return parts.path if parts.scheme == 'https' and parts.netloc == 'studio.x.com' else ''


def x_media_address(address):
    """The uploaded file's own page, once the address says which file it is. Field runs found its form can open before that."""
    found = X_MEDIA_PAGE.fullmatch(x_studio_path(address))
    return {'media_url': f'https://studio.x.com/library/{found[1]}/edit'} if found else {}


def x_file_link(filename):
    return 'a[download="' + filename.replace('\\', '\\\\').replace('"', '\\"') + '"]'


async def x_link_names_another_video(form, filename, media_url):
    """Whether this form's download link names a different video from the page's address. Field reads found its link on video.twimg.com under the address's second number. A link of any other shape says nothing either way."""
    number = re.search(r'/library/\d+_(\d+)/', media_url or '')
    found = re.match(r'https://video\.twimg\.com/(?:amplify_video|ext_tw_video)/(\d+)/', await form.locator(x_file_link(filename)).first.get_attribute('href') or '')
    return bool(number and found and found[1] != number[1])


def x_media_form(page, filename):
    """This file's own form, because filling another file's and composing from it could post the wrong video. A field check found the form holds a download link carrying the file's exact name, with Library's Upload Media button and tiles further out.
    It is the outermost element with that link and Change thumbnail, without that button, and without another file's link. Reviews showed counting the name across the page proves nothing, and that a wider element can hold this file's link beside another file's form. The classes on this page are generated."""
    # Only what shows counts: a review found a hidden copy of the form could stand in front of the real one for the whole wait.
    return (page.locator('div:visible').filter(has=page.locator(x_file_link(filename) + ':visible')).filter(has=page.locator('button:visible').filter(has_text=X_CHANGE_THUMBNAIL))
            .filter(has_not=page.locator('button').filter(has_text=re.compile(r'^\s*Upload Media\s*$')))
            .filter(has_not=page.locator(f'a[download]:not({x_file_link(filename)})'))).first


async def x_form_shows(page, form, filename):
    """This file's form shows, and only one does. Each has its own download link, so two showing means the right one can't be told."""
    return await form.count() == 1 and await page.locator(x_file_link(filename) + ':visible').count() == 1


async def x_names_showing(name):
    return [item for item in await name.all() if await item.is_visible()]


X_NAME_AMONG_TILES = r"""(name, link) => {
  let around = name;
  while (!around.contains(link)) around = around.parentElement;
  return [...around.querySelectorAll('button')].some(button => /^\s*Upload Media\s*$/.test(button.textContent));
}"""


async def x_names_among_tiles(names, link):
    """Of the names showing, those out among Library's tiles rather than in this file's details: a field read found Upload Media further out than everything around this file's link."""
    from patchright.async_api import Error as BrowserError
    try:
        if await link.count() != 1:
            return []
        around = await link.element_handle(timeout=5000)
        return [name for name in names if await name.evaluate(X_NAME_AMONG_TILES, around, timeout=5000)]
    except BrowserError:
        return []  # The page is still changing, and the next look says what it became.


def x_first_click_hold(size):
    return X_FIRST_CLICK_SECONDS + size / X_UPLOAD_BYTES_PER_SECOND


async def x_open_media(page, name, form, filename, hold=0):
    """Field runs clicked the uploaded file's name as soon as it showed, and the address didn't change in 30 seconds, while 15 seconds on the name showed twice. A click two minutes after an upload opened the form at once.
    So the file is open once its own form shows, and the address is not waited for. A click changes nothing, and comes again only while no form has come.
    Two field runs in a row clicked five seconds in and got the file's details instead: this file's link and its title box, still on /library, with no Change thumbnail anywhere for ten minutes, while a fresh page's click reached the form. So once those details show, the click comes again on the name out among the tiles. A click can't cut off an upload the way leaving the page could.
    The next field run clicked 36 times on a 113 MB file's tile and got no further than those details. So a new upload's first click waits as well, which is the `hold`: it's a guess at when the upload ends, since nothing on the page has been seen to say."""
    from patchright.async_api import Error as BrowserError
    clicked = None
    began = time.monotonic()
    seen = {'clicks': 0}
    change = page.locator('button').filter(has_text=X_CHANGE_THUMBNAIL)
    link = page.locator(x_file_link(filename) + ':visible')

    async def opened():
        nonlocal clicked
        if await x_form_shows(page, form, filename):
            return True
        showing = await x_names_showing(name)
        # A field run stopped with one click, the name twice and this file's link showing, and a fresh page then opened its form at once. Those counts couldn't say why, so these are for the next one.
        seen.update(names=await name.count(), names_showing=len(showing), links_showing=await link.count(),
                    change_buttons=await change.count(), change_showing=len(await x_names_showing(change)), other_links=await page.locator(f'a[download]:not({x_file_link(filename)})').count(),
                    progress_showing=await page.locator('[role="progressbar"]:visible').count(),
                    path=re.sub(r'\d+', '<n>', x_studio_path(page.url)))
        if seen['links_showing']:
            seen.setdefault('link_first_at', round(time.monotonic() - began))
        due = time.monotonic() - began >= hold and (clicked is None or time.monotonic() - clicked >= X_OPEN_SECONDS)
        target = showing if len(showing) == 1 else await x_names_among_tiles(showing, link) if due else []
        if due and len(target) == 1:
            clicked = time.monotonic()
            seen.setdefault('first_click_at', round(clicked - began))
            seen['clicks'] += 1
            try:
                await target[0].click(timeout=X_OPEN_SECONDS * 1000)
            except BrowserError:
                pass  # The next look says what happened.
        return False
    try:
        await wait_until(page, opened, seconds=hold + 600, label="the uploaded file's form in Media Studio")
        return seen  # So a run that gets through can show whether a second click was what did it.
    except VpubError as failure:
        if failure.code != 'processing_timeout':
            raise
        # A field run ended here with nothing to say which of several things had happened. These are counts, with no words from the page.
        # A fresh page then opened the same file with one click, which is what a resume does.
        raise VpubError(failure.code, f"{failure.message} Last look: {seen}. vpub resume looks again on a fresh page without uploading again.", failure.hint) from None


async def publish_x(page, tx):
    before = tx.receipt.get('before_ids')
    if before is None:
        before = await works(page, 'x', tx.identity)
    tx.save(before_ids=list(before), expected_text=tx.options['post_text'])
    # Read once, before anything of this post's could be there, and kept, so that a row already there can't pass for its result.
    if tx.options.get('schedule') and tx.options.get('submission_mode') != 'draft_only' and 'schedule_rows_before' not in tx.receipt:
        when = datetime.fromisoformat(tx.options['schedule'].replace('Z', '+00:00'))
        tx.save(schedule_rows_before=await x_schedule_baseline(page, tx.options['post_text'], x_schedule_moment(when)))
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
    # A clip already in Media Studio, from a plan that names its page. A field run's dry run left one, and uploading the same file again would give two tiles of one name, which get no click. Its form only counts if its file has this video's name.
    if tx.options.get('existing_media_url') and not tx.receipt['media_selected']:
        tx.save(media_selected=True, upload_filename=tx.media_name(), media_url=tx.options['existing_media_url'], reused_media=True)
    retained_media_url = tx.receipt.get('media_url')

    def still_same_media():
        if retained_media_url and x_media_address(page.url).get('media_url') != retained_media_url:
            raise VpubError('wrong_work', "Media Studio is no longer on this attempt's file, so no further changes were sent.", 'Look at the original file in Media Studio. Do not upload it again.')

    hold = 0
    if tx.receipt.get('media_url'):
        if not re.fullmatch(r'https://studio\.x\.com/library/\d+_\d+/edit', tx.receipt['media_url']):
            raise VpubError('invalid_media_url', 'Resume requires the original Media Studio asset URL.')
        await page.goto(tx.receipt['media_url'], wait_until='domcontentloaded', timeout=60000)
        # From a review: a page that went on to another file would have overwritten this attempt's address.
        still_same_media()
    elif not tx.receipt['media_selected']:
        media = tx.media()
        await upload_file(page, media, accept='video/')
        tx.save(phase='uploading')
        hold = x_first_click_hold(Path(media).stat().st_size)
    filename = tx.receipt['upload_filename']
    form = x_media_form(page, filename)
    change = form.locator('button:visible').filter(has_text=X_CHANGE_THUMBNAIL)
    if not tx.receipt.get('media_url'):
        look = await x_open_media(page, page.get_by_text(filename, exact=True), form, filename, hold)
        tx.save(phase='opening_media', open_look=look, **x_media_address(page.url))
        retained_media_url = tx.receipt.get('media_url')
    await change.wait_for(state='visible', timeout=X_FORM_SECONDS * 1000)
    retained_media_url = retained_media_url or x_media_address(page.url).get('media_url')
    still_same_media()
    if await x_link_names_another_video(form, filename, retained_media_url):
        raise VpubError('wrong_work', "This form's file is a different video from the one its address names, so nothing was changed.", 'Look at the file in Media Studio. Do not upload it again.')
    still_same_media()
    tx.save(phase='media_ready', **x_media_address(page.url))
    # The wait can be long, and what comes next changes the file's details.
    await risk_check(page)
    stop_check('x')
    still_same_media()
    if tx.cover() and not tx.receipt.get('cover'):
        await change.click()
        # Distinguish the image-only thumbnail input from the mixed media input.
        inp = page.locator('input[type=file][accept="image/jpeg,image/jpg,image/png"]')
        if await inp.count() != 1:
            raise VpubError('layout_changed', 'Media Studio thumbnail input is ambiguous.')
        await choose_file(page, inp, tx.cover())
        choose = page.locator('button').filter(has_text=re.compile(r'^\s*Choose\s*$'))
        # Uploaded images are saved immediately; Choose is for a video frame.
        await choose.wait_for(state='hidden', timeout=30000)
        await page.wait_for_timeout(2000)
        tx.save(phase='cover_selected', cover={'source':'production_artwork', 'state':'UI_ACCEPTED', 'remote_pixels_verified':False})
    details = bool(await visible(form.get_by_placeholder('Title', exact=True)))
    still_same_media()
    tx.save(media_details_filled=details)
    if details:
        media_title = await unique_visible(form.get_by_placeholder('Title', exact=True), 'X media title')
        if len(tx.options['title']) > 70:
            raise VpubError('title_too_long', 'X Studio title must fit 70 characters; do not silently truncate.')
        await media_title.fill(tx.options['title'])
        await media_title.press('Tab')
        description = None
        if len(tx.options['description']) <= 200:
            description = await unique_visible(form.get_by_placeholder('Type a brief description here', exact=True), 'X media description')
            await description.fill(tx.options['description'])
            await description.press('Tab')
        await page.wait_for_timeout(1500)
        # From a review: a run could record its text as read back on the post's words alone.
        if normalize(await media_title.input_value()) != normalize(tx.options['title']) or (description and normalize(await description.input_value()) != normalize(tx.options['description'])):
            raise VpubError('metadata_mismatch', "X Studio's title or description differs from the manifest. Nothing was posted.")
    # The non-icon detail Post button opens the composer; it does not publish.
    # A field run looked straight after filling the details and found no such button, and a moment later it was there.
    compose_buttons = form.locator('button[type="button"]').filter(has_text=re.compile('^Post$'))
    await wait_until(page, lambda: visible(compose_buttons), seconds=30, label='Studio compose button')
    compose = await unique_visible(compose_buttons, 'Studio compose button')
    file_form = await form.element_handle(timeout=15000)

    async def form_video():
        return [source for source in await file_form.evaluate(X_FILE_VIDEOS) if source]
    # A preview doesn't press anything final, so it doesn't wait. It only says what it saw, which is how a new upload's form can be found out without posting.
    draft_only = tx.options.get('submission_mode') == 'draft_only'
    file_videos = await form_video() if draft_only else await look_until(page, form_video, X_VIDEO_SECONDS * 1000) or []
    if len(file_videos) > 1:
        raise VpubError('layout_changed', "This file's form shows more than one video, so its composer couldn't be tied to it. Nothing was posted.")
    file_video = file_videos[0] if file_videos else None
    tx.save(file_video_seen=bool(file_video))
    if tx.options.get('duration_seconds') and not draft_only:
        await x_video_is_whole(page, file_form, tx.options['duration_seconds'])
    still_same_media()
    await compose.click()
    caption = page.get_by_placeholder("What's happening?", exact=True)
    await caption.wait_for(timeout=20000)
    still_same_media()
    await caption.fill(tx.options['post_text'])
    if await caption.input_value() != tx.options['post_text']:
        raise VpubError('metadata_mismatch', 'X post text differs from the manifest.')
    still_same_media()
    tx.save(phase='preview', review_url=page.url, ai_disclosure='caption_text', metadata_readback=True, **x_media_address(page.url))
    if tx.options.get('submission_mode') == 'draft_only':
        return tx.save(state='PREVIEW_READY', note='Media saved in Studio; composer preview is not a confirmed persistent post draft.')
    if tx.options.get('schedule'):
        return await x_send_scheduled(page, tx, file_form, file_video, still_same_media=still_same_media)
    post = await unique_visible(page.locator('button[type="button"]').filter(has_text=re.compile('^Post$')), 'final Post')
    await wait_until(page, lambda: enabled(post), seconds=90, label='X final Post enabled')

    async def still_this_files():
        if not await x_composer_is_this_files(file_form, file_video, caption, post):
            raise VpubError('layout_changed', "X's composer could not be tied to this file's form and video. Nothing was posted.")
        if await caption.input_value() != tx.options['post_text']:
            raise VpubError('metadata_mismatch', 'X post text differs from the manifest. Nothing was posted.')
        still_same_media()
    await still_this_files()
    await tx.submit(page, post, still_holds=still_this_files)
    # As with Done, leaving before its composer has closed could cut the post off. A field run's closed as soon as it was pressed. A minute on, reading the account says more than waiting would.
    from patchright.async_api import Error as BrowserError
    try:
        await page.locator('button.tweetbtn:visible').wait_for(state='hidden', timeout=X_DONE_SECONDS * 1000)
    except BrowserError:
        pass
    return await finish(page, tx, before)


# Instagram's own words for its controls. A field run's account showed its pages in zh-hk, where a read of the real page found one
# control labelled 新帖子 (the English page's svg is New post) and a composer whose button said 從電腦選擇. The other Chinese words are
# Instagram's usual ones and haven't been seen on a real page yet. Each is the whole name, so a longer one that holds it isn't taken for
# it, and a step that finds none of its words stops before anything is shared and says which step it was.
IG_NEW_POST = 'svg[aria-label="New post"], [aria-label="新帖子"], [aria-label="新貼文"]'
IG_FROM_COMPUTER = re.compile(r'^\s*(Select from computer|從電腦選擇|从电脑选择)\s*$')
IG_NEXT = re.compile(r'^\s*(Next|下一步)\s*$')
IG_OK = re.compile(r'^\s*(OK|確定|确定|好)\s*$')
# The zh-hk crop control is none of these: a field run found the file chosen and then no crop control to press. Every Chinese word
# for crop holds 裁, and the composer's step has no other icon that does, so one in the composer window holding it counts too.
IG_CROP = ', '.join([*(f'svg[aria-label="{word}"]' for word in ('Select crop', '選取裁剪', '選擇裁剪', '选择裁剪', '選取裁切', '選擇裁切')),
                     '[role="dialog"] svg[aria-label*="裁"]'])
IG_ORIGINAL = re.compile(r'^\s*(Original|原始|原圖|原图)\s*$')
# Its Chinese word isn't known either: every guess starts with 原 and is short, and in the composer window only that choice is.
IG_ORIGINAL_LIKE = re.compile(r'^\s*原\S{0,4}\s*$')
IG_AI_LABEL = re.compile(r'^\s*(Add AI label|(加上|新增|添加|加入)\s*AI\s*(標籤|标签))\s*$')
IG_ADVANCED = re.compile(r'^\s*(Advanced settings|進階設定|高級設定|高级设置)\s*$')
IG_SHARE = re.compile(r'^\s*(Share|分享)\s*$')
IG_SHARE_TO = re.compile(r'^\s*(Share to|分享至|分享到)\s*$')
IG_SHARED = re.compile(r'(Your (post|reel) has been shared|Post shared|已分享|已發佈|已发布)', re.I)
IG_LANGUAGE_HINT = ("CrossCast knows Instagram's English words for each step, and its usual Chinese ones. If Instagram shows its pages in another "
                    "language, or a Chinese word has changed, switching Instagram to English avoids this. Then vpub resume starts this attempt's "
                    "steps over; nothing was shared.")


async def instagram_language(page):
    """The language Instagram's page says it's in, for an error to name. It names nobody."""
    try:
        return await page.evaluate("document.documentElement.lang || ''") or 'unknown'
    except Exception:
        return 'unknown'


async def instagram_shares_elsewhere(page):
    """Whether the step's own Share to row has a switch on, which would post this somewhere the plan doesn't say. A field read found that row's words and its one switch under the same grandparent, and the word Threads only in the page's footer, so words elsewhere on the page say nothing."""
    heading = await visible(page.get_by_text(IG_SHARE_TO))
    if heading is None:
        return False
    switches = heading.locator('../..').locator('[role=switch],input[type=checkbox]')
    return any([await box.is_checked() for box in await switches.all() if await box.is_visible()])


INSTAGRAM_NEW_POST_SECONDS = 15


async def publish_instagram(page, tx):
    """One upload through Instagram's own steps. A resume starts them over, because the composer keeps nothing until Share: a field run's preview closed with the account's posts unchanged, and its form said it may not outlast the browser."""
    before = await works(page, 'instagram', tx.identity)
    caption = tx.options['post_text']
    if len(caption) > 2200:
        raise VpubError('description_too_long', 'Instagram caption exceeds 2200 characters; no upload started.')
    tx.save(before_ids=list(before), expected_text=caption)
    # Sidebar New post; account remains the one verified before navigation. A field run found none in 10 seconds after a sign-in
    # made elsewhere, and its error could only say waiting for locator(…), so this one names the control.
    new_post = await look_until(page, lambda: visible(page.locator(IG_NEW_POST)), INSTAGRAM_NEW_POST_SECONDS * 1000)
    if new_post is None:
        raise VpubError('layout_changed', f"Instagram's New post button didn't show on the profile page, so nothing was uploaded. The page gives its language as {await instagram_language(page)}.",
                        "Open Instagram with vpub auth login instagram and look at what it shows: after a sign-in made elsewhere it can ask something first. " + IG_LANGUAGE_HINT)
    from patchright.async_api import Error as BrowserError
    step = "opening the composer's file picker"
    try:
        await new_post.click()
        await page.get_by_role('button', name=IG_FROM_COMPUTER).wait_for(timeout=20000)
        step = 'choosing the video'
        await upload_file(page, tx.media(), accept='video/')
        tx.save(phase='uploading')
        async def crop_ready():
            notice = await visible(page.get_by_text('Video posts are now shared as reels', exact=True))
            if notice:
                await page.get_by_role('button', name=IG_OK).click()
            else:
                # The same notice in a language whose words haven't been seen: a window that names Reels and has an OK button. The composer's own steps have none.
                ok = await visible(page.get_by_role('dialog').filter(has_text=re.compile('reels', re.I)).get_by_role('button', name=IG_OK))
                if ok:
                    await ok.click()
            return await visible(page.get_by_role('button', name=IG_NEXT))
        step = 'waiting for the crop step'
        await wait_until(page, crop_ready, seconds=600, label='Instagram crop step')
        tx.save(phase='crop')
        step = 'choosing the original crop'
        await page.locator(IG_CROP).click()
        # One choice only: the same one found both ways counts once, and two different ones stop here.
        await page.get_by_text(IG_ORIGINAL).or_(page.locator('[role="dialog"]').get_by_text(IG_ORIGINAL_LIKE)).click()
        tx.save(crop='original', video_modified=False)
        step = 'going on to the cover step'
        await page.get_by_role('button', name=IG_NEXT).click()
        tx.save(phase='cover')
        cover_button = page.get_by_text(IG_FROM_COMPUTER)
        if tx.cover():
            await cover_button.wait_for(state='visible', timeout=60000)
            await choose_in_picker(page, cover_button, tx.cover())
            await page.wait_for_timeout(2000)
            tx.save(cover={'state':'UI_ACCEPTED', 'source':'production_artwork', 'remote_pixels_verified':False})
        else:
            # A field look found this step shows that button. With no cover to wait for, a press straight after the last could land before the step has come, and a review showed pressing on without it would press the last step's Next again.
            if not await look_until(page, lambda: visible(cover_button), 60000):
                raise VpubError('layout_changed', "Instagram's next step did not come, so nothing was shared.")
        step = 'going on to the caption step'
        await page.get_by_role('button', name=IG_NEXT).click()
    except BrowserError as exc:
        raise VpubError('layout_changed', f"Instagram's composer stopped while {step}, so nothing was shared. The page gives its language as {await instagram_language(page)}. {failure_message(exc)}",
                        IG_LANGUAGE_HINT) from None
    tx.save(phase='caption')
    boxes = page.locator('textarea,[contenteditable=true]')
    # Field runs found Instagram's menus and buttons come a moment after a press, and finding no box here would stop an upload that was going fine.
    await wait_until(page, lambda: visible(boxes), seconds=30, label='Instagram caption')
    editor = await unique_visible(boxes, 'Instagram caption')
    await editor.fill(caption)

    async def words_are_kept():
        actual = await editor.input_value() if await editor.evaluate('e=>e.tagName')=='TEXTAREA' else await editor.inner_text()
        if normalize(actual) != normalize(caption):
            raise VpubError('metadata_mismatch', 'Instagram caption differs from the manifest.')
    await words_are_kept()
    # The native AI control is required when requested; never silently omit it.
    label = page.get_by_text(IG_AI_LABEL)

    async def ai_switch():
        """The AI label's switch. A field look found several switches in this step, each an input of its own, so the nearest one to the words has to be the only one that near, or it could be another setting."""
        async def switches_in(row):
            return [box for box in await row.locator('[role=switch],input[type=checkbox]').all() if await box.is_visible()]
        ai = await visible(label)
        if ai is None:
            return None
        row = ai.locator('..')
        switches = await switches_in(row) or await switches_in(row.locator('..'))
        return switches[0] if len(switches) == 1 else None
    if tx.options['ai_generated']:
        ai = await visible(label)
        if ai is None:
            advanced = await visible(page.get_by_text(IG_ADVANCED))
            if advanced:
                await advanced.click()
            ai = await look_until(page, lambda: visible(label), 5000)
        if ai is None:
            raise VpubError('declaration_unverified', f'Instagram AI-label control is unavailable; upload retained, Share not clicked. The page gives its language as {await instagram_language(page)}.',
                            IG_LANGUAGE_HINT)
        switch = await ai_switch()
        if switch is None:
            raise VpubError('declaration_unverified', 'Instagram AI-label switch could not be identified.')
        if not await switch.is_checked():
            await switch.click()
        if not await look_until(page, switch.is_checked, 3000):
            raise VpubError('declaration_unverified', 'Instagram AI label was not confirmed.')
    tx.save(phase='preview', metadata_readback=True, ai_disclosure='native_control', shares_elsewhere=await instagram_shares_elsewhere(page))
    if tx.options.get('submission_mode') == 'draft_only':
        return tx.save(state='PREVIEW_READY', note='Reached unpublished preview; this web composer may not persist after the browser closes.')
    post = await unique_visible(page.get_by_role('button', name=IG_SHARE), 'Instagram final Share')

    async def still_says_it():
        await words_are_kept()
        if await instagram_shares_elsewhere(page):
            raise VpubError('shares_elsewhere', "Instagram's Share to switch is on, so this would also go to another platform. Nothing was shared.", 'Turn it off on Instagram, then run vpub resume.')
        if tx.options['ai_generated']:
            # Found afresh, because a review showed a switch added in front could take the old one's place.
            found = await ai_switch()
            if found is None or not await found.is_checked():
                raise VpubError('declaration_unverified', 'Instagram AI label was not confirmed.')
    await tx.submit(page, post, still_holds=still_says_it)
    # Its window may say this more than once, and an error about that here would read as a failed post after it has gone out.
    await page.get_by_text(IG_SHARED).filter(visible=True).first.wait_for(timeout=600000)
    return await finish(page, tx, before)


DOUYIN_OFFER_SECONDS = 30


async def douyin_reopen_kept_draft(page, tx, caption):
    """Back into this attempt's form, if the upload page offers a kept draft. It's this attempt's if the page offered none before the attempt began, and the form comes back with its exact title. Returns whether it got back in. With nothing on offer the file goes up again, since nothing was ever pressed and there's nothing else to go back to."""
    await page.goto('https://creator.douyin.com/creator-micro/content/upload', wait_until='domcontentloaded', timeout=60000)
    await page.locator('input[type=file]').wait_for(state='attached', timeout=20000)
    if not await douyin_offers_draft(page, DOUYIN_OFFER_SECONDS):
        return False
    if tx.receipt.get('draft_offered_before') is not False:
        raise VpubError('existing_draft_requires_attention', "Douyin's upload page is offering a kept draft that was there before this attempt, so it wasn't opened.", "Continue or discard that draft on Douyin's upload page yourself, then run this again.")
    offer = await unique_visible(page.get_by_text('继续编辑', exact=True), 'Douyin 继续编辑')
    await offer.click(timeout=15000)
    await page.wait_for_url(re.compile(r'/content/(publish|post/video)'), timeout=60000)
    title = page.locator('input[placeholder*="填写作品标题"]')
    await look_until(page, title.input_value, 15000)
    if await title.input_value() != tx.options['title']:
        raise VpubError('wrong_work', "The draft Douyin reopened isn't titled as this attempt is, so nothing was changed.", "Look at it on Douyin's upload page yourself.")
    editor = await unique_visible(page.locator('[contenteditable=true]'), 'Douyin description')
    if douyin_card_words(await editor.inner_text()) != douyin_card_words(caption):
        await douyin_type_caption(page, editor, caption)
    await page.get_by_text('重新上传', exact=True).wait_for(state='visible', timeout=120000)
    tx.save(phase='uploaded', draft_url=page.url)
    return True


async def publish_douyin(page, tx):
    caption = douyin_caption(tx.options['description'], tx.options['tags'])
    if len(caption.encode('utf-16-le')) // 2 > 1000:
        raise VpubError('description_too_long', 'Douyin description plus hashtags exceeds 1000 UTF-16 units. No video uploaded.')
    if tx.resuming and tx.receipt['media_selected'] and await douyin_reopen_kept_draft(page, tx, caption):
        return await finish_douyin_form(page, tx, tx.receipt['before_ids'])
    before = await works(page, 'douyin', tx.identity, must_answer=True)
    same_title = await douyin_cards_titled(page, tx.options['title'], caption)
    # An attempt starting over goes up again, so it's held to a work with these words already being there. A new plan for a repost isn't.
    if tx.resuming and tx.receipt['media_selected'] and same_title:
        raise VpubError('inspect_existing_work', 'Douyin already lists a work with this title and caption, so nothing was uploaded again.', 'Look at it on Douyin, and use vpub verify to read it back.')
    tx.save(before_ids=list(before), expected_text=tx.options['title'], expected_caption=caption, same_title_cards_before=len(same_title))
    if tx.options.get('schedule'):
        stamp = douyin_card_stamp(datetime.fromisoformat(tx.options['schedule'].replace('Z', '+00:00')))
        tx.save(same_time_cards_before=len(await douyin_cards_at(same_title, stamp)))
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
    await douyin_type_caption(page, editor, caption)
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
    return {'at': when.isoformat(), 'local': local.isoformat(), 'stamp': stamp, 'card_stamp': douyin_card_stamp(when), 'date': [local.year, local.month, local.day], 'time': [local.hour, local.minute], 'timezone_basis': 'computer'}


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
        await choose_file(page, area.locator('input.semi-upload-hidden-input'), path)
        from .browser_edits import DOUYIN_COVER_SECONDS, douyin_cover_settles, douyin_cover_stuck, douyin_cover_traffic
        fonts = await douyin_cover_settles(page)
        done = dialog.get_by_role('button', name='完成', exact=True)
        await wait_until(page, lambda: enabled(done), seconds=90, label='Douyin cover ready')
        await risk_check(page)
        stop_check('douyin')
        from patchright.async_api import TimeoutError as BrowserTimeout
        # This window itself, since another one opening could otherwise answer for it.
        window = await dialog.element_handle(timeout=5000)
        traffic = douyin_cover_traffic(page)
        try:
            await done.click()
            # First landscape save may offer a portrait upsell. Close it so the
            # explicit portrait asset below is applied through its own control.
            await page.wait_for_timeout(700)
            later = await visible(page.get_by_role('button', name='暂不设置', exact=True))
            if later:
                await later.click()
            try:
                await window.wait_for_element_state('hidden', timeout=DOUYIN_COVER_SECONDS * 1000)
            except BrowserTimeout:
                # A field run's first real post stopped here on a 30-second wait, as the edit route's window had stayed for over three minutes.
                raise VpubError('cover_not_ready', f"Douyin's cover window was still working {DOUYIN_COVER_SECONDS} seconds after 完成, so nothing was published. {await douyin_cover_stuck(page, fonts, traffic())}", 'vpub resume tries again, and vpub resume KEY --no-cover goes without one.') from None
        finally:
            traffic(stop=True)
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
    if await title.input_value() != tx.options['title'] or douyin_card_words(await editor.inner_text()) != douyin_card_words(caption):
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
    elif address.hostname in {'www.youtube.com', 'youtube.com'} and address.path.startswith('/shorts/'):
        candidate = address.path.removeprefix('/shorts/').rstrip('/')
    else:
        return None
    return candidate if re.fullmatch(r'[\w-]{11}', candidate) else None


async def yt_video_id(page):
    for href in await page.locator(f'{YT_DIALOG} a[href]').evaluate_all('es => es.map(e => e.href)'):
        found = youtube_link_id(href)
        if found:
            return found
    return None


def youtube_link_shape(href):
    """A link with anything that could name a video or a channel taken out, for a record."""
    address = urlsplit(href or '')
    return (address.hostname or '') + re.sub(r'[\w-]{11,}', '<id>', address.path)


async def yt_upload_look(page):
    """What Studio's dialog shows of this upload: its progress, and the shape of its links, with no other words from the page. A field run's ID never came, and nothing could say whether the upload was slow or the link was one this didn't read."""
    from patchright.async_api import Error as BrowserError
    progress = page.locator(f'{YT_DIALOG} ytcp-video-upload-progress')
    try:
        shown = await progress.count()
        found = re.search(r'(\d{1,3})\s*%', await progress.first.inner_text(timeout=2000)) if shown else None
        links = await page.locator(f'{YT_DIALOG} a[href]').evaluate_all('es => es.map(e => e.href)')
        return {'progress_shown': shown, 'percent': int(found[1]) if found else None, 'finished': await yt_upload_finished(page), 'links': sorted({youtube_link_shape(link) for link in links})}
    except BrowserError:
        return {'progress_shown': None, 'percent': None, 'finished': None, 'links': None}


async def yt_reopen_stopped_upload(page, tx):
    """Back into this attempt's own upload dialog, if it stopped before its final Save and left a draft. A field run's first vertical video did, listed under its file's name with a button to edit it. Once a title has gone in, the draft goes by that. Returns whether it got back in. The file goes up again only if this attempt has no video ID and Studio lists nothing of it."""
    from .youtube_studio import drafts_named
    has_id = tx.receipt.get('draft_id')
    rows = await drafts_named(page, tx.identity, {Path(tx.receipt['upload_filename']).stem, tx.options['title']} if has_id else {Path(tx.receipt['upload_filename']).stem})
    if len(rows) > 1:
        raise VpubError('inspect_existing_draft', 'Studio lists more than one draft of this attempt, so nothing was opened or uploaded.', 'Keep the one you want in YouTube Studio, then run vpub resume again.')
    if not rows:
        if has_id:
            raise VpubError('inspect_existing_draft', "Studio lists no draft of this attempt's video, so nothing was opened or uploaded.", 'Look for it in YouTube Studio. Never upload it again.')
        return False
    button = await unique_visible(rows[0].get_by_text(YT_EDIT_DRAFT), 'YouTube Edit draft button')
    await button.click(timeout=15000)
    return True


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


async def yt_set_time(page, clock, wanted):
    """Studio's time box comes with a list of times. A field check typed 14:07: Enter moved it to 15:07, and leaving the box with Tab kept 14:07.
    So it is typed and left, then chosen from the list when the list offers exactly this time, and Enter is the last thing tried. The caller reads back what stayed; this says what the box showed at each step."""
    steps = {}

    async def held(step):
        steps[step] = normalize(await clock.input_value())
        return steps[step] == normalize(wanted)
    await clock.click(timeout=10000)
    await clock.select_text(timeout=5000)
    await clock.press_sequentially(wanted)
    await held('typed')
    await clock.press('Tab')
    await page.wait_for_timeout(1000)
    if await held('after_leaving'):
        return steps
    await clock.click(timeout=10000)
    await page.wait_for_timeout(500)
    offered = page.locator('tp-yt-paper-item:visible, [role="option"]:visible').filter(has_text=re.compile(r'^\s*' + re.escape(wanted) + r'\s*$'))
    steps['listed'] = await offered.count()
    if steps['listed'] == 1:
        await offered.click(timeout=5000)
        await page.wait_for_timeout(1000)
        if await held('from_list'):
            return steps
    await clock.click(timeout=10000)
    await clock.select_text(timeout=5000)
    await clock.press_sequentially(wanted)
    await clock.press('Enter')
    await page.wait_for_timeout(1000)
    await held('after_enter')
    return steps


# A channel left on Studio's default shows "Time zone / Default set to local time zone (GMT+0800)",
# in a collapsed part that inner_text() leaves out. Local there means the browser's own zone.
YT_LOCAL_ZONE = re.compile(r'(?:local time ?zone|本地时区|当地时区|本地時區|當地時區)[^()]*\(\s*(GMT\s*[+\-−]\s*\d{1,2}(?::?\d{2})?)\s*\)', re.I)
BROWSER_ZONE = '() => Intl.DateTimeFormat().resolvedOptions().timeZone'


def studio_zone_names(text):
    """The named zones Studio's scheduler text shows, and whether another zone expression makes it unreadable."""
    names, unreadable = set(), False
    named_zone = r'UTC|[A-Za-z][A-Za-z0-9_+\-]*(?:/[A-Za-z0-9_+\-]+)+'
    for line in text.splitlines():
        line, labelled = re.subn(r'^(?:Time zone|Timezone|时区|時區)\s*:?\s*', '', normalize(line), flags=re.I)
        line = re.sub(r'(?<![\w/])\(?GMT\s*[+\-−]\s*\d{1,2}(?::?\d{2})?\)?', '', line).strip()
        if re.fullmatch(named_zone, line):
            names.add(line)
        elif line and (labelled or re.search(named_zone, line)):
            unreadable = True  # Do not drop a second, ambiguous zone expression.
    return names, unreadable


def yt_schedule_zone(text, when, local_zone=None):
    """Only an explicit zone identifies target-date rules; a GMT offset or abbreviation does not.

    Named-zone lines are a conservative supported display contract, not a claim
    that every live Studio layout exposes one. Unknown layouts stay as drafts.
    local_zone is the browser's own zone, given only when Studio names no zone but says it
    uses the local one; the offset Studio shows must still agree with that zone's rules.
    """
    names, unreadable = studio_zone_names(text)
    if local_zone and not names and not unreadable:
        names = {local_zone}
    if unreadable or len(names) != 1:
        raise VpubError('schedule_unverified', 'YouTube Studio did not identify one explicit time zone. A GMT offset alone cannot establish the target-date time. The upload is kept as a draft; nothing was published.',
                        'Inspect this retained draft and schedule it manually in Studio. Do not upload it again.')
    name = next(iter(names))
    try:
        zone = timezone.utc if name == 'UTC' else ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise VpubError('schedule_unverified', 'The named Studio time zone has no available time-zone rules. The upload is kept as a draft; nothing was published.',
                        'Use a supported installation with time-zone data, or schedule this retained draft manually in Studio.') from exc
    local = when.astimezone(zone)
    # The form cannot choose between two occurrences of a repeated fall-back minute.
    if local.replace(fold=1 - local.fold).utcoffset() != local.utcoffset():
        raise VpubError('schedule_unverified', 'The intended time repeats during a daylight-saving change in Studio\'s named zone. The upload is kept as a draft; nothing was published.')
    # Etc/GMT+5 is an IANA key with the opposite sign, not a displayed GMT+5 offset.
    offsets = studio_offsets(text.replace(name, ''))
    if not studio_zone_offsets_agree(offsets, local, datetime.now(zone)):
        raise VpubError('schedule_unverified', 'Studio\'s displayed offset conflicts with its named time zone. The upload is kept as a draft; nothing was published.')
    return local, name


async def yt_schedule_local(page, when):
    scheduler = await unique_visible(page.locator(f'{YT_DIALOG} ytcp-visibility-scheduler'), 'YouTube schedule time zone')
    shown = await scheduler.inner_text()
    names, unreadable = studio_zone_names(shown)
    if names or unreadable:
        return yt_schedule_zone(shown, when)
    hidden = await scheduler.evaluate('e => e.textContent')
    default = set(YT_LOCAL_ZONE.findall(hidden if isinstance(hidden, str) else ''))
    if len(default) != 1:
        return yt_schedule_zone(shown, when)
    zone = await page.evaluate(BROWSER_ZONE)
    return yt_schedule_zone(f'{shown}\n({next(iter(default))})', when, zone if isinstance(zone, str) else None)


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
    local, zone = await yt_schedule_local(page, when)
    date_style = studio_style(STUDIO_DATES, await day.inner_text())
    time_style = studio_style(STUDIO_TIMES, await clock.input_value())
    if not date_style or not time_style:
        raise VpubError('schedule_unverified', 'YouTube Studio wrote its date or time in a style CrossCast cannot read. The upload is kept as a draft; nothing was published.')
    await day.click(timeout=15000)
    box = await unique_visible(page.locator('ytcp-date-picker tp-yt-paper-input input'), 'YouTube schedule date box')
    await box.fill(date_style[2](local))
    await box.press('Enter')
    await page.wait_for_timeout(1000)
    held_before = await clock.input_value()
    entered = await yt_set_time(page, clock, time_style[2](local))
    shown_day, shown_time = await day.inner_text(), await clock.input_value()
    if studio_read(STUDIO_DATES, shown_day) != (local.year, local.month, local.day) or studio_read(STUDIO_TIMES, shown_time) != (local.hour, local.minute):
        raise VpubError('schedule_unverified', f'The YouTube schedule was not confirmed: Studio shows {normalize(shown_day)} {normalize(shown_time)} for {local.strftime("%Y-%m-%d %H:%M")}. Its time box held {normalize(held_before)} before, then {entered}. The upload is kept as a draft; nothing was published.',
                         "A time on the quarter hour can be chosen from Studio's own list, which has worked on a real channel. Choose one and make a new plan.")
    return {'at': when.isoformat(), 'local': local.isoformat(), 'date': [local.year, local.month, local.day],
            'time': [local.hour, local.minute], 'timezone_basis': 'studio', 'timezone': zone, 'time_entry': entered}


async def yt_done_window(page):
    return await visible(page.locator(YT_DONE_LOCATOR).filter(has_text=YT_DONE_WINDOW))


async def yt_press_answer(page, done):
    """What Studio did with Publish: 'warned', 'closed', 'confirmed', or nothing yet. A field run's Schedule button stayed on
    screen behind Studio's own Video scheduled window for the whole wait, so that window is an answer too."""
    if await visible(page.locator(f'{YT_CHECKS_WARNING} button')):
        return 'warned'
    if await yt_done_window(page):
        return 'confirmed'
    return None if await done.is_visible() else 'closed'


async def yt_checks_finished(page):
    words = await page.locator(f'{YT_DIALOG} .progress-label').evaluate_all('es => es.map(e => e.innerText)')
    return any(YT_CHECKS_DONE.search(word or '') for word in words)


async def yt_back_from_warning(page):
    """Out of Studio's warning without choosing its main button, whose words nobody here has read on a real channel. Public uploaders press Go back there, then wait for the checks and press Publish again."""
    warning = page.locator(YT_CHECKS_WARNING)
    words = normalize(await warning.first.inner_text())
    back = await visible(page.locator(f'{YT_CHECKS_WARNING} button:not(#primary-action-button button):not(#publish-button button)'))
    if back:
        await back.click(timeout=15000)
    else:
        await page.keyboard.press('Escape')

    async def gone():
        return not await visible(page.locator(f'{YT_CHECKS_WARNING} button'))
    if not await look_until(page, gone, 5000):
        raise VpubError('layout_changed', "Studio's warning about its checks would not close, so nothing more was pressed.", 'Finish this draft in YouTube Studio. Do not upload it again.')
    return words


async def yt_uncover(page, refused):
    """What lay over the final button, then one press on Studio's backdrop where nothing else is, which closes the window on top
    the way a person's click beside it would. A field run's Schedule button was covered by that backdrop three times running.
    Only a backdrop named as what refused the press is pressed: under the upload window alone, a press there would close the upload window."""
    from patchright.async_api import Error as BrowserError
    if 'in the way: tp-yt-iron-overlay-backdrop' not in refused:
        return None
    on_top = await page.evaluate(YT_ON_TOP)
    try:
        await page.locator('tp-yt-iron-overlay-backdrop.opened').first.click(position={'x': 2, 'y': 2}, timeout=5000)
    except BrowserError:
        return {'on_top': on_top, 'pressed_backdrop': False}
    await page.wait_for_timeout(1000)
    return {'on_top': on_top, 'pressed_backdrop': True}


async def yt_press_publish(page, tx, done):
    """Press once, let Studio settle, then read this video's own page.

    A field run published successfully but timed out waiting for the old Save
    after a checks warning. Neither that button nor a warning proves a retry
    is safe. Only the independent read-back decides what the press achieved.
    """
    from patchright.async_api import Error as BrowserError
    from .youtube_studio import PIN_TICKS, act_and_settle
    # An explicit resume may already have independently proved this is a draft.
    again = bool(tx.resuming and tx.receipt.get('still_a_draft') and tx.receipt['final_submit_attempted'])

    async def schedule_still_holds():
        when = datetime.fromisoformat(tx.options['schedule'].replace('Z', '+00:00'))
        local, zone = await yt_schedule_local(page, when)
        recorded = tx.receipt.get('schedule') or {}
        day = await unique_visible(page.locator(f'{YT_DIALOG} #datepicker-trigger'), 'YouTube schedule date')
        clock = await unique_visible(page.locator(f'{YT_DIALOG} #time-of-day-container input'), 'YouTube schedule time')
        if (recorded.get('at') != when.isoformat() or recorded.get('local') != local.isoformat() or recorded.get('timezone') != zone
                or studio_read(STUDIO_DATES, await day.inner_text()) != (local.year, local.month, local.day)
                or studio_read(STUDIO_TIMES, await clock.input_value()) != (local.hour, local.minute)):
            raise VpubError('schedule_unverified', 'The Studio schedule or time zone changed before Save. The upload is kept as a draft; nothing was published.')

    await act_and_settle(page, lambda: tx.submit(page, done, again=again, uncover=lambda refused: yt_uncover(page, refused),
                                               still_holds=schedule_still_holds if tx.options.get('schedule') else None), PIN_TICKS)
    answer = None
    for tick in range(YT_PRESS_ANSWER_SECONDS * 2 + 1):
        stop_check('youtube')
        await risk_check(page)
        try:
            answer = await yt_press_answer(page, done)
        except BrowserError:
            answer = None  # A transitioning control is not publication evidence.
        if answer:
            break
        if tick < YT_PRESS_ANSWER_SECONDS * 2:
            await page.wait_for_timeout(500)
    stop_check('youtube')
    if answer == 'warned':
        tx.save(checks_warning='Studio showed a checks warning after Save. No second final press was made; read back the recorded video.')
    elif answer == 'confirmed':
        # Its first two lines, such as Video scheduled and when it goes public, which a read-back can compare with the plan.
        window = await yt_done_window(page)
        lines = [line.strip() for line in (await window.inner_text()).splitlines() if line.strip()][:2] if window else []
        tx.save(studio_confirmation=' '.join(lines)[:200])
    elif not answer:
        tx.save(after_press=await page.evaluate(YT_OPEN_DIALOGS))


async def yt_finish_draft(page, tx):
    """Back into this attempt's own draft and press Publish again, after a read-back showed the last press left a draft. It goes by the video's ID, changes no field, and uploads nothing."""
    video_id, options = tx.receipt['id'], tx.options
    await page.goto(f'https://studio.youtube.com/video/{video_id}/edit', wait_until='domcontentloaded', timeout=60000)
    button = await look_until(page, lambda: visible(page.get_by_text(YT_EDIT_DRAFT)), 30000)
    if not button:
        raise VpubError('inspect_existing_draft', "Studio didn't offer to edit this attempt's draft, so nothing was pressed.", 'Look for it in YouTube Studio. Never upload it again.')
    await button.click(timeout=15000)
    title_box = page.locator(f'{YT_DIALOG} #title-textarea #textbox')
    await wait_until(page, lambda: visible(title_box), seconds=60, label='YouTube details form')
    if await wait_until(page, lambda: yt_video_id(page), seconds=60, label='YouTube video ID') != video_id:
        raise VpubError('wrong_work', "The draft Studio opened has another video ID from this attempt's, so nothing was changed.")
    title = await unique_visible(title_box, 'YouTube title')
    description = await unique_visible(page.locator(f'{YT_DIALOG} #description-textarea #textbox'), 'YouTube description')
    if normalize(await title.inner_text()) != normalize(options['title']) or normalize(await description.inner_text()) != normalize(options['description']):
        raise VpubError('metadata_mismatch', "This draft's title or description differs from the plan, so nothing was pressed.", 'Look at it in YouTube Studio. Never upload it again.')
    return await yt_final_steps(page, tx, video_id, True)


async def yt_upload_finished(page, reopened=False):
    # The upload stops when the page closes, so it has to finish before this command can leave.
    progress = page.locator(f'{YT_DIALOG} ytcp-video-upload-progress')
    if await progress.count():
        return await progress.first.get_attribute('uploading') is None
    # This page isn't uploading a reopened draft. A field read of one found its progress bars at 100, or with no value at all.
    return reopened and await page.locator(f'{YT_DIALOG} [role="progressbar"]').evaluate_all("es => es.every(e => [null, '100'].includes(e.getAttribute('aria-valuenow')))")


async def publish_youtube(page, tx):
    """One upload through Studio's own form. Studio shows the video's ID as soon as the upload starts, so it is saved first."""
    options = tx.options
    if tx.receipt.get('still_a_draft'):
        return await yt_finish_draft(page, tx)
    tx.save(expected_text=options['description'], visibility=options['visibility'])
    if options.get('create_missing_playlists'):
        from .youtube_studio import ensure_playlists
        tx.save(created_playlists=await ensure_playlists(page, tx.identity, options['playlist_titles']))
    reopened = tx.resuming and tx.receipt['media_selected'] and await yt_reopen_stopped_upload(page, tx)
    if not reopened:
        await page.goto(f'https://studio.youtube.com/channel/{tx.identity}/videos/upload?d=ud', wait_until='domcontentloaded', timeout=60000)
        picker = page.locator('ytcp-uploads-file-picker input[type="file"]')
        await wait_until(page, picker.count, seconds=60, label='YouTube upload dialog')
        await choose_file(page, picker.first, tx.media())
    tx.save(phase='uploading')
    title_box = page.locator(f'{YT_DIALOG} #title-textarea #textbox')
    await wait_until(page, lambda: visible(title_box), seconds=180, label='YouTube details form')
    # Nobody has timed this on a large file, so it gets longer than a small clip needed, and a stop says what the dialog did show.
    try:
        video_id = await wait_until(page, lambda: yt_video_id(page), seconds=YT_LINK_SECONDS, label='YouTube video ID')
    except VpubError as failure:
        if failure.code != 'processing_timeout':
            raise
        raise VpubError(failure.code, f'{failure.message} Last look: {await yt_upload_look(page)}.', failure.hint) from None
    if tx.receipt.get('draft_id') not in (None, video_id):
        raise VpubError('wrong_work', "The draft Studio opened has another video ID from this attempt's, so nothing was changed.")
    tx.save(phase='editing', draft_id=video_id, draft_url=f'https://studio.youtube.com/video/{video_id}/edit')
    title = await unique_visible(title_box, 'YouTube title')
    await title.fill(options['title'])
    description = await unique_visible(page.locator(f'{YT_DIALOG} #description-textarea #textbox'), 'YouTube description')
    await description.fill(options['description'])
    if normalize(await title.inner_text()) != normalize(options['title']) or normalize(await description.inner_text()) != normalize(options['description']):
        raise VpubError('metadata_mismatch', 'YouTube title or description differs from the manifest.')
    if tx.cover():
        if not await look_until(page, page.locator(f'{YT_DIALOG} input#file-loader').count, 5000):
            raise VpubError('cover_unavailable', 'YouTube offers no custom thumbnail on this channel yet. The upload is kept as a draft; nothing was published.',
                            'Custom thumbnails need a verified channel. Verify it in YouTube Studio, or plan without a cover.')
        await choose_file(page, page.locator(f'{YT_DIALOG} input#file-loader').first, tx.cover())
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

        async def tags_shown():
            return await page.locator(f'{YT_DIALOG} #tags-container ytcp-chip').count() == len(set(options['tags']))
        if not await look_until(page, tags_shown, 3000):
            raise VpubError('metadata_mismatch', 'YouTube tags differ from the manifest.')
    await yt_category(page, options['category_id'])
    if options.get('playlist_titles'):
        await yt_playlists(page, options['playlist_titles'])
        tx.save(playlists=options['playlist_titles'])
    tx.save(phase='details_ready', metadata_readback=True, ai_disclosure='native_control')
    return await yt_final_steps(page, tx, video_id, reopened)


async def yt_final_steps(page, tx, video_id, reopened):
    options = tx.options
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
    await wait_until(page, lambda: yt_upload_finished(page, reopened), seconds=YT_UPLOAD_SECONDS, label='YouTube upload')
    tx.save(phase='preview', review_url=page.url)
    if options.get('submission_mode') == 'draft_only':
        return tx.save(state='PREVIEW_READY', note='Uploaded as an unpublished draft in YouTube Studio; nothing was published.')
    done = await unique_visible(page.locator(f'{YT_DIALOG} #done-button'), 'YouTube final Save')
    try:
        await wait_until(page, lambda: yt_button_ready(done), seconds=YT_SAVE_SECONDS, label='YouTube final Save enabled')
    except VpubError as failure:
        if failure.code != 'processing_timeout':
            raise
        raise VpubError(failure.code, f'{failure.message} The upload is an unsaved Studio draft ({video_id}) that Studio was still processing; nothing was saved or published.',
                        'Open that draft in YouTube Studio and finish or delete it there. Never upload this video again for this attempt.') from None
    tx.save(id=video_id, url=work_url('youtube', tx.identity, video_id))
    # On a real channel Studio greyed a Save out at once and sent the change about six seconds later, and leaving before it ended cancelled it. The read-back below leaves this page.
    await yt_press_publish(page, tx, done)
    result = {}
    for attempt in range(8):
        stop_check('youtube')
        result = await verify(page, 'youtube', tx.identity, tx.receipt)
        if result['state'] == 'SUBMISSION_VERIFIED':
            break
        await asyncio.sleep(5)
    return tx.save(**result)
