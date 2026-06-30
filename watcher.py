import json
import os
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from email.utils import parsedate_to_datetime

import feedparser
import requests

STATE_FILE = Path("state.json")
DISCORD_WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]

YOUTUBE_CHANNEL_IDS = [
    x.strip() for x in os.environ.get("YOUTUBE_CHANNEL_IDS", "").split(",") if x.strip()
]

STEAM_APP_IDS = [
    x.strip() for x in os.environ.get("STEAM_APP_IDS", "").split(",") if x.strip()
]

STEAM_STORE_API = "https://store.steampowered.com/api/appdetails"
MESSAGE_QUEUE = []
SESSION = requests.Session()

try:
    from zoneinfo import ZoneInfo
    BERLIN_TZ = ZoneInfo("Europe/Berlin")
except Exception:
    BERLIN_TZ = None


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"youtube": {}, "steam": {}}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def queue_discord(content, embed=None):
    MESSAGE_QUEUE.append({"content": content, "embed": embed})


def flush_queue():
    for msg in MESSAGE_QUEUE:
        payload = {"content": msg["content"]}
        if msg["embed"]:
            payload["embeds"] = [msg["embed"]]

        while True:
            r = SESSION.post(DISCORD_WEBHOOK_URL, json=payload, timeout=30)

            if r.status_code in (200, 204):
                break

            if r.status_code == 429:
                retry_after = 1
                try:
                    data = r.json()
                    retry_after = data.get("retry_after", 1)
                    if retry_after > 50:
                        retry_after = retry_after / 1000.0
                except Exception:
                    retry_after = 1
                time.sleep(retry_after)
                continue

            r.raise_for_status()

        time.sleep(0.35)


def fetch_youtube_latest(channel_id):
    feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    feed = feedparser.parse(feed_url)

    if not feed.entries:
        return channel_id, None

    latest = feed.entries[0]
    video_id = latest.get("yt_videoid") or latest.get("videoid")
    title = latest.get("title", "New upload")
    url = latest.get("link")
    author = latest.get("author", channel_id)
    published = latest.get("published", "")
    thumbnail = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg" if video_id else None

    return channel_id, {
        "video_id": video_id,
        "title": title,
        "url": url,
        "author": author,
        "published": published,
        "thumbnail": thumbnail
    }


def check_youtube(state):
    changed = False

    max_workers = min(24, max(1, len(YOUTUBE_CHANNEL_IDS)))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(fetch_youtube_latest, channel_id) for channel_id in YOUTUBE_CHANNEL_IDS]

        for future in as_completed(futures):
            channel_id, latest = future.result()
            if not latest or not latest["video_id"]:
                continue

            last_seen = state["youtube"].get(channel_id)

            if last_seen != latest["video_id"]:
                embed = {
                    "title": f"{latest['author']} posted a new video",
                    "url": latest["url"],
                    "description": f"**{latest['title']}**\n\n{format_youtube_date(latest['published'])}",
                    "color": 0xFF0000
                }

                if latest["thumbnail"]:
                    embed["thumbnail"] = {"url": latest["thumbnail"]}

                queue_discord("A new Video is live on Youtube", embed)

                state["youtube"][channel_id] = latest["video_id"]
                changed = True

    return changed


def fetch_steam_details(app_id):
    r = SESSION.get(
        STEAM_STORE_API,
        params={"appids": app_id, "cc": "de", "l": "en"},
        timeout=30
    )
    r.raise_for_status()
    data = r.json().get(str(app_id), {})

    if not data.get("success"):
        return None

    details = data.get("data", {})
    discount_end = None

    if "price_overview" in details:
        pov = details["price_overview"]
        discount_end = pov.get("discount_expiration")

    details["_discount_end"] = discount_end
    return details


def format_price(cents, currency):
    if cents is None:
        return "N/A"
    if not currency:
        return f"{cents / 100:.2f}"
    return f"{cents / 100:.2f} {currency}"


def format_end_date(timestamp):
    if not timestamp:
        return "Ende: Unbekannt"

    try:
        if BERLIN_TZ:
            dt = datetime.fromtimestamp(timestamp, tz=BERLIN_TZ)
            return dt.strftime("%H:%M / %d.%m.%Y %Z")
        else:
            dt = datetime.utcfromtimestamp(timestamp)
            return dt.strftime("%H:%M / %d.%m.%Y UTC")
    except Exception:
        return "Ende: Unbekannt"


def format_youtube_date(published):
    if not published:
        return "Unbekannt"

    try:
        dt = parsedate_to_datetime(published)
        if BERLIN_TZ:
            if dt.tzinfo is None:
                # assume UTC if no tz provided
                dt = dt.replace(tzinfo=ZoneInfo("UTC"))
            dt = dt.astimezone(BERLIN_TZ)
            return dt.strftime("%H:%M / %d.%m.%Y %Z")
        else:
            return dt.strftime("%H:%M / %d.%m.%Y")
    except Exception:
        try:
            dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
            if BERLIN_TZ:
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=ZoneInfo("UTC"))
                dt = dt.astimezone(BERLIN_TZ)
                return dt.strftime("%H:%M / %d.%m.%Y %Z")
            else:
                return dt.strftime("%H:%M / %d.%m.%Y")
        except Exception:
            return published



def check_steam(state):
    changed = False

    for app_id in STEAM_APP_IDS:
        details = fetch_steam_details(app_id)
        if not details:
            continue

        price = details.get("price_overview") or {}

        current = {
            "name": details.get("name", f"App {app_id}"),
            "is_free": bool(details.get("is_free")),
            "discount_percent": price.get("discount_percent") or 0,
            "initial": price.get("initial"),
            "final": price.get("final"),
            "currency": price.get("currency"),
            "url": f"https://store.steampowered.com/app/{app_id}/",
            "image": details.get("header_image")
        }

        previous = state["steam"].get(app_id)

        if previous is None:
            embed = None
            content = None

            if current["is_free"]:
                original_price = format_price(current.get("initial"), current["currency"])
                footer_text = format_end_date(details.get("_discount_end"))

                content = "A wishlisted game is now FREE! @everyone"
                embed = {
                    "title": f"{current['name']} is FREE",
                    "url": current["url"],
                    "description": f"~~{original_price}~~ → **FREE**",
                    "color": 0x00FF00,
                    "footer": {"text": footer_text}
                }

                if current["image"]:
                    embed["thumbnail"] = {"url": current["image"]}

            elif current["discount_percent"] > 0:
                original = format_price(current["initial"], current["currency"])
                new = format_price(current["final"], current["currency"])
                footer_text = format_end_date(details.get("_discount_end"))

                content = "Steam Sale"
                embed = {
                    "title": f"{current['name']} is on SALE",
                    "url": current["url"],
                    "description": f"**-{current['discount_percent']}%**\n\n~~{original}~~ → **{new}**",
                    "color": 0xFFFF00,
                    "footer": {"text": footer_text}
                }

                if current["image"]:
                    embed["thumbnail"] = {"url": current["image"]}

            if embed:
                queue_discord(content or "", embed)

            state["steam"][app_id] = current
            changed = True
            continue

        became_free = (not previous["is_free"]) and current["is_free"]
        discount_started = previous["discount_percent"] == 0 and current["discount_percent"] > 0
        discount_changed = previous["discount_percent"] != current["discount_percent"] and current["discount_percent"] > 0
        price_back_to_normal = previous["discount_percent"] > 0 and current["discount_percent"] == 0

        embed = None
        content = None

        if became_free:
            original_price = format_price(previous.get("final") or current.get("initial"), current["currency"])
            footer_text = format_end_date(details.get("_discount_end"))

            content = "A wishlisted game is now FREE! @everyone"
            embed = {
                "title": f"{current['name']} is FREE",
                "url": current["url"],
                "description": f"~~{original_price}~~ → **FREE**",
                "color": 0x00FF00,
                "footer": {"text": footer_text}
            }

            if current["image"]:
                embed["thumbnail"] = {"url": current["image"]}

        elif discount_started or discount_changed:
            original = format_price(current["initial"], current["currency"])
            new = format_price(current["final"], current["currency"])
            footer_text = format_end_date(details.get("_discount_end"))

            content = "Steam Sale"
            embed = {
                "title": f"{current['name']} is on SALE",
                "url": current["url"],
                "description": f"**-{current['discount_percent']}%**\n\n~~{original}~~ → **{new}**",
                "color": 0xFFFF00,
                "footer": {"text": footer_text}
            }

            if current["image"]:
                embed["thumbnail"] = {"url": current["image"]}

        elif price_back_to_normal:
            original = format_price(current["final"], current["currency"])

            content = "Steam Price Update"
            embed = {
                "title": f"{current['name']} back to normal price",
                "url": current["url"],
                "description": f"Now costs **{original}**",
                "color": 0xFF0000
            }

            if current["image"]:
                embed["thumbnail"] = {"url": current["image"]}

        if embed:
            queue_discord(content or "", embed)
            state["steam"][app_id] = current
            changed = True
        else:
            state["steam"][app_id] = current

    return changed


def queue_final_message():
    if BERLIN_TZ:
        now_text = datetime.now(BERLIN_TZ).strftime("%d.%m.%Y %H:%M:%S %Z")
    else:
        now_text = datetime.utcnow().strftime("%d.%m.%Y %H:%M:%S UTC")

    embed = {
        "title": "----------- THIS IS EVERYTHING -----------",
        "description": now_text,
        "color": 0x5865F2
    }
    queue_discord("", embed)


def main():
    state = load_state()

    yt_changed = check_youtube(state)
    steam_changed = check_steam(state)

    if yt_changed or steam_changed:
        save_state(state)

    queue_final_message()
    flush_queue()


if __name__ == "__main__":
    main()
