"""X's pages, on its rebuilt site and the earlier one.

On 2026-10-31 signed-out visitors got the rebuilt site while a signed-in account still got the earlier one, so both are
supported. The rebuilt site has almost no test labels, so controls there are found by the icons and addresses the site
itself uses, which stay the same whatever language the account is in. Only the earlier site has been tried while signed in.
"""
from datetime import datetime, timezone
import re

from .core import VpubError

LIKE_OFF = 'button[data-testid="like"], [role="group"] button[aria-pressed="false"]:has(use[href^="#icon-heart"])'
LIKE_ON = 'button[data-testid="unlike"], [role="group"] button[aria-pressed="true"]:has(use[href^="#icon-heart"])'
SAVE_OFF = 'button[data-testid="bookmark"], [role="group"] button[aria-pressed="false"]:has(use[href^="#icon-bookmark"])'
SAVE_ON = 'button[data-testid="removeBookmark"], [role="group"] button[aria-pressed="true"]:has(use[href^="#icon-bookmark"])'
REPLY = '[data-testid="reply"], [role="group"] a[href^="/compose/post?in_reply_to="]'
REPOST = '[data-testid="retweet"], [data-testid="unretweet"], [role="group"] button:has(use[href^="#icon-repost"])'
MORE = '[data-testid="caret"], button[data-slot="xds-menu-trigger"]:has(use[href^="#icon-more-dots"])'
# A signed-in account's window nests one dialog inside another, so this means the outer one, which holds every control.
COMPOSER = '[role="dialog"]:has([contenteditable="true"]):not([role="dialog"] [role="dialog"])'
TEXT_BOX = '[data-testid="tweetTextarea_0"], [contenteditable="true"]:not([data-testid="tweetTextarea_0"] *)'
SEND_REPLY = re.compile(r"^(Reply|回复|回覆)$")
HIDE_REPLY = re.compile(r"^(Hide reply|隐藏回复|隱藏回覆)$")
UNHIDE_REPLY = re.compile(r"^(Unhide reply|取消隐藏回复|取消隱藏回覆)$")
DELETE_POST = re.compile(r"^(Delete|删除|刪除)$")
GONE_PAGE = re.compile(r"doesn.t exist|does not exist|was deleted|不存在|已被删除|已被刪除|已删除|已刪除", re.I)
EDIT_POST = re.compile(r"^(Edit( post)?|编辑(帖子)?|修改(帖子)?|編輯(貼文)?)$")
UPDATE_POST = re.compile(r"^(Update|更新)$")
# A video's picture is stored under the number Media Studio's address gives that video.
VIDEO_NUMBER = re.compile(r"/(?:amplify_video_thumb|ext_tw_video_thumb)/(\d+)/")
AD_LINES = {"Promoted", "Ad", "广告", "廣告", "推广", "推廣"}
VIEWS = re.compile(r"(\d[\d,]*)\s*(?:views?\b|次观看|次觀看|观看|觀看|次浏览|次瀏覽|浏览|瀏覽|次查看)", re.I)
# What one post on a page says about itself. The action row names the post exactly, and a quoted post has no action row.
POST_FIELDS = r"""article => {
  const links = [...article.querySelectorAll('a[href*="/status/"]')];
  const reply = article.querySelector('[role="group"] a[href^="/compose/post?in_reply_to="]');
  const id = reply ? reply.getAttribute('href').match(/in_reply_to=(\d+)/)?.[1] : null;
  const own = id ? links.find(a => a.getAttribute('href').endsWith('/status/' + id)) : links.find(a => a.querySelector('time'));
  const outside = e => !e.closest('a,button,[role="group"],[role="link"]');
  const earlier = article.querySelector('[data-testid="tweetText"], [data-testid="User-Name"]');
  const block = earlier ? [...article.querySelectorAll('[data-testid="tweetText"]')].find(outside)
    : [...article.querySelectorAll('div[dir="auto"]')].find(e => outside(e) && !e.querySelector('a[href*="/status/"]') && e.innerText.trim());
  return {href: own ? own.getAttribute('href') : null, text: block ? block.innerText : null,
          time: own?.querySelector('time')?.getAttribute('datetime') || null,
          video: !!article.querySelector('video, [data-testid="videoPlayer"], [data-testid="videoComponent"]'),
          poster: article.querySelector('video')?.getAttribute('poster') || null,
          lines: article.innerText.split('\n').map(line => line.trim())};
}"""


async def rebuilt(page):
    """Which site this account gets, read from any signed-in page."""
    return not await page.locator('a[data-testid="AppTabBar_Profile_Link"]').count()


async def open_menu(page, button):
    """A post's own menu. On a signed-in account a click can open nothing while the keyboard does, so it gets both and then stops."""
    await button.click()
    menus = page.get_by_role("menu")
    for attempt in range(2):
        try:
            await menus.first.wait_for(state="visible", timeout=3000)
            return menus
        except Exception:
            if not attempt:
                await button.press("Enter")
    raise VpubError("layout_changed", "X did not open this post's menu; nothing changed.")


def post_row(page, post):
    """The article for exactly this post."""
    return page.locator(f'article:has(a[href="/compose/post?in_reply_to={post}"]), article:has(a[href$="/status/{post}"] time)')


def post_time(post):
    """X's post IDs carry the moment they were made, and the rebuilt site prints the time only in words."""
    return datetime.fromtimestamp(((int(post) >> 22) + 1288834974657) / 1000, timezone.utc).isoformat()


async def post_fields(article):
    data = await article.evaluate(POST_FIELDS)
    found = re.fullmatch(r"/([A-Za-z0-9_]+)/status/(\d+)", data["href"] or "")
    if not found:
        return None
    media = VIDEO_NUMBER.search(data.get("poster") or "")
    return {"author": found[1], "id": found[2], "href": data["href"], "text": data["text"], "published_at": data["time"] or post_time(found[2]),
            "has_video": bool(data.get("video")), "media_id": media[1] if media else None, "is_ad": bool(AD_LINES & set(data["lines"]))}


async def own_posts(page, identity):
    """This account's posts on the page now open, each read from its own row: its words, its moment and its address."""
    posts = [await post_fields(row) for row in await page.locator("main article").all()]
    return [post for post in posts if post and post["author"].casefold() == identity.casefold() and not post["is_ad"]]


async def views_of(row):
    """The action row's own summary holds the exact view count, in the account's language."""
    group = row.locator('[role="group"]')
    label = await group.first.get_attribute("aria-label") if await group.count() else None
    found = VIEWS.search(label or "")
    return found[1].replace(",", "") if found else None
