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
from .core import DOUYIN_TOO_LONG, VpubError, douyin_caption, douyin_caption_units, stop_check
from .browser_runtime import failure_message, look_until, risk_check, unique_visible, visible
from .progress import stage
from .browser_backends import (STUDIO_DATES, STUDIO_TIMES, X_CHOSEN_TIME, YT_DONE_WINDOW, YT_EDIT_DRAFT, YT_ZH_SCHEDULE_HEADING, choose_file, choose_in_picker, normalize, studio_offsets, studio_read, studio_style, studio_zone_offsets_agree,
                               upload_file, wait_until, work_url, works, verify, verify_scheduled_x, x_chosen_time_says, x_new_works, x_new_posts, x_schedule_baseline, x_schedule_moment, douyin_card_stamp, douyin_card_status, douyin_new_works, douyin_cards_at, douyin_cards_titled, douyin_form_differs, douyin_leave_caption, douyin_scheduled_card, douyin_type_caption, DOUYIN_SCHEDULED)

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
# What Library's tile says while its file is still going up. A field run saw "Loading 73%" thirty seconds into a 61 MB upload.
X_LOADING = re.compile(r'Loading\s*(\d{1,3})(?:[.,]\d+)?\s*%', re.I)
# A number that hasn't moved for this long is not taken for an upload under way, so it no longer holds a click back.
X_LOADING_STALL_SECONDS = 120
# How much longer the wait goes on each time, while a number moved during the last wait. A pause shorter than this in a real upload doesn't end it.
X_LOADING_WAIT_SECONDS = 300
# How long no moving number must have shown before the first click: one look that missed it isn't enough.
X_LOADING_CLEAR_SECONDS = 3
X_LIBRARY_LOOK_SECONDS = 60
# What a wait leaves of its command's time, so it ends with its own answer rather than being cut off with the browser.
X_COMMAND_MARGIN_SECONDS = 120


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


async def x_files_going_up(page):
    """The percentages below 100 that Library's tiles show for files still going up, lowest first: none, or None when the page couldn't be read.
    A field run's new tile said "Loading 73%" thirty seconds into a 61 MB upload: the file got a page of its own only at 100%, and the browser closing before then lost the upload.
    The text is what the match was made on, and a review found a label shown in capitals or with a decimal would otherwise be missed."""
    from patchright.async_api import Error as BrowserError
    try:
        said = [await item.text_content(timeout=5000) or '' for item in await page.get_by_text(X_LOADING).all() if await item.is_visible()]
    except BrowserError:
        return None  # The page is still changing, and the next look says what it became.
    return sorted(number for text in said if (found := X_LOADING.search(text)) and (number := int(found[1])) < 100)


async def x_upload_was_lost(page, tx):
    """What a look found when a resumed attempt's own file never reached Library, so choosing it again is the only way on; None when it is there or can't have been lost.
    A field run's browser closed at "Loading 73%" on a 61 MB file. Its receipt said a file was chosen, but Media Studio had given the file no page of its own and Library held no file of its name, so every resume looked for that name and never found it.
    Only an attempt that chose its own file and never got its form open can have lost it. A reused clip, or a file whose form has shown, is in Library, and choosing that again would make two tiles of one name, which get no click.
    From a review: Library has been seen not to load its list while its Upload Media button worked, so one short look could take a slow list for a lost file. The look lasts X_LIBRARY_LOOK_SECONDS, and Library is loaded afresh once and looked at again before the file is called lost."""
    if tx.receipt.get('reused_media') or tx.options.get('existing_media_url') or 'open_look' in tx.receipt:
        return None
    name = page.get_by_text(tx.receipt['upload_filename'], exact=True)

    async def shows():
        return bool(await x_names_showing(name))
    for again in (False, True):
        if again:
            await page.reload(wait_until='domcontentloaded', timeout=60000)
            upload = page.locator('button').filter(has_text=re.compile(r'^\s*Upload Media\s*$')).first

            async def back():
                return await upload.is_visible()
            # From a review: a check shown by the reloaded page must stop everything as one shown before it does, and the page the file goes up from must be this account's.
            try:
                await wait_until(page, back, seconds=30, label="Library loaded again")
            except VpubError as failure:
                if failure.code != 'processing_timeout':
                    raise
                raise VpubError('layout_changed', "Library didn't come back after it was loaded again, so nothing was uploaded.", 'Run vpub resume again.') from None
            hrefs = await page.locator('a[href*="analytics.twitter.com/user/"]').evaluate_all('es => es.map(e=>e.href)')
            if not any('/user/' + tx.identity.casefold() + '/' in h.casefold() for h in hrefs):
                raise VpubError('wrong_account', 'Media Studio account does not match the signed-in publishing account.')
        try:
            await wait_until(page, shows, seconds=X_LIBRARY_LOOK_SECONDS, label="this attempt's file in Library")
            return None
        except VpubError as failure:
            if failure.code != 'processing_timeout':
                raise
    # A count only, for whoever reads the receipt: other files of CrossCast's showing would say Library's list did load.
    from patchright.async_api import Error as BrowserError
    try:
        others = len([item for item in await page.get_by_text(re.compile(r'^crosscast-[0-9a-f]{16}\.\w+$')).all() if await item.is_visible()])
    except BrowserError:
        others = None
    return {'looked_seconds': 2 * X_LIBRARY_LOOK_SECONDS, 'reloaded': True, 'other_crosscast_files_showing': others}


async def x_open_media(page, name, form, filename, hold=0, may_upload_again=True):
    """Field runs clicked the uploaded file's name as soon as it showed, and the address didn't change in 30 seconds, while 15 seconds on the name showed twice. A click two minutes after an upload opened the form at once.
    So the file is open once its own form shows, and the address is not waited for. A click changes nothing, and comes again only while no form has come.
    Two field runs in a row clicked five seconds in and got the file's details instead: this file's link and its title box, still on /library, with no Change thumbnail anywhere for ten minutes, while a fresh page's click reached the form. So once those details show, the click comes again on the name out among the tiles. A click can't cut off an upload the way leaving the page could.
    The next field run clicked 36 times on a 113 MB file's tile and got no further than those details. So a new upload's first click waits as well, which is the `hold`: a guess at when the upload ends.
    A later field run saw the tile say it: "Loading 73%" thirty seconds into a 61 MB upload, with the file's page given only at 100%. So no click comes while a tile's number keeps moving, nor for X_LOADING_CLEAR_SECONDS after, and while it moves the wait goes on past its usual end, X_LOADING_STALL_SECONDS at a time.
    From a review: a number that stays put, at 100 or anywhere, may not be this upload at all, so after X_LOADING_STALL_SECONDS it no longer holds the click back. Nor can the wait run on into the command's own cut-off, which would close the browser mid-upload with nothing recorded."""
    from patchright.async_api import Error as BrowserError
    from . import browser_runtime
    clicked = None
    began = time.monotonic()
    seen = {'clicks': 0}
    loading = {'last': None, 'moved_at': None, 'fresh_at': None, 'shown_at': None}
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
        going = await x_files_going_up(page)
        now = time.monotonic()
        if going:
            if loading['last'] is None:
                loading['moved_at'] = now  # A number just seen is taken to be moving until it shows otherwise.
            elif going != loading['last']:
                loading['moved_at'] = now
                seen['loading_moves'] = seen.get('loading_moves', 0) + 1
            loading['last'], loading['shown_at'] = going, now
            if now - loading['moved_at'] < X_LOADING_STALL_SECONDS:
                loading['fresh_at'] = now
        if going is not None and loading['last'] is not None:
            seen['loading'] = going
        # A look that couldn't read the page says nothing, so it doesn't click either.
        held = going is None or (loading['fresh_at'] is not None and now - loading['fresh_at'] < X_LOADING_CLEAR_SECONDS)
        due = not held and now - began >= hold and (clicked is None or now - clicked >= X_OPEN_SECONDS)
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
    after = ("vpub resume looks again on a fresh page, and uploads the file again only if Library shows no file of its name." if may_upload_again
             else "This file's form has shown before, so vpub resume looks again on a fresh page but never uploads it again: look for it in Media Studio.")
    seconds = hold + 600
    while True:
        end = browser_runtime.command_deadline
        cut = end is not None and time.monotonic() + seconds > end - X_COMMAND_MARGIN_SECONDS
        if cut:
            seconds = max(1, end - X_COMMAND_MARGIN_SECONDS - time.monotonic())
        moves = seen.get('loading_moves', 0)
        try:
            await wait_until(page, opened, seconds=seconds, label="the uploaded file's form in Media Studio")
            return seen  # So a run that gets through can show whether a second click was what did it.
        except VpubError as failure:
            if failure.code != 'processing_timeout':
                raise
            # Only a number that moved during this wait, and showed at its end or just before, is an upload still under way. From a review: one that ended
            # in the wait's last seconds had its click still held back, and stopping then closed the browser as the upload finished.
            going_on = (seen.get('loading_moves', 0) != moves and loading['shown_at'] is not None
                        and time.monotonic() - loading['shown_at'] < X_LOADING_CLEAR_SECONDS + X_OPEN_SECONDS)
            if going_on and cut:
                raise VpubError('upload_too_slow', f"Media Studio was still taking the file when this command's time ran out, so the browser closes and the upload is lost. Last look: {seen}. "
                                "On this connection the file can't go up within one command, and vpub resume uploads it again from the start, so run it when the connection is faster.",
                                "Run vpub resume when the connection is faster; it uploads the file again from the start.") from None
            if not going_on:
                # A field run ended here with nothing to say which of several things had happened. These are counts, with no words from the page.
                # A fresh page then opened the same file with one click, which is what a resume does.
                raise VpubError(failure.code, f"{failure.message} Last look: {seen}. {after}", failure.hint) from None
        stop_check('x')
        seconds = X_LOADING_WAIT_SECONDS  # The file was still going up and getting further, and leaving now would lose it.


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

    hold, lost = 0, None
    if tx.receipt.get('media_url'):
        if not re.fullmatch(r'https://studio\.x\.com/library/\d+_\d+/edit', tx.receipt['media_url']):
            raise VpubError('invalid_media_url', 'Resume requires the original Media Studio asset URL.')
        await page.goto(tx.receipt['media_url'], wait_until='domcontentloaded', timeout=60000)
        # From a review: a page that went on to another file would have overwritten this attempt's address.
        still_same_media()
    elif not tx.receipt['media_selected'] or (lost := await x_upload_was_lost(page, tx)):
        media = tx.media()
        await upload_file(page, media, accept='video/')
        # Counted once the file has gone up again: a stop or a check before then sends nothing.
        tx.save(phase='uploading', **({'lost_uploads': tx.receipt.get('lost_uploads', 0) + 1, 'lost_look': lost} if lost else {}))
        hold = x_first_click_hold(Path(media).stat().st_size)
    filename = tx.receipt['upload_filename']
    form = x_media_form(page, filename)
    change = form.locator('button:visible').filter(has_text=X_CHANGE_THUMBNAIL)
    if not tx.receipt.get('media_url'):
        look = await x_open_media(page, page.get_by_text(filename, exact=True), form, filename, hold, may_upload_again='open_look' not in tx.receipt)
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
    await page.get_by_text('重新上传', exact=True).wait_for(state='visible', timeout=120000)
    tx.save(phase='uploaded', draft_url=page.url)
    # From a review of setting covers first: the words a draft lost are typed again by publish_douyin, after any cover still to be set.
    return True


async def publish_douyin(page, tx):
    caption = douyin_caption(tx.options['description'], tx.options['tags'])
    if douyin_caption_units(caption) > 1000:
        raise VpubError('description_too_long', DOUYIN_TOO_LONG + ' No video uploaded.')
    if tx.resuming and tx.receipt['media_selected'] and await douyin_reopen_kept_draft(page, tx, caption):
        # From a review of setting covers first: a cover step that stopped on a form just uploaded left its draft with no words, and the
        # resume typed them before the covers, the order that buried 肥鱼抢麦's slot. So a kept draft gets its covers first too, with its box
        # emptied while one is still to be set, and its words after; they are read back before 发布 as ever.
        editor = await unique_visible(page.locator('[contenteditable=true]'), 'Douyin description')
        if douyin_covers_to_set(tx) and await editor.inner_text():
            await douyin_empty_caption(tx, editor)
        await douyin_set_covers(page, tx, editor)
        await douyin_words_in(page, tx, caption)
        return await finish_douyin_form(page, tx, tx.receipt['before_ids'], covers=False)
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
    # From a review of setting covers first: a form just uploaded holds none of the covers an earlier run set on another form, and its
    # receipt said they were on, so this form and a later resume from its kept draft went without them. Their records go with that form.
    for key in ('landscape_cover', 'portrait_cover'):
        tx.receipt.pop(key, None)
    tx.save(phase='uploading')
    await page.wait_for_url(re.compile(r'/content/(publish|post/video)'), timeout=120000)
    tx.save(phase='editing', draft_url=page.url)
    title = page.locator('input[placeholder*="填写作品标题"]')
    await title.fill(tx.options['title'])
    await page.get_by_text('重新上传', exact=True).wait_for(state='visible', timeout=900000)
    tx.save(phase='uploaded', description_typed=False)
    # From field runs on a16202b and fc60a30: once 肥鱼抢麦's long description was in, what lay over its 竖封面3:4 slot came from the
    # description's own wrapper, and at every point of the slot's grid something else was on top, so no press could go to it. So on a form
    # just uploaded the covers go on first, while the description is still empty, and the words go in after them. From a review of that:
    # the title, filled last, still held the cursor, so it leaves first, and the description box is found once the upload is done.
    await page.evaluate('() => { if (document.activeElement && document.activeElement !== document.body) document.activeElement.blur(); }')
    editor = await unique_visible(page.locator('[contenteditable=true]'), 'Douyin description')
    await douyin_set_covers(page, tx, editor)
    await douyin_words_in(page, tx, caption)
    return await finish_douyin_form(page, tx, before, covers=False)


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


DOUYIN_COVERS = (('landscape', '横封面4:3'), ('portrait', '竖封面3:4'))
DOUYIN_SLOT_MS = 10000  # As long as the press a field run's covered 横封面4:3 slot ran out on.
# What a trial press says of a slot that something lies over or that is turned off, as with a field run's 9:16 video's 横封面4:3 slot.
DOUYIN_SLOT_OFF = re.compile(r'intercepts pointer events|element is not enabled')
DOUYIN_WINDOW_MS = 20000  # How long a press on a slot waits for Douyin's cover window to open.
# From a final check of setting covers first: the description goes in after the covers, so a draft Douyin kept from a stop here may hold
# no words yet, and one finished by hand would go out without them.
DOUYIN_COVER_AGAIN = ('vpub resume tries again, and vpub resume KEY --no-cover goes without one. The description goes in after the covers, '
                      "so Douyin's kept draft may not hold it yet: finish the draft with vpub resume, not by hand.")
# From field runs on 647e002 and 7fa6c10: something lay over a cover slot in three of the last four Douyin posts, a span twice and a div twice,
# the second div over a reopened draft's 竖封面3:4 slot on both tries. Nothing else with buttons was open, and no more than its tag was kept.
# So a slot something lies over is looked at again for this long, with nothing pressed, for a layer Douyin shows while it readies the cover
# area, a list that opened late or a toast to go: a second apart, and from the final check of 073cf63, half a second more for the pointer
# to settle away from the slot before each look, so about every one and a half seconds.
DOUYIN_COVERED_SECONDS = 90
DOUYIN_COVERED_LOOK_MS = 1000
# From round 1's final check and an offline probe of this driver: a press, a trial press too, moves the pointer onto its point, where what the
# page shows only under the pointer then comes up and takes the press, and every look that decided a press had been made with the pointer
# somewhere else. So the pointer goes to a press's point first, and what it brings up gets this long: a fixed wait, since a window that isn't
# drawing may never run an animation frame. The probe also found that what came up under a pointer held still isn't hovered itself until the
# pointer moves again, so a mask that came with the pointer brought up an icon of its own only at the press. The pointer is put on the same
# point again, up to DOUYIN_AIM_MOVES times, until what is on top there is what it hovers; the probe's deepest case took three.
DOUYIN_SETTLE_MS = 500
DOUYIN_AIM_MOVES = 4
# Where the pointer rests for a look with it away from the slot. From a review of a490e80: the window's top-left corner, where round 2 had it,
# hovered whatever lay there, a header's logo or a menu that could open over the slot, and an offline probe of this driver found that a point
# just outside the page hovers nothing.
DOUYIN_POINTER_AWAY = (-1, -1)
DOUYIN_SPOT_TRIES = 4  # How many spots of a slot, or of a bare part of its cover box, are aimed at and looked at before none is taken to hold.
# The point of the window a Playwright press on an element goes to, worked out as Playwright works it out: the middle of the part of its box
# inside the window, or the offset at from its padding box, rounded down to hundredths. The aim and the look both use it, so the pointer is
# put where the press will go and the look sees what is there. The probe found a press made after this aim moved the pointer nowhere.
DOUYIN_POINT = r"""(e, at) => {
  const r = e.getBoundingClientRect(), cut = n => (n * 100 | 0) / 100;
  if (at) { const s = getComputedStyle(e);
    return {x: cut(r.left + (parseInt(s.borderLeftWidth, 10) || 0) + at.x), y: cut(r.top + (parseInt(s.borderTopWidth, 10) || 0) + at.y)}; }
  const x0 = Math.min(Math.max(r.left, 0), innerWidth), x1 = Math.min(Math.max(r.right, 0), innerWidth);
  const y0 = Math.min(Math.max(r.top, 0), innerHeight), y1 = Math.min(Math.max(r.bottom, 0), innerHeight);
  let x = 0, y = 0;
  for (const v of [x0, x1, x1, x0]) x += v / 4;
  for (const v of [y0, y0, y1, y1]) y += v / 4;
  return {x: cut(x), y: cut(y)};
}"""
# Where the pointer goes for a press on an element, whether the element shows at all, and whether all of it lies in the window, as a press
# would otherwise scroll it there first.
DOUYIN_AIM = r"""(e, at) => { const point = """ + DOUYIN_POINT + r""", r = e.getBoundingClientRect();
  return {...point(e, at), shown: r.width > 0 && r.height > 0 && getComputedStyle(e).visibility === 'visible',
    in_window: r.left >= 0 && r.top >= 0 && r.right <= innerWidth && r.bottom <= innerHeight}; }"""
# Whether what is on top at the pointer's point is what the pointer hovers, so that a press made there now brings up nothing more.
DOUYIN_SETTLED = r"""p => { const top = document.elementFromPoint(p.x, p.y); return !!top && top.matches(':hover') && !top.querySelector(':hover'); }"""
# Whether nothing on the page is hovered, as with the pointer off the page.
DOUYIN_UNHOVERED = "() => !document.querySelector(':hover')"
# What a look at a slot and the guard on its press both tell apart, from inside the slot: what a press on it goes to (the slot, or the button
# or link that holds it), its own cover box, whether something is a control or lies in one below what holds the slot, since a press on the
# slot reaches those holders anyway, a bare part of that box a press may go through (inside it, as big as the slot, not holding it, no
# control and in none), and what a press at the slot's centre would reach beneath anything over it.
DOUYIN_SLOT_PARTS = r"""(slot, point) => {
  const r = slot.getBoundingClientRect(), root = slot.getRootNode().elementFromPoint ? slot.getRootNode() : document;
  const target = slot.matches('input, textarea, select') || slot.isContentEditable ? slot : slot.closest('button, [role=button], a, [role=link]') || slot;
  const round = n => Math.round(n * 100) / 100;
  const share = (e, x0, y0, x1, y1) => { const b = e.getBoundingClientRect(), whole = (x1 - x0) * (y1 - y0);
    const part = Math.max(0, Math.min(b.right, x1) - Math.max(b.left, x0)) * Math.max(0, Math.min(b.bottom, y1) - Math.max(b.top, y0));
    return whole > 0 ? round(part / whole) : 0; };
  const tokens = e => (e.getAttribute('role') || '').toLowerCase().split(/\s+/).filter(Boolean);
  const controls = /^(button|link|checkbox|radio|switch|menuitem|menuitemcheckbox|menuitemradio|option|tab|combobox|textbox|searchbox|slider|spinbutton|treeitem)$/;
  const control = e => e.matches('button, a, input, select, textarea, label, summary, iframe, object, embed, video[controls], audio[controls], [contenteditable]:not([contenteditable="false" i])')
    || (e.hasAttribute('tabindex') && e.tabIndex >= 0) || tokens(e).some(t => controls.test(t));
  const inControl = e => { for (let n = e; n && !n.contains(slot); n = n.parentElement) if (control(n)) return true; return false; };
  const box = slot.closest('[class^="coverControl-"]');
  const bare = e => !!e && !!box && box.contains(e) && !e.contains(slot) && !inControl(e) && share(e, r.left, r.top, r.right, r.bottom) >= 0.99;
  const reach = () => { const c = point(slot, null); return root.elementsFromPoint(c.x, c.y).find(e => target.contains(e)); };
  // Whether a press at a spot where e is on top goes to the slot: e holds what a press at the centre would reach beneath anything over it, or,
  // from a field run on a16202b, where something of the description's lay over the slot's centre and stayed and no spot was pressed though
  // most of the slot lay clear, e is the slot's own picture: an img, picture, canvas, svg or video with no controls, or anything showing a
  // background image, no control and in none, over at least half of the slot, whose box holds the slot's centre with a pixel to spare on
  // each side, so not half of a card even where a fractional edge meets the centre as it is rounded. From a review of that: nothing in the
  // markup shows a handler a script put on such a part, so the guard holds a press to that very part.
  const picture = e => { const b = e.getBoundingClientRect(), c = point(slot, null);
    return (e.matches('img, picture, canvas, svg, video:not([controls])') || /url\(/.test(getComputedStyle(e).backgroundImage || ''))
      && b.left + 1 < c.x && c.x < b.right - 1 && b.top + 1 < c.y && c.y < b.bottom - 1; };
  const own = (e, under) => !!e && target.contains(e) && ((!!under && e.contains(under))
    || (!inControl(e) && share(e, r.left, r.top, r.right, r.bottom) >= 0.5 && picture(e)));
  return {r, root, target, round, share, tokens, inControl, box, bare, reach, own, picture};
}"""
DOUYIN_PREVIEW_SECONDS = 15
DOUYIN_SAVED_PREVIEW = """slot => {
  const {r, share, inControl, picture} = (""" + DOUYIN_SLOT_PARTS + ")(slot, " + DOUYIN_POINT + """ );
  return [slot, ...slot.querySelectorAll('*')].filter(e => !inControl(e)
    && share(e, r.left, r.top, r.right, r.bottom) >= 0.5 && picture(e)).map(e => {
      let drawn = '';
      if (e.tagName === 'CANVAS') { try { drawn = e.toDataURL(); } catch { drawn = 'unreadable'; } }
      if (e.localName === 'svg') drawn = e.outerHTML;
      return JSON.stringify([e.tagName, e.currentSrc || e.getAttribute('src') || '',
        getComputedStyle(e).backgroundImage, drawn]);
    }).sort();
}"""


async def douyin_wait_saved_preview(page, slot, before):
    async def saved():
        after = await slot.evaluate(DOUYIN_SAVED_PREVIEW, timeout=DOUYIN_SLOT_MS)
        return bool(after) and after != before
    try:
        await wait_until(page, saved, seconds=DOUYIN_PREVIEW_SECONDS, label='Douyin saved cover preview')
    except VpubError as exc:
        if exc.code == 'processing_timeout':
            raise VpubError('cover_unverified', 'Cover editor closed without a changed cover preview.', DOUYIN_COVER_AGAIN) from None
        raise

# What lies over a slot, seen from inside it without pressing anything: at the slot's centre, where Playwright presses, and on a grid over
# its box, as offsets from its padding box, which a press's position is measured from, nearest the centre first. From a review, the centre
# is open as Playwright's own check has it: where the slot, or the button or link that holds it, or something inside either is on top. And
# a spot where anything inside the slot was on top took a press on a button in its corner, so a spot is open only where what is on top
# holds what a press at the centre would reach beneath anything over it: a press there reaches nothing one at the centre wouldn't. From the
# field run on a16202b, a spot is also open on the slot's own picture (own in DOUYIN_SLOT_PARTS). A press there may reach a handler a
# script put on the picture, which no markup shows, so that promise doesn't hold for it; the press is held to that very part.
# What is on top at the centre is told by its tag, a few class tokens of code, a role from a fixed list, its style, how much it covers, where
# it sits, whether it holds the slot, which then takes no pointer events there, and whether it is a control or lies in one below what holds
# the slot, since a press on the slot reaches those holders anyway; and the same for the outermost thing holding it that doesn't hold the
# slot. Never by its words or any other attribute: a field run's list showed other people's names. From round 1's final check, embedded
# frames, objects and players with controls count as controls too. Asked with through, it also gives handles on the parts of the cover box a
# press may go through (douyin_through_press); with at, whether that spot of the slot holds; and with layer and on, whether that part of the
# cover box is itself on top at that point of its own. Each of those is asked with the pointer already on the point it decides.
DOUYIN_SLOT_LOOK = r"""(slot, ask) => {
  ask = ask || {};
  const point = """ + DOUYIN_POINT + r""";
  const {r, root, target, round, share, tokens, inControl, box, bare, reach, own} = (""" + DOUYIN_SLOT_PARTS + r""")(slot, point);
  const W = innerWidth, H = innerHeight, fits = (value, shape) => shape.test(value) ? value : 'other';
  const roles = /^(alert|alertdialog|banner|button|checkbox|combobox|dialog|group|img|link|list|listbox|listitem|log|menu|menubar|menuitem|menuitemcheckbox|menuitemradio|none|note|option|presentation|progressbar|radio|region|searchbox|slider|spinbutton|status|switch|tab|tablist|tabpanel|textbox|timer|toolbar|tooltip|tree|treeitem)$/;
  const roleOf = e => tokens(e).length ? tokens(e).find(t => roles.test(t)) || 'other' : null;
  // Where something lies, in whole pixels: left, top, width and height. From a review of the field run on fc60a30, what lay over the slot
  // and what held it are given so too.
  const rect = e => { const b = e.getBoundingClientRect(); return [b.left, b.top, b.width, b.height].map(n => Math.round(n) + 0); };
  const facts = e => { const s = getComputedStyle(e);
    return {tag: fits(e.tagName.toLowerCase(), /^[a-z][a-z0-9-]{0,40}$/), classes: [...e.classList].filter(c => /^[A-Za-z_-][A-Za-z0-9_-]{0,40}$/.test(c)).slice(0, 4),
      role: roleOf(e), modal: e.getAttribute('aria-modal') === 'true', position: fits(s.position, /^[a-z-]{1,20}$/),
      z_index: fits(s.zIndex, /^(auto|-?\d{1,10})$/), opacity: Number(s.opacity) || 0, pointer_events: fits(s.pointerEvents, /^[a-z-]{1,20}$/),
      cursor: fits(s.cursor, /^[a-z-]{1,20}$/), slot_covered: share(e, r.left, r.top, r.right, r.bottom), window_covered: share(e, 0, 0, W, H),
      box: rect(e)}; };
  const lead = e => { const s = getComputedStyle(e); return {x: parseInt(s.borderLeftWidth, 10) || 0, y: parseInt(s.borderTopWidth, 10) || 0}; };
  const x0 = Math.max(r.left, 0), y0 = Math.max(r.top, 0), x1 = Math.min(r.right, W), y1 = Math.min(r.bottom, H), c = point(slot, null);
  const hit = x1 > x0 && y1 > y0 ? root.elementFromPoint(c.x, c.y) : null, under = hit && reach();
  const holds = e => own(e, under);
  const edge = lead(slot), cells = [];
  for (const down of [0.1, 0.3, 0.5, 0.7, 0.9]) for (const across of [0.1, 0.3, 0.5, 0.7, 0.9]) {
    const at = {x: round(r.width * across - edge.x), y: round(r.height * down - edge.y)}, p = point(slot, at);
    cells.push(p.x >= 0 && p.y >= 0 && p.x < W && p.y < H ? {p, at, top: root.elementFromPoint(p.x, p.y), off: Math.hypot(across - 0.5, down - 0.5)} : null);
  }
  const grid = cells.filter(Boolean).sort((a, b) => a.off - b.off);
  // From the field run on a16202b, nothing told why no spot was pressed. So a look also maps its grid, row by row from the top, in fixed
  // letters: o what a press at the centre would reach, b the slot's own picture (own), s another part of its own, c a control of its own,
  // m a bare part of its own cover box, X anything else, and - outside the window. From the field run on fc60a30, where every point was X,
  // anything else is told apart too: k another part of its own cover box, h something holding what the press goes to, whether the slot
  // takes no pointer events there or the holder paints over it, D something of the description's, as the place over the centre is told,
  // and X what is left. A look also gives where the slot, its cover box and the description box lie.
  // From a review of that: only boxes that show and take words, as CrossCast's own lookup of the description finds it.
  const boxes = [...document.querySelectorAll('[contenteditable=true]')].filter(b => b.getClientRects().length > 0);
  const outer = e => { let h = e; while (h && h.parentElement && !h.parentElement.contains(slot)) h = h.parentElement; return h; };
  const words = e => { const h = outer(e); return boxes.some(b => b.contains(e) || (!!h && h.contains(b))); };
  const mark = g => !g ? '-' : !g.top ? 'X' : !target.contains(g.top)
    ? (g.top.contains(slot) ? 'h' : bare(g.top) ? 'm' : !!box && box.contains(g.top) ? 'k' : words(g.top) ? 'D' : 'X')
    : !!under && g.top.contains(under) ? 'o' : own(g.top, null) ? 'b' : inControl(g.top) ? 'c' : 's';
  const map = [0, 5, 10, 15, 20].map(i => cells.slice(i, i + 5).map(mark).join(''));
  const where ={slot: rect(slot), cover_box: box ? rect(box) : null, description: boxes.length === 1 ? rect(boxes[0]) : null};
  const seen = {open: !!hit && target.contains(hit), spots: grid.filter(g => holds(g.top)).slice(0, 5).map(g => g.at), map, where};
  let atTop = null;
  if (ask.at) { const p = point(slot, ask.at); atTop = root.elementFromPoint(p.x, p.y); seen.at_open = holds(atTop); }
  if (ask.layer) { const q = point(ask.layer, ask.on), top = root.elementFromPoint(q.x, q.y); seen.on_bare = top === ask.layer && bare(top); }
  const answer = (over_it, parts = []) => {
    const through = ask.through ? parts.map(g => { const b = g.top.getBoundingClientRect(), e = lead(g.top);
      return {at: g.at, on: {x: round(g.p.x - b.left - e.x), y: round(g.p.y - b.top - e.y)}}; }) : null;
    // Asked with keep, a handle on what is on top at the spot asked about, for the guard to hold a spot press to (douyin_spot_press).
    if (ask.keep) return {seen: {...seen, over_it, through}, layers: atTop ? [atTop] : []};
    return ask.through ? {seen: {...seen, over_it, through}, layers: parts.map(g => g.top)} : {...seen, over_it, through};
  };
  if (seen.open) return answer(null);
  const inWindow = {slot_in_window: r.left >= 0 && r.top >= 0 && r.right <= W && r.bottom <= H, window: [W, H]};
  if (!hit) return answer({tag: null, ...inWindow});
  const inBox = !!box && box.contains(hit), holds_slot = hit.contains(slot), isControl = inControl(hit);
  let holder = holds_slot ? null : hit;
  while (holder && holder.parentElement && !holder.parentElement.contains(slot)) holder = holder.parentElement;
  const place = inBox ? 'cover box' : boxes.some(e => e.contains(hit) || (holder && holder.contains(e))) ? 'description' : 'page';
  // The spots a press through may go to: where a bare part of the layer over the centre is itself on top, nearest the centre first. From
  // round 1's final check, what is on top at the centre may be an icon or a button of that layer's own that showed with the pointer, which
  // is never pressed, so the spots are its other ones. From the field run on a16202b, what lies over the centre may come from elsewhere, the
  // description's there, while a bare part of the slot's own cover box lies over the rest, as a layer it shows under the pointer might; then
  // they are where such a part is itself on top.
  const parts = (inBox && holder ? grid.filter(g => holder.contains(g.top) && bare(g.top))
    : !inBox && !holds_slot ? grid.filter(g => !target.contains(g.top) && bare(g.top)) : []).slice(0, ask.tries || 4);
  return answer({...facts(hit), place, off_body: !!holder && holder.parentElement === document.body, control: isControl, holds_slot,
    holder: holder && holder !== hit ? facts(holder) : null, ...inWindow}, parts);
}"""
# The guard on one press (douyin_press), put on the page's window from a world of the driver's own that the page can't see. Whether what is
# on top at a point is what the look that decided the press found there: at the slot's centre, anything inside what its press goes to, as
# Playwright's own check has it; at a spot, the very part the look found open there (part), or what holds what a press at the centre would
# reach beneath anything over it; for the press through, the bare part of the cover box itself. It is asked once at the point the pointer was put on, and again at each of the press's
# events at the pointer's own point: a mouse event's point is rounded to whole pixels, so the pointer event's is kept for it. The first event
# where it doesn't hold, and every later one, is held back from the page's controls; as with Playwright's own check, only listeners the page
# put on its window first see it. Only the tag of what was on top is kept, or 'itself' when it was what was being pressed. From a review of
# the change after 073cf63: a mask with a pressed look of its own that draws it in, as cards often have, no longer covered all of the slot
# once the button was down, which the browser shows before the press's first event, so its press through was held back. That part has to
# be bare, as big as the slot, at the point the pointer was put on just before the button goes down; at the press's own events it needs only
# to be that very part on top there, in its cover box and in no control.
DOUYIN_PRESS_GUARD = r"""(slot, ask) => {
  const point = """ + DOUYIN_POINT + r""", parts = """ + DOUYIN_SLOT_PARTS + r""";
  const pressed = ask.layer || slot, b = pressed.getBoundingClientRect();
  const guard = {passed: [], held: null, in_the_way: null,
    shown: pressed.isConnected && b.width > 0 && b.height > 0 && getComputedStyle(pressed).visibility === 'visible'};
  const tag = e => !e ? 'nothing' : /^[a-z][a-z0-9-]{0,40}$/.test(e.tagName.toLowerCase()) ? e.tagName.toLowerCase() : 'other';
  const holds = (x, y, pressing) => {
    const {root, target, bare, reach, box, inControl} = parts(slot, point), top = root.elementFromPoint(x, y), under = ask.kind === 'spot' ? reach() : null;
    const same = e => !!e && e === ask.layer && !!box && box.contains(e) && !e.contains(slot) && !inControl(e);
    const ok = ask.kind === 'through' ? (pressing ? same(top) : top === ask.layer && bare(top))
      : ask.kind === 'spot' ? !!top && target.contains(top)
        && ((!!ask.part && top === ask.part && !inControl(top)) || (!!under && top.contains(under)))
      : !!top && target.contains(top);
    if (!ok) guard.in_the_way = !!top && (top === pressed || top === ask.part) ? 'itself' : tag(top);
    return ok;
  };
  const start = point(pressed, ask.offset || null);
  guard.ready = guard.shown && holds(start.x, start.y);
  const kinds = ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click'];
  let last = null;
  const listener = e => {
    if (!e.isTrusted) return;
    const near = !!last && Math.abs(last[0] - e.clientX) < 1 && Math.abs(last[1] - e.clientY) < 1, here = near ? last : [e.clientX, e.clientY];
    if (e.type === 'pointerdown' || e.type === 'pointerup') last = [e.clientX, e.clientY];
    if (guard.held === null && !holds(here[0], here[1], true)) guard.held = e.type;
    if (guard.held === null) { guard.passed.push(e.type); return; }
    e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation();
  };
  if (guard.ready) for (const kind of kinds) addEventListener(kind, listener, {capture: true});
  guard.stop = () => { for (const kind of kinds) removeEventListener(kind, listener, {capture: true});
    return {shown: guard.shown, ready: guard.ready, passed: guard.passed, held: guard.held, in_the_way: guard.in_the_way}; };
  return guard;
}"""


async def douyin_evaluate(target, script, arg=None):
    """A page script on a slot's locator, which waits for the slot as a press on it would, or on a handle on a part of its cover box."""
    from patchright.async_api import Locator
    if isinstance(target, Locator):
        return await target.evaluate(script, arg, timeout=DOUYIN_SLOT_MS)
    return await target.evaluate(script, arg)


async def douyin_slot_aim(page, target, at=None):
    """Puts the pointer where a press on the target will go, before anything there is looked at or pressed: its centre, or the offset at from
    its padding box (DOUYIN_POINT), after scrolling it into the window if it isn't all there, as the press would. From round 1's final check
    and an offline probe of this driver: a press moves the pointer onto its point, and a layer the page shows only under the pointer then
    took the press, or refused one right after a trial press had passed. The probe found that a press made with the pointer already on its
    point moves it nowhere. What the pointer brings up gets DOUYIN_SETTLE_MS, again until what is on top there is what it hovers
    (DOUYIN_SETTLED), at most DOUYIN_AIM_MOVES times. From a review of a490e80: a slot that lifts or grows under the pointer, as cards often
    do, had moved by then, so its trial press went to a point no look had seen and was thrown away, and the slot wasn't pressed while a layer
    over it cleared. So the point is worked out again after each wait and the pointer follows it, and it settles only where the point stays.
    Returns where the pointer rests and whether it settled there, or None for a target that can't be aimed at now, which its press or look
    then reports."""
    from patchright.async_api import Error as BrowserError
    try:
        point = await douyin_evaluate(target, DOUYIN_AIM, at)
        if isinstance(point, dict) and point.get('shown') and not point.get('in_window'):
            await target.scroll_into_view_if_needed(timeout=DOUYIN_SLOT_MS)
            point = await douyin_evaluate(target, DOUYIN_AIM, at)
        rests = None
        for _ in range(DOUYIN_AIM_MOVES):
            if not isinstance(point, dict) or not point.get('shown'):
                return rests and {**rests, 'settled': False}
            rests = {'x': point['x'], 'y': point['y']}
            await page.mouse.move(rests['x'], rests['y'])
            await page.wait_for_timeout(DOUYIN_SETTLE_MS)
            point = await douyin_evaluate(target, DOUYIN_AIM, at)
            if isinstance(point, dict) and (point.get('x'), point.get('y')) == (rests['x'], rests['y']) and await page.evaluate(DOUYIN_SETTLED, rests):
                return {**rests, 'settled': True}
        return rests and {**rests, 'settled': False}
    except BrowserError:
        return None


async def douyin_pointer_away(page):
    """Puts the pointer at DOUYIN_POINTER_AWAY, off the page, and gives what that takes away the same time to go, again until nothing is
    hovered, at most DOUYIN_AIM_MOVES times, for a look at what stays over the slot with the pointer away. Whether nothing was hovered then.
    A page that can't take it shows in the look that follows."""
    from patchright.async_api import Error as BrowserError
    x, y = DOUYIN_POINTER_AWAY
    try:
        for _ in range(DOUYIN_AIM_MOVES):
            await page.mouse.move(x, y)
            await page.wait_for_timeout(DOUYIN_SETTLE_MS)
            if await page.evaluate(DOUYIN_UNHOVERED):
                return True
    except BrowserError:
        pass
    return False


async def douyin_slot_refuses(page, editor, slot):
    """Why a cover slot can't take a press, or None when it can. The browser stops a trial press before the page's own controls see it, though a
    review's probe of this driver found that listeners a page put on its window first do see it. From round 1's final check and an offline
    probe of this driver: a press moves the pointer onto the slot, and a layer the page shows a moment later under the pointer let a trial
    press pass and then refused the press right after it. So the pointer is put on the slot's centre first and held there for what it brings
    up (douyin_slot_aim), whether or not that settles, since Playwright's own trial press decides here. When something lies over the slot,
    Escape goes to the description once more, from the end of its words, in case a list it opened late is what lies there, and
    douyin_slot_press then looks at what is there before anything is pressed."""
    from patchright.async_api import Error as BrowserError
    await douyin_slot_aim(page, slot)
    try:
        await slot.click(trial=True, timeout=DOUYIN_SLOT_MS)
        return None
    except BrowserError as exc:
        refused = failure_message(exc)
    if 'intercepts pointer events' in refused:
        await douyin_leave_caption(page, editor, from_the_end=True)
    return refused


async def douyin_cover_dialog(page):
    """Douyin's cover window, once a slot has been pressed."""
    await page.locator('[role=dialog]').first.wait_for(state='visible', timeout=DOUYIN_WINDOW_MS)
    dialog = await unique_visible(page.locator('[role=dialog]'), 'Douyin cover editor')
    await dialog.get_by_text('上传封面', exact=True).wait_for(timeout=DOUYIN_WINDOW_MS)
    return dialog


DOUYIN_PRESS_EVENTS = ('pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click')  # The events of a press the guard looks at, in order.


def douyin_in_the_way(state):
    """The tag of what was on top where the guard found its rule didn't hold, or 'other' for anything that isn't a plain tag."""
    return state.get('in_the_way') if re.fullmatch(r'[a-z][a-z0-9-]{0,40}', str(state.get('in_the_way'))) else 'other'


def douyin_held_back(state):
    """Why a press was held back with nothing pressed, in fixed words with only the tag of what was on top, as failure_message keeps it."""
    if not state.get('shown'):
        return 'Nothing was pressed: it no longer showed.'
    if state.get('in_the_way') == 'itself':
        return ("Nothing was pressed: by then what was to be pressed there was no longer as the look had found it "
                "[in the way: itself, intercepts pointer events].")
    return f"Nothing was pressed: something else was on top at its point by then [in the way: {douyin_in_the_way(state)}, intercepts pointer events]."


def douyin_cut_short(state):
    """For a press the guard held back part of the way through: the first of its events held back and the tag of what was on top then. From
    the final check of 073cf63: such a press was told as one that went through and opened no cover window, which pointed the next field run
    at the wrong thing."""
    held = state.get('held') if state.get('held') in DOUYIN_PRESS_EVENTS else 'other'
    return {'from': held, 'in_the_way': douyin_in_the_way(state)}


def douyin_cut_short_said(cut):
    """How a stop tells a press the guard held back part of the way through, in fixed words with only an event's name and a tag."""
    held = cut['from'] if cut.get('from') in DOUYIN_PRESS_EVENTS else 'other'
    tag = douyin_in_the_way(cut)
    if tag == 'itself':
        return f"CrossCast held back its {held} and anything after it, since by then what it was pressing there was no longer as the look had found it [in the way: itself]"
    return f"CrossCast held back its {held} and anything after it, since something else was on top at its point by then [in the way: {tag}]"


async def douyin_press(page, slot, press):
    """The one press on a slot, made where the pointer already rests, at the point the look or trial press that decided it was made: the
    button goes down and comes up there, and the pointer doesn't move. From a review of a490e80 and offline probes of this driver: Playwright's
    own press tried again after a refusal with no wait, scrolling the slot first, so on a page as long as Douyin's form a layer that came
    after the look let the press land at a point no look had seen; and it took anything inside what it pressed, so a button of the slot's own,
    or an icon of the cover box's mask, that showed a little after the pointer had settled took the press. So for this press alone a guard
    goes on the window (DOUYIN_PRESS_GUARD), and at each of the press's events asks again, at the pointer's own point, what the look asked
    there. An event where it doesn't hold, and every later one, is held back from the page's controls, as Playwright's own check holds back a
    refused press. Returns whether the page took the press, and when it took none, why, in fixed words. A press held back after part of it
    went through counts as made, so it is never made again; from the final check of 073cf63, the third value then says where it was cut
    short (douyin_cut_short), and is None otherwise."""
    from patchright.async_api import Error as BrowserError
    ask = {'kind': press['kind'], 'layer': press.get('target'), 'offset': press.get('offset'), 'part': press.get('part')}
    try:
        guard = await slot.evaluate_handle(DOUYIN_PRESS_GUARD, ask, timeout=DOUYIN_SLOT_MS)
    except BrowserError as exc:
        return False, failure_message(exc), None
    went_down = False
    try:
        state = await guard.evaluate('g => ({shown: g.shown, ready: g.ready, in_the_way: g.in_the_way})')
        if not state.get('ready'):
            return False, douyin_held_back(state), None
        await page.mouse.down()
        went_down = True
        await page.mouse.up()
        state = await guard.evaluate('g => g.stop()')
    except BrowserError as exc:
        return went_down, failure_message(exc), None
    finally:
        try:
            await guard.evaluate('g => g.stop()')
            await guard.dispose()
        except BrowserError:
            pass
    if state.get('held') and not state.get('passed'):
        return False, douyin_held_back(state), None
    return True, None, douyin_cut_short(state) if state.get('held') else None


async def douyin_first_press(page, tx, slot, role, label):
    """The press on a slot that took its first trial press: at once, where that trial press left the pointer, with nothing looked at, as both of
    阿哈摇's slots were pressed. From round 1's final check, it is guarded as the covered route's presses are (douyin_press). One held back
    because something else was on top by then presses nothing, and (None, why) sends the slot on as if its trial press had been refused that
    way. Any other refusal stops here, and so does a press that goes through and opens no cover window, with nothing pressed again; from a
    review of a490e80, that stop's record says whether the page was drawing too, and from the final check of 073cf63, where the guard cut
    the press short, if it did. Otherwise (the window, None)."""
    from patchright.async_api import Error as BrowserError
    pressed, why, cut = await douyin_press(page, slot, {'kind': 'centre'})
    if not pressed:
        if 'intercepts pointer events' in why:
            return None, why
        raise VpubError('cover_blocked', f"Douyin's {label} slot took its trial press, then couldn't take the press at its centre, so nothing was published. {why}",
                        DOUYIN_COVER_AGAIN)
    record = {'role': role, 'slot': label, 'refused': None, 'over_it': None, 'pressed': 'at its centre', 'pressed_once': True,
              **({'cut_short': cut} if cut else {})}
    try:
        dialog = await douyin_cover_dialog(page)
    except BrowserError:
        from .browser_edits import douyin_drawing
        tx.save(cover_slot_covered={**record, 'drawing': await douyin_drawing(page),
                                    'note': 'It took its first trial press, so nothing was looked at, and it was pressed once at its centre.'})
        how = f"but {douyin_cut_short_said(cut)}, and " if cut else 'and '
        raise VpubError('cover_blocked', f"Douyin's {label} slot was pressed once at its centre, {how}Douyin's cover window didn't open, so nothing was published "
                        "and nothing was pressed again.", DOUYIN_COVER_AGAIN) from None
    # From a review of the change after 073cf63: a press cut short whose window opened anyway left no sign of the cut, though that window may
    # then hang (cover_not_ready). It is kept as the covered route keeps its record, until this slot's cover is on.
    if cut:
        tx.save(cover_slot_covered={**record, 'note': "It took its first trial press, so nothing was looked at, and it was pressed once at its centre, "
                                                      "and Douyin's cover window opened."})
    return dialog, None


async def douyin_slot_look(slot, through=False, at=None, layer=None, on=None, keep=False):
    """What lies over a slot now, seen with nothing pressed (DOUYIN_SLOT_LOOK), and with through, handles on the parts of its cover box a press
    may go through. With at, whether that spot of the slot holds, and with layer and on, whether that part of the cover box is itself on top
    at that point of its own. From a review: a look the page didn't answer, as when the slot was taken off the page for a while or a second
    cover- element came, ended the wait with a bare driver error, so it is a look that saw nothing open, and says why. With keep, a handle on
    what is on top at the spot at, for the guard on a spot press (douyin_spot_press)."""
    from patchright.async_api import Error as BrowserError
    ask = {'through': through, 'at': at, 'layer': layer, 'on': on, 'tries': DOUYIN_SPOT_TRIES, 'keep': keep}
    try:
        await slot.scroll_into_view_if_needed(timeout=DOUYIN_SLOT_MS)
        if not through and not keep:
            return await slot.evaluate(DOUYIN_SLOT_LOOK, ask, timeout=DOUYIN_SLOT_MS), []
        found = await slot.evaluate_handle(DOUYIN_SLOT_LOOK, ask, timeout=DOUYIN_SLOT_MS)
        parts = await (await found.get_property('layers')).get_properties()
        return await (await found.get_property('seen')).json_value(), [parts[key].as_element() for key in sorted(parts, key=int)]
    except BrowserError as exc:
        return {'open': False, 'spots': [], 'over_it': None, 'through': [], 'unseen': failure_message(exc)}, []


async def douyin_drawn(page, covered):
    """Whether the page was drawing, gathered once, at the first look that finds the slot covered, for the lead that the hidden window draws no
    frames (douyin_drawing): fixed words, true or false, and a count."""
    if covered['drawing'] is None:
        from .browser_edits import douyin_drawing
        covered['drawing'] = await douyin_drawing(page)


def douyin_map(seen):
    """The look's map of its grid (DOUYIN_SLOT_LOOK), five rows of five fixed letters, or None for anything else."""
    rows = seen.get('map')
    if isinstance(rows, list) and len(rows) == 5 and all(isinstance(row, str) and re.fullmatch(r'[obscmkhDX-]{5}', row) for row in rows):
        return rows
    return None


def douyin_where(seen):
    """Where the look found the slot, its cover box and the description box, each as whole pixels (left, top, width, height) or None, from
    the field run on fc60a30, where something lay over every point of the slot. Anything else is None."""
    found = seen.get('where')
    if not isinstance(found, dict):
        return None
    def box(value):
        ok = isinstance(value, list) and len(value) == 4 and all(type(n) is int and -100000 < n < 100000 for n in value)
        return value if ok else None
    return {name: box(found.get(name)) for name in ('slot', 'cover_box', 'description')}


def douyin_tried(covered, why):
    """A spot of the slot's own that was aimed at and not pressed, counted by why, in a fixed word: closed when, with the pointer there, it no
    longer held, unseen when that look failed, and otherwise as douyin_slot_trial says. From the field run on a16202b: nothing told whether
    any spot had been tried."""
    covered['spot_tries'][why] = covered['spot_tries'].get(why, 0) + 1


def douyin_settled(covered, where, aimed):
    """The aim, with where the pointer never settled counted for the receipt. From a review of a490e80: a slot whose point never settled read
    as one whose trial press was refused, and the stops then said what hadn't happened."""
    if not aimed or not aimed.get('settled'):
        covered['unsettled'][where] = covered['unsettled'].get(where, 0) + 1
    return aimed


async def douyin_look_away(page, slot, covered):
    """A look with the pointer away from the slot (douyin_pointer_away). What it sees over the centre is kept as over_it_away, None when the
    centre was open then. From a review of the change after 073cf63: a look that failed set it back to None, as if the centre had been open,
    so a failed look leaves what the last look that could see found there. The same goes for its map of the slot (slot_map_away)."""
    douyin_settled(covered, 'away', {'settled': await douyin_pointer_away(page)})
    seen, _ = await douyin_slot_look(slot)
    covered['unseen'] = seen.get('unseen')
    if not covered['unseen']:
        covered['over_it_away'], covered['slot_map_away'], covered['where'] = seen['over_it'], douyin_map(seen), douyin_where(seen)
    if seen['over_it']:
        await douyin_drawn(page, covered)
    return seen


async def douyin_slot_trial(slot, aimed, at=None):
    """Whether the slot takes a trial press at the point the pointer was aimed at, with the pointer still there, so the press can follow at that
    point with the pointer unmoved: None when it does, and otherwise why not, in a fixed word. A point the pointer couldn't be put on, or
    didn't settle on, isn't tried (unaimed, unsettled). Playwright tries a refused press again after scrolling the slot, which would leave the
    pointer on a point no look has seen, so a slot that has moved since the aim isn't pressed either (moved)."""
    from patchright.async_api import Error as BrowserError
    if not aimed:
        return 'unaimed'
    if not aimed['settled']:
        return 'unsettled'
    try:
        await slot.click(trial=True, timeout=DOUYIN_SLOT_MS, **({'position': at} if at else {}))
        now = await douyin_evaluate(slot, DOUYIN_AIM, at)
    except BrowserError:
        return 'refused'
    return None if isinstance(now, dict) and (now.get('x'), now.get('y')) == (aimed['x'], aimed['y']) else 'moved'


async def douyin_look_on(page, slot, covered, through=False):
    """A look with the pointer on the slot's centre, aimed there again so that what it brings up has settled, and the trial press there when the
    centre is open then. What it sees over the centre is what a press there would hit, kept as over_it. From round 1's final check, a later
    look that finds the centre open but takes no trial press is flagged, so a stop doesn't tell what an earlier look saw as if it were the
    last, and from a review of a490e80, the flag says why. Returns what it saw, handles on the parts of the cover box a press may go through,
    and the press, or None."""
    aimed = douyin_settled(covered, 'centre', await douyin_slot_aim(page, slot))
    seen, layers = await douyin_slot_look(slot, through=through)
    covered['unseen'] = seen.get('unseen')
    if not covered['unseen']:
        covered['slot_map'] = douyin_map(seen)
    if seen['open']:
        why = await douyin_slot_trial(slot, aimed)
        if why is None:
            covered['open_but_refused'], covered['centre_trial'] = False, None
            return seen, layers, {'pressed': 'at its centre', 'kind': 'centre'}
        covered['open_but_refused'], covered['centre_trial'] = True, why
        covered['moved'] += why == 'moved'
    elif seen['over_it']:
        covered['over_it'], covered['open_but_refused'], covered['centre_trial'] = seen['over_it'], False, None
        await douyin_drawn(page, covered)
    return seen, layers, None


async def douyin_spot_press(page, slot, spots, covered):
    """A press at a spot of the slot's own, for a slot whose centre something lies over, decided with the pointer on that spot: up to
    DOUYIN_SPOT_TRIES of them, nearest the centre first. From a review, a spot holds only where what is on top holds what a press at the
    centre would reach beneath anything over it, and from the field run on a16202b, also where it is the slot's own picture, no control and
    in none (own in DOUYIN_SLOT_PARTS). From round 1's final check, that is looked at with the pointer there, since a button of the slot's own that
    showed only under the pointer took such a press. Each spot tried and not pressed is counted by why (douyin_tried), and from a review of
    that, the press is held to the very part found there (part), since nothing in the markup shows a handler a script put on a picture."""
    for at in spots[:DOUYIN_SPOT_TRIES]:
        aimed = douyin_settled(covered, 'spot', await douyin_slot_aim(page, slot, at))
        seen, kept = await douyin_slot_look(slot, at=at, keep=True)
        why = 'unseen' if seen.get('unseen') else await douyin_slot_trial(slot, aimed, at) if seen.get('at_open') else 'closed'
        if why is None:
            return {'pressed': 'at a spot where nothing lay over it', 'kind': 'spot', 'at': at, 'offset': at, 'part': kept[0] if kept else None}
        covered['moved'] += why == 'moved'
        douyin_tried(covered, why)
    return None


async def douyin_through_press(page, slot, seen, layers, covered):
    """The press through a part of the slot's own cover box as big as the slot, no control and in none, where it is itself on top: where a
    person's press would land, and what they would be pressing. From a review: pressed with force at the slot's centre, the press went to a
    small icon of the layer there with a press of its own, so it goes only to that part. From round 1's final check: an icon or a button that
    showed only under the pointer took that press too. So the spots come from a look with the pointer on the slot, since a layer that came
    with the pointer is there only then, and each of up to DOUYIN_SPOT_TRIES of them is aimed at and looked at again, and taken only if that
    part itself is on top there with the pointer there. From a review of a490e80, the press itself is guarded the same way (douyin_press), and
    a spot the pointer never settled on is counted apart, so a stop says so."""
    for spot, layer in zip(seen.get('through') or [], layers):
        aimed = douyin_settled(covered, 'bare part', await douyin_slot_aim(page, layer, spot['on']))
        if not aimed or not aimed['settled']:
            continue
        there, _ = await douyin_slot_look(slot, layer=layer, on=spot['on'])
        if there.get('on_bare'):
            return {'pressed': 'through its own cover box', 'kind': 'through', 'at': spot['at'], 'offset': spot['on'], 'target': layer}
    return None


def douyin_with_pointer(away, on):
    """Whether a pair of looks found the slot's centre open with the pointer away and something over it with the pointer on it."""
    return away['open'] and not on['open'] and not on.get('unseen')


async def douyin_slot_opening(page, slot, covered):
    """Where a slot that something lay over can be pressed, once Escape has gone to the description: from field runs on 647e002 and 7fa6c10,
    where a slot was covered on both of its tries. A look with the pointer away and one with it on the slot's centre come first. From round
    1's final check and an offline probe of this driver: what is open with the pointer away and over the slot with the pointer on it came with
    the pointer, and since every press brings the pointer back, waiting can't clear it, so this returns at once, with came_with_pointer. From a
    review of a490e80: a layer that came by itself between the two looks, as one Douyin shows while it readies the cover area might, read the
    same, and was pressed through or stopped the post at once, so a second pair of looks has to find the same before it counts. What stays
    with the pointer away is looked at about every one and a half seconds for up to DOUYIN_COVERED_SECONDS, with the pointer away and
    nothing pressed but trial presses: at the centre once it is open, aimed at and looked at again with the pointer on it, or at a spot of
    the slot's own, the same way. A stop or a platform check ends the wait. From a review: like X's waits, this one ends before the
    command's own cut-off, which would close the browser with nothing recorded."""
    from . import browser_runtime
    end = browser_runtime.command_deadline
    limit = DOUYIN_COVERED_SECONDS if end is None else min(DOUYIN_COVERED_SECONDS, end - X_COMMAND_MARGIN_SECONDS - covered['started'])
    away = await douyin_look_away(page, slot, covered)
    on, _, covered['press'] = await douyin_look_on(page, slot, covered)
    waiting, pairs = False, 0
    while covered['press'] is None:
        pairs = pairs + 1 if douyin_with_pointer(away, on) else 0
        if pairs >= 2:
            covered['came_with_pointer'] = True
            break
        if pairs:
            away = await douyin_look_away(page, slot, covered)
            if away['open']:
                on, _, covered['press'] = await douyin_look_on(page, slot, covered)
            continue
        # The same spots aren't aimed at again while nothing about them has changed, so the pointer doesn't go back and forth at each look.
        if not away['open'] and away['spots'] and away['spots'] != covered['tried']:
            covered['tried'] = away['spots']
            covered['press'] = await douyin_spot_press(page, slot, away['spots'], covered)
            if covered['press']:
                break
        if time.monotonic() - covered['started'] >= limit:
            break
        if not waiting:
            stage('waiting for a cover slot to clear')
            waiting = True
        await page.wait_for_timeout(DOUYIN_COVERED_LOOK_MS)
        await risk_check(page)
        stop_check('douyin')
        away = await douyin_look_away(page, slot, covered)
        if away['open']:
            on, _, covered['press'] = await douyin_look_on(page, slot, covered)
    if waiting:
        stage('reading the page')


async def douyin_slot_last(page, slot, covered, leave_out):
    """What a slot gets once waiting can't clear what lies over it: at once for what came with the pointer, or after the wait for what stayed.
    One more look with the pointer on the slot's centre, which may find it open, then a spot of the slot's own that holds with the pointer on
    it, and, unless it is a 4:3 slot that may be left out, the press through a bare part of its own cover box (douyin_through_press). From
    the field run on a16202b, when what stayed over the centre lies outside the cover box, that part is looked for with the pointer on a
    clear spot of the slot's own instead. The press, or None."""
    seen, layers, press = await douyin_look_on(page, slot, covered, through=not leave_out)
    clear = covered['tried'] or seen['spots']
    if press is None and seen['spots'] != covered['tried']:
        press = await douyin_spot_press(page, slot, seen['spots'], covered)
    if (press is None and not leave_out and not covered['came_with_pointer'] and clear and not seen['open'] and not seen.get('through')
            and not seen.get('unseen') and (seen.get('over_it') or {}).get('place') not in (None, 'cover box')):
        # From the field run on a16202b: with something from elsewhere over the centre, the pointer there hovers that, not the slot's cover box,
        # so a layer the box shows only under the pointer is looked for with the pointer on a clear spot of the slot's own. From a review of
        # that, only once the wait is over, since no look with the pointer away judged that layer, and its map is kept (slot_map_spot).
        douyin_settled(covered, 'clear spot', await douyin_slot_aim(page, slot, clear[0]))
        seen, layers = await douyin_slot_look(slot, through=True)
        covered['unseen'] = seen.get('unseen')
        if not covered['unseen']:
            covered['slot_map_spot'] = douyin_map(seen)
    if press is None and not leave_out:
        covered['bare_spots'] = len(seen.get('through') or [])
        press = await douyin_through_press(page, slot, seen, layers, covered)
    return press


def douyin_seconds(seconds):
    whole = round(seconds)
    return 'under a second' if whole < 1 else '1 second' if whole == 1 else f'{whole} seconds'


def douyin_over_it_words(over_it):
    """What last lay over a slot's centre, in a few words of code and style: its tag and class tokens, what held it, where it sat and how much
    it covered. Never any of its words. From a review: a slot that took no pointer events was said to have its own holder over it, and a
    slot whose centre was open was said to have something the browser couldn't see there."""
    if not over_it:
        return 'nothing was seen over its centre, yet it took no trial press'
    if not over_it.get('tag'):
        return 'the browser could see nothing at its centre' + ('' if over_it.get('slot_in_window') else ', which lay outside the window')
    def name(e):
        return e['tag'] + ''.join('.' + token for token in e['classes'])
    if over_it.get('holds_slot'):
        return f"it took no pointer events at its centre, where {name(over_it)}, which holds it, was on top"
    within = f" inside {name(over_it['holder'])}" if over_it.get('holder') else ''
    where = {'cover box': " in the slot's own cover box", 'description': ' by the description box'}.get(
        over_it['place'], " hung off the page's body" if over_it['off_body'] else ' elsewhere on the page')
    part = 'all of the slot' if over_it['slot_covered'] >= 0.99 else f"{round(over_it['slot_covered'] * 100)}% of the slot"
    return (f"the last thing seen over its centre was {name(over_it)}{within}{where}{', a control' if over_it['control'] else ''}, "
            f"{over_it['position']}, over {part} and {round(over_it['window_covered'] * 100)}% of the window")


# Why the last look's open centre wasn't pressed, as a stop says it after "its centre was open at the last look" (douyin_slot_trial). From a
# review of a490e80: every one of these was told as a refused trial press.
DOUYIN_CENTRE_UNTRIED = {'unsettled': ', but the pointer never settled there, so no trial press was made',
                         'unaimed': ", but the pointer couldn't be put there, so no trial press was made",
                         'moved': ' and took its trial press, but the slot had moved since the pointer was put there'}


def douyin_last_seen(covered):
    """What the last look with the pointer on a slot's centre saw there. From round 1's final check: a stop told what an earlier look had seen,
    though the last one had found the centre open and its trial press refused. From a review of a490e80: a press decided at the open centre
    and then held back was said to have taken no trial press. From the final check of 073cf63: when the looks after it failed, the stop
    said both that the centre was open at the last look and that the last look had failed, so that look is named apart; and from a review
    of that, by its kind, since a look with the pointer away that could see may have come after it and seen something there."""
    if covered.get('pressed') == 'at its centre':
        return 'its centre was open at the last look and took its trial press'
    if covered.get('open_but_refused'):
        look = 'the last look with the pointer on it that could see it' if covered.get('unseen') else 'the last look'
        return f"its centre was open at {look}" + DOUYIN_CENTRE_UNTRIED.get(covered.get('centre_trial'), ', yet it took no trial press')
    if not covered['over_it'] and covered.get('unseen'):
        return 'no look could see what lay over its centre'
    return douyin_over_it_words(covered['over_it'])


def douyin_pointer_said(covered):
    """Whether what lay over a slot's centre showed only while the pointer was over the slot or stayed with the pointer away, in fixed words."""
    over_it = covered['over_it']
    if covered.get('open_but_refused') or not over_it or not over_it.get('tag') or over_it.get('holds_slot'):
        return ''
    if covered.get('came_with_pointer'):
        return ' It showed only while the pointer was over the slot.'
    return ' It stayed with the pointer away from the slot.' if covered.get('over_it_away') else ''


def douyin_over_it_said(covered):
    """A stop's short account of what lay over a slot, how long it was waited for, and whether it showed only while the pointer was over the
    slot, with no words from the page. From a review of a490e80: what came with the pointer isn't waited for, so its time is that of the looks."""
    spent = douyin_seconds(covered['waited_seconds'])
    lead = f"After {spent} of looks" if covered.get('came_with_pointer') else f"After a wait of {spent}"
    return f"{lead}, {douyin_last_seen(covered)}.{douyin_pointer_said(covered)}"


def douyin_not_through(covered):
    """Why a slot that still couldn't take a press wasn't pressed through what lay over it."""
    over_it = covered['over_it']
    if covered.get('unseen'):
        return f"Its last look failed, so nothing was pressed through: {covered['unseen']}"
    if covered.get('open_but_refused') or not over_it or not over_it.get('tag') or over_it.get('holds_slot'):
        return 'So nothing was pressed through.'
    bare = None
    if covered.get('bare_spots'):
        unsettled = covered['unsettled'].get('bare part', 0)
        bare = ('the pointer never settled there' if unsettled >= covered['bare_spots'] else
                ('the pointer never settled there or ' if unsettled else '') + 'something else was on top once the pointer was there')
    if over_it['place'] != 'cover box':
        # From the field run on a16202b, a bare part of the slot's own cover box over the rest of the slot may be pressed through then.
        outside = "It lies outside the slot's own cover box, so it wasn't pressed"
        return (f"{outside}, and wherever a bare part of that box as big as the slot lay on top, {bare}, so that wasn't pressed either."
                if bare else f"{outside}.")
    if bare:
        return f"Wherever a bare part of it as big as the slot lay on top, {bare}, so it wasn't pressed."
    if over_it['control']:
        return "It's a control, or lies inside one, so it wasn't pressed."
    return "No part of it as big as the slot lay bare to a press, so it wasn't pressed."


def douyin_spots_said(covered):
    """Whether spots of the slot's own clear of what lay over it were tried, for a stop. From the field run on a16202b: most of the slot lay
    clear, and the stop didn't say whether any of it had been tried."""
    tries = sum(covered.get('spot_tries', {}).values())
    if tries:
        return f" Clear spots of the slot's own were tried {tries} time{'s' if tries != 1 else ''}, and none could take a press."
    # From a review of that: when the last look failed, no look said so, and when it found the centre open, all of the slot was clear.
    if covered.get('unseen') or covered.get('open_but_refused'):
        return ''
    return " No clear spot of the slot's own was one a press could go to."


def douyin_slot_record(covered, role=None, label=None):
    """What a receipt keeps of a slot that something lay over: what it was with the pointer on the slot and with the pointer away, with no
    words from the page, whether it came with the pointer, whether the page was drawing, how and where the slot was pressed, if it was, and
    how long it waited. A stop's goes under cover_slot_covered with the slot's role and label, so a cover's own key means what it did. From a
    review of a490e80, it also keeps, in fixed words and counts, why an open centre wasn't pressed, where the pointer never settled, how
    many trial presses found the slot moved, and whether the press that followed a first trial press was the one held back. From the final
    check of 073cf63, a press the guard held back part of the way through says where (cut_short). From the field run on a16202b, the last
    looks' maps of the slot, with the pointer on its centre, away and on a clear spot (slot_map, slot_map_away, slot_map_spot, in
    DOUYIN_SLOT_LOOK's letters), and the spots of its own tried and not pressed, by why (spot_tries)."""
    press = covered['press'] or {}
    return {**({'role': role, 'slot': label} if role else {}), 'refused': covered['refused'],
            **({'refused_after_trial': True} if covered.get('refused_after_trial') else {}), 'over_it': covered['over_it'],
            'over_it_away': covered['over_it_away'], 'came_with_pointer': covered['came_with_pointer'], 'pressed': press.get('pressed'),
            **({'at': press['at']} if 'at' in press else {}), **({'cut_short': covered['cut_short']} if covered.get('cut_short') else {}),
            **({'unseen': covered['unseen']} if covered.get('unseen') else {}),
            **({'open_but_refused': True, 'centre_trial': covered['centre_trial']} if covered.get('open_but_refused') else {}),
            **({'unsettled': dict(covered['unsettled'])} if covered.get('unsettled') else {}),
            **({'moved': covered['moved']} if covered.get('moved') else {}),
            **({'slot_map': covered['slot_map']} if covered.get('slot_map') else {}),
            **({'slot_map_away': covered['slot_map_away']} if covered.get('slot_map_away') else {}),
            **({'slot_map_spot': covered['slot_map_spot']} if covered.get('slot_map_spot') else {}),
            **({'where': covered['where']} if covered.get('where') else {}),
            **({'spot_tries': dict(covered['spot_tries'])} if covered.get('spot_tries') else {}),
            'drawing': covered['drawing'], 'waited_seconds': covered['waited_seconds']}


def douyin_slot_skipped(size, refused=None, covered=None):
    """The record of a 4:3 cover left out while a 竖封面3:4 one goes on: for a video taller than it is wide, whose 横封面4:3 slot isn't pressed
    at all, or for one whose size couldn't be read and whose slot was turned off, had something over it that came with the pointer, or still
    had something over it after the wait. Such a slot is never pressed through. A review found its note said the description's list had been
    closed when nothing had been done about one, so it says only what was tried."""
    if not refused:
        return {'state': 'SKIPPED', 'video_size': list(size),
                'note': f"The video is {size[0]}x{size[1]}, taller than it is wide, so Douyin's 横封面4:3 slot wasn't pressed and the planned 4:3 cover wasn't set."}
    unread = "The video's size couldn't be read from its file, and Douyin's 横封面4:3 slot"
    if not covered:
        return {'state': 'SKIPPED', 'video_size': None, 'refused': refused,
                'note': f"{unread} was turned off, so the planned 4:3 cover wasn't set and nothing was pressed there."}
    if covered['came_with_pointer']:
        how = (f"{unread} had something over it that showed only while the pointer was over the slot, once Escape had been pressed in the "
               f"description at the end of its words: {douyin_last_seen(covered)}.")
    else:
        how = (f"{unread} still couldn't take a press after Escape was pressed in the description at the end of its words and a wait of "
               f"{douyin_seconds(covered['waited_seconds'])}: {douyin_last_seen(covered)}.{douyin_pointer_said(covered)}")
    failed = f" Its last look failed: {covered['unseen'].rstrip('.')}." if covered.get('unseen') else ''
    return {'state': 'SKIPPED', 'video_size': None, **douyin_slot_record(covered),
            'note': f"{how}{failed} So the planned 4:3 cover wasn't set and nothing was pressed there."}


def douyin_slot_blocked(role, label, size, tall_too, refused, covered=None):
    """The stop for a cover slot that couldn't take a press, saying why a 4:3 cover wasn't left out instead, and for one that something lay
    over, what it was, for how long, whether it came with the pointer, and why it wasn't pressed through. From a review of a490e80: a slot
    whose press was held back after its first trial press had passed read as one whose trial press was refused, so it says so."""
    why = ('' if role != 'landscape' else
           f"The video is {size[0]}x{size[1]}, at least as wide as it is tall, so its 4:3 cover is never left out. " if size and size[0] >= size[1] else
           "It's the only cover this attempt has, so it isn't left out. " if not tall_too else
           "A 4:3 cover is left out only when something lies over its slot or the slot is turned off, and the browser saw neither. ")
    first = 'It took its first trial press, but not the press after it. ' if covered and covered.get('refused_after_trial') else ''
    over = f" {douyin_over_it_said(covered)} {douyin_not_through(covered)}{douyin_spots_said(covered)}" if covered else ''
    return VpubError('cover_blocked', f"Douyin's {label} slot couldn't take a press, so nothing was published. {why}{first}{refused}{over}", DOUYIN_COVER_AGAIN)


def douyin_cover_unopened(label, record, failed=None):
    """The stop for a slot that something lay over, pressed once where douyin_slot_press found it could be, when that press was held back or
    opened no cover window, and from the final check of 073cf63, where the guard cut that press short, if it did."""
    cut = f"but {douyin_cut_short_said(record['cut_short'])}, and " if record.get('cut_short') else 'and '
    done = (f"couldn't take the press {record['pressed']}, so nothing was published. {failed}" if failed else
            f"was pressed once {record['pressed']}, {cut}Douyin's cover window didn't open, so nothing was published and nothing was pressed again.")
    return VpubError('cover_blocked', f"Douyin's {label} slot {done} {douyin_over_it_said(record)}", DOUYIN_COVER_AGAIN)


async def douyin_slot_press(page, tx, slot, role, label, size, tall_too, refused, after_trial=False):
    """How a cover slot that refused a press is to be pressed: for one that something lay over, what it was and where the slot can be pressed,
    after a platform check and a stop. None is a 4:3 cover left out, whose record is saved. Anything else stops here, before anything is
    published. With after_trial, the refusal was the press that followed a first trial press that passed (douyin_first_press)."""
    # The one cover ever left out after a refusal: a 4:3 one on a video whose size can't be read, while a 竖封面3:4 one goes on.
    leave_out = role == 'landscape' and tall_too and not size
    if 'intercepts pointer events' not in refused:
        if leave_out and DOUYIN_SLOT_OFF.search(refused):
            tx.save(landscape_cover=douyin_slot_skipped(None, refused))
            return None
        raise douyin_slot_blocked(role, label, size, tall_too, refused)
    covered = {'refused': refused, 'refused_after_trial': after_trial, 'over_it': None, 'over_it_away': None, 'came_with_pointer': False,
               'drawing': None, 'press': None, 'unseen': None, 'open_but_refused': False, 'centre_trial': None, 'unsettled': {}, 'moved': 0,
               'bare_spots': 0, 'tried': None, 'slot_map': None, 'slot_map_away': None, 'slot_map_spot': None, 'spot_tries': {}, 'where': None,
               'started': time.monotonic()}
    await douyin_slot_opening(page, slot, covered)
    # A 4:3 slot that may be left out is never pressed through: under what came with the pointer it is left out at once, unless a spot of its
    # own holds, and under what stayed, once the wait is over, as before.
    if covered['press'] is None and (covered['came_with_pointer'] or not leave_out):
        covered['press'] = await douyin_slot_last(page, slot, covered, leave_out)
    covered['waited_seconds'] = round(time.monotonic() - covered['started'], 1)
    if covered['press'] is None and leave_out:
        tx.save(landscape_cover=douyin_slot_skipped(None, refused, covered))
        return None
    # From a review: these came before that last look, not between it and the press it decides.
    await risk_check(page)
    stop_check('douyin')
    if covered['press'] is None:
        tx.save(cover_slot_covered=douyin_slot_record(covered, role, label))
        raise douyin_slot_blocked(role, label, size, tall_too, refused, covered)
    return covered


async def douyin_cover_window(page, tx, slot, role, label, how):
    """The one press on a slot that something lay over, where douyin_slot_press found it could be, with the pointer already there and the press
    guarded there as the look that decided it (douyin_press): on the slot, or on the part of its own cover box found for it. A press held back,
    or one that opens no cover window, stops with cover_blocked, and nothing is pressed again. From round 1's final check: once the window is
    open, what lay over the slot is saved at once, so a stop later in the cover step keeps it, and the cover's own record takes its place once
    the cover is on."""
    from patchright.async_api import Error as BrowserError
    pressed, why, cut = await douyin_press(page, slot, how['press'])
    if cut:
        how['cut_short'] = cut
    dialog = None
    if pressed:
        try:
            dialog = await douyin_cover_dialog(page)
        except BrowserError:
            pass
    record = {**douyin_slot_record(how, role, label), 'pressed_once': pressed}
    if dialog is None:
        tx.save(cover_slot_covered=record)
        raise douyin_cover_unopened(label, record, None if pressed else why)
    tx.save(cover_slot_covered={**record, 'note': "It was pressed once, and Douyin's cover window opened."})
    return dialog


async def douyin_slot_window(page, tx, editor, slot, role, label, size, tall_too):
    """One press on a cover slot and the cover window it opens, with how the slot was pressed: {} for one that took its first trial press. None
    is a 4:3 cover left out, whose record is saved. Anything else stops before anything is published, and nothing is pressed again."""
    refused, after_trial = await douyin_slot_refuses(page, editor, slot), False
    if not refused:
        dialog, refused = await douyin_first_press(page, tx, slot, role, label)
        if dialog is not None:
            return dialog, {}
        # From round 1's final check: a layer came between the trial press and the press, which was then held back with nothing pressed. The
        # slot goes on as if its trial press had been refused that way, after the same one Escape in the description, and its record says so.
        await douyin_leave_caption(page, editor, from_the_end=True)
        after_trial = True
    how = await douyin_slot_press(page, tx, slot, role, label, size, tall_too, refused, after_trial)
    return None if how is None else (await douyin_cover_window(page, tx, slot, role, label, how), how)


async def finish_douyin_form(page, tx, before, covers=True):
    """Complete this already-uploaded form; never select the video again. With covers false, this pass set them already (publish_douyin)."""
    caption = douyin_caption(tx.options['description'], tx.options['tags'])
    title = page.locator('input[placeholder*="填写作品标题"]')
    editor = await unique_visible(page.locator('[contenteditable=true]'), 'Douyin description')
    if covers:
        await douyin_set_covers(page, tx, editor)
    else:
        await douyin_leave_caption(page, editor)
    return await douyin_finish_after_covers(page, tx, before, caption, title, editor)


def douyin_covers_planned(tx):
    """The covers left to set, as (role, label, path). A cover an earlier pass set on this form isn't set again, and one it left out is looked
    at again. A form just uploaded starts with none on (publish_douyin)."""
    return [(role, label, tx.cover(role)) for role, label in DOUYIN_COVERS if (tx.receipt.get(role + '_cover') or {}).get('state') != 'UI_ACCEPTED']


def douyin_covers_to_set(tx):
    """Whether a cover is left that a slot will be pressed for, with a file to set it from. From a final check of setting covers first: a
    video taller than it is wide never has its 4:3 one pressed, so that one doesn't count, and a kept draft's words aren't taken out for it."""
    tall_too = bool(tx.cover('portrait'))
    for role, _, path in douyin_covers_planned(tx):
        size = tx.video_size() if role == 'landscape' else None
        if path and not (tall_too and size and size[1] > size[0]):
            return True
    return False


async def douyin_empty_caption(tx, editor):
    """The kept draft's words taken out of its description box, as douyin_type_caption does before it types, so that nothing of theirs lies
    over a cover slot while a cover is set. They go back in afterwards (douyin_words_in) and are read back before 发布."""
    await editor.click()
    await editor.press('ControlOrMeta+A')
    await editor.press('Backspace')
    tx.save(description_typed=False)


async def douyin_words_in(page, tx, caption):
    """The plan's words in the description box once the covers are set, typed only where the box doesn't already hold them, read as the
    read-back before 发布 reads them, or a draft whose lines ran together would never be typed again and would stop every resume. From a
    review of setting covers first, the box is found again here, after the cover windows, and the receipt says the words are in."""
    editor = await unique_visible(page.locator('[contenteditable=true]'), 'Douyin description')
    if douyin_form_differs(caption, await editor.inner_text()):
        await douyin_type_caption(page, editor, caption)
    tx.save(description_typed=True)


# Said by a stop in the cover step while the description isn't in the form yet. From a check of setting covers first: a run's own receipt
# keeps a stop's code and message, not its hint, so this is part of the message.
DOUYIN_WORDS_NOT_IN = ("The description isn't in Douyin's kept draft yet, since it goes in after the covers, so finish this post with vpub "
                       "resume, not by hand on Douyin.")


async def douyin_set_covers(page, tx, editor):
    """The covers this attempt plans (douyin_covers_now). A stop there while the description is still to go in says so, so that nobody posts
    the kept draft by hand without its words."""
    try:
        await douyin_covers_now(page, tx, editor)
    except VpubError as exc:
        if tx.receipt.get('description_typed') is False and DOUYIN_WORDS_NOT_IN not in exc.message:
            raise VpubError(exc.code, f"{exc.message.rstrip()} {DOUYIN_WORDS_NOT_IN}", exc.hint) from None
        raise


async def douyin_covers_now(page, tx, editor):
    """The covers this attempt plans, each through its own slot and Douyin's cover window, after the description's lists are closed and the
    cursor has left it (douyin_leave_caption)."""
    await douyin_leave_caption(page, editor)
    planned = douyin_covers_planned(tx)
    # From a field run: a 1080x1920 video's 横封面4:3 slot had something over it, and its press ran out and stopped the post. So a video taller
    # than it is wide gets only its 竖封面3:4 cover, as the owner chose, though an earlier 1080x1920 video's slot had taken its 4:3 cover.
    # A review found leaving the 4:3 one out left a post with no cover when it was the only one, as vpub resume --cover gives, and that a
    # wider video went out without it after any refusal, a second slot from a layout change too. So it is left out only while a 竖封面3:4 cover
    # goes on: on a taller video, or on one whose size can't be read when its slot is turned off or still covered after the wait. Any other
    # refusal stops here. From field runs on 647e002 and 7fa6c10 and round 1's final check, a slot something lies over is pressed only where a
    # look made with the pointer already on that point found it could be: on the slot itself, or through a bare part of its own cover box as
    # big as itself. What came with the pointer is pressed through at once, what stays is waited for, and a slot that still can't be pressed
    # stops with what lay there (douyin_slot_window).
    tall_too = bool(tx.cover('portrait'))
    for role, label, path in [cover for cover in planned if cover[2]]:
        control = page.locator('[class^="coverControl-"]').filter(has=page.get_by_text(label, exact=True))
        slot = control.locator('[class^="cover-"]:not([class^="cover-tip"])')
        size = tx.video_size() if role == 'landscape' else None
        if tall_too and size and size[1] > size[0]:
            tx.save(landscape_cover=douyin_slot_skipped(size))
            continue
        # Keep the slot's pre-open picture signature only in memory: an old
        # preview or placeholder must not certify that this save changed it.
        from patchright.async_api import Error as BrowserError
        try:
            before_preview = await slot.evaluate(DOUYIN_SAVED_PREVIEW, timeout=DOUYIN_SLOT_MS) if await slot.count() == 1 else None
        except BrowserError:
            before_preview = None
        opened = await douyin_slot_window(page, tx, editor, slot, role, label, size, tall_too)
        if opened is None:
            continue
        if before_preview is None:
            raise VpubError('cover_unverified', 'The cover preview could not be read before opening its editor.', DOUYIN_COVER_AGAIN)
        dialog, how = opened
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
                raise VpubError('cover_not_ready', f"Douyin's cover window was still working {DOUYIN_COVER_SECONDS} seconds after 完成, so nothing was published. {await douyin_cover_stuck(page, fonts, traffic())}", DOUYIN_COVER_AGAIN) from None
        finally:
            traffic(stop=True)
        await douyin_wait_saved_preview(page, slot, before_preview)
        # A stop's account of what lay over this slot is done with once its cover is on.
        if (tx.receipt.get('cover_slot_covered') or {}).get('role') == role:
            tx.receipt.pop('cover_slot_covered')
        tx.save(**{role+'_cover': {'state':'UI_ACCEPTED', 'remote_pixels_verified':False,
                 'preview_present_before':bool(before_preview), 'preview_changed':True,
                 **(douyin_slot_record(how) if how else {})}})


async def douyin_finish_after_covers(page, tx, before, caption, title, editor):
    """The rest of the form once its covers are on or left out: the declaration, who sees it, its collection and time, the title and words
    read back, and the one press on 发布."""
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
    if await title.input_value() != tx.options['title']:
        raise VpubError('metadata_mismatch', "Douyin's title differs from the plan, so nothing was published.")
    differs = douyin_form_differs(caption, await editor.inner_text())
    if differs:
        raise VpubError('metadata_mismatch', f"Douyin's description differs from the plan, so nothing was published: {differs}.",
                        'vpub resume opens the kept draft and writes the description again.')
    tx.save(phase='preview', metadata_readback=True, ai_disclosure='native_control', visibility=tx.options['visibility'])
    if tx.options.get('submission_mode') == 'draft_only':
        return await douyin_keep_draft(page, tx)
    post = await unique_visible(page.get_by_role('button', name='发布', exact=True), 'Douyin final publish')
    await tx.submit(page, post)
    # From the field run on fc60a30 and a review of that: a post that went out without a cover, with --no-cover, or with --cover in a slot
    # other than the one that stopped, kept a stopped run's account of what lay over a slot. It goes once 发布 has been pressed, not before,
    # so a run that stops short of that keeps it.
    if 'cover_slot_covered' in tx.receipt:
        tx.receipt.pop('cover_slot_covered')
        tx.save()
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
YT_LOCAL_ZONE = re.compile(
    r'(?:(?:local time ?zone|本地时区|当地时区|本地時區|當地時區)[^()]*\(\s*GMT'
    r'|默认设置为本地时区\s*\(\s*格林尼治标准时间)\s*([+\-−]\s*(?:[01]?\d|2[0-3])(?::?[0-5]\d)?)\s*\)', re.I)
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
    shown = (await scheduler.inner_text()).replace('格林尼治标准时间', 'GMT')
    names, unreadable = studio_zone_names(shown)
    if names or unreadable:
        return yt_schedule_zone(shown, when)
    hidden = await scheduler.evaluate('e => e.textContent')
    default = set(YT_LOCAL_ZONE.findall(hidden if isinstance(hidden, str) else ''))
    if len(default) != 1:
        return yt_schedule_zone(shown, when)
    zone = await page.evaluate(BROWSER_ZONE)
    return yt_schedule_zone(f'{shown}\n(GMT{next(iter(default))})', when, zone if isinstance(zone, str) else None)


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
        lines = [line.strip() for line in (await window.inner_text()).splitlines() if line.strip()] if window else []
        if lines[:1] == [YT_ZH_SCHEDULE_HEADING]:
            evidence = lines[:2]
            for label, link in zip(lines, lines[1:]):
                if label == '视频链接':
                    evidence.extend((label, link))
            tx.save(studio_confirmation='\n'.join(evidence)[:400])
        else:
            tx.save(studio_confirmation=' '.join(lines[:2])[:200])
    elif not answer:
        tx.save(after_press=await page.evaluate(YT_OPEN_DIALOGS))


async def yt_finish_draft(page, tx, video_id):
    """Reopen the independently observed draft by its recorded ID, without selecting a file."""
    options = tx.options
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
        draft_id = tx.receipt.get('draft_id')
        video_id = tx.receipt.get('id') or draft_id
        if not isinstance(video_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id) or draft_id not in (None, video_id):
            raise VpubError('wrong_work', 'The saved receipt does not identify one YouTube draft. Nothing was opened or uploaded.')
        if tx.receipt.get('id') or tx.receipt.get('metadata_readback') is True:
            return await yt_finish_draft(page, tx, video_id)
        # An early verify can find the draft before audience, disclosure and other details were checked.
        # Reopen it through the normal details form below. Never fall back to the file picker for it.
        if not tx.resuming or not tx.receipt.get('media_selected'):
            raise VpubError('inspect_existing_draft', 'This draft has incomplete upload evidence. No file was selected.')
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
            # Said as vpub status says it: a new plan for the same video would only find this attempt.
            raise VpubError('cover_unavailable', 'YouTube offers no custom thumbnail on this channel yet. The upload is kept as a draft; nothing was published.',
                            "YouTube Studio's settings say why, under the channel's feature eligibility. The person either turns it on there and then "
                            f"runs vpub resume {tx.plan['confirmation']}, or runs vpub resume {tx.plan['confirmation']} --no-cover to finish without it.")
        await choose_file(page, page.locator(f'{YT_DIALOG} input#file-loader').first, tx.cover())
        await page.wait_for_timeout(2000)
        # From a review: Google's check can come as the thumbnail goes in, and the presses after it would only meet its window.
        await risk_check(page)
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
