import json
import os
from pathlib import Path
from datetime import datetime

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


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"youtube": {}, "steam": {}}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def send_discord(content, embed=None):
    payload = {"content": content}
    if embed:
        payload["embeds"] = [embed]

    r = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=20)
    r.raise_for_status()


def check_youtube(state):
    changed = False

    for channel_id in YOUTUBE_CHANNEL_IDS:
        feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
        feed = feedparser.parse(feed_url)

        if not feed.entries:
            continue

        latest = feed.entries[0]

        video_id = latest.get("yt_videoid") or latest.get("videoid")
        title = latest.get("title", "New upload")
        url = latest.get("link")
        author = latest.get("author", channel_id)
        published = latest.get("published", "")

        last_seen = state["youtube"].get(channel_id)

        if last_seen != video_id:
            thumbnail = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"

            embed = {
                "title": f"{author} posted a new video",
                "url": url,
                "description": f"**{title}**\n\n{published}",
                "color": 0xFF0000,
                "thumbnail": {"url": thumbnail},
            }

            send_discord("A new Video is live on Youtube", embed)

            state["youtube"][channel_id] = video_id
            changed = True

    return changed



def fetch_steam_details(app_id):
    r = requests.get(
        STEAM_STORE_API,
        params={"appids": app_id, "cc": "de", "l": "en"},
        timeout=20
    )
    r.raise_for_status()
    data = r.json().get(str(app_id), {})

    if not data.get("success"):
        return None

    details = data.get("data", {})

    discount_end = None

    if "price_overview" in details:
        pov = details["price_overview"]
        discount_end = pov.get("discount_expiration")  # VERY rare

    details["_discount_end"] = discount_end
    return details


def format_price(cents, currency):
    if cents is None:
        return "N/A"
    return f"{cents/100:.2f} {currency}"

def format_end_date(timestamp):
    if not timestamp:
        return "Ends: Unknown"

    try:
        dt = datetime.utcfromtimestamp(timestamp)
        return dt.strftime("Ends: %d.%m.%Y %H:%M UTC")
    except:
        return "Ends: Unknown"


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
                    "thumbnail": {"url": current["image"]},
                    "footer": {"text": footer_text}
                }

            elif current["discount_percent"] > 0:
                original = format_price(current["initial"], current["currency"])
                new = format_price(current["final"], current["currency"])
                footer_text = format_end_date(details.get("_discount_end"))

                content = "Steam Sale"
                embed = {
                    "title": f"{current['name']} is on SALE",
                    "url": current["url"],
                    "description": (
                        f"**-{current['discount_percent']}%**\n\n"
                        f"~~{original}~~ → **{new}**"
                    ),
                    "color": 0xFFFF00,
                    "thumbnail": {"url": current["image"]},
                    "footer": {"text": footer_text}
                }

            if embed:
                send_discord(content or "", embed)

            state["steam"][app_id] = current
            changed = True
            continue

        became_free = (not previous["is_free"]) and current["is_free"]

        discount_started = (
                previous["discount_percent"] == 0 and current["discount_percent"] > 0
        )

        discount_changed = (
                previous["discount_percent"] != current["discount_percent"]
                and current["discount_percent"] > 0
        )

        price_back_to_normal = (
                previous["discount_percent"] > 0 and current["discount_percent"] == 0
        )

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
                "thumbnail": {"url": current["image"]},
                "footer": {"text": footer_text}
            }

        elif discount_started or discount_changed:
            original = format_price(current["initial"], current["currency"])
            new = format_price(current["final"], current["currency"])
            footer_text = format_end_date(details.get("_discount_end"))

            content = "Steam Sale"
            embed = {
                "title": f"{current['name']} is on SALE",
                "url": current["url"],
                "description": (
                    f"**-{current['discount_percent']}%**\n\n"
                    f"~~{original}~~ → **{new}**"
                ),
                "color": 0xFFFF00,
                "thumbnail": {"url": current["image"]},
                "footer": {"text": footer_text}
            }

        elif price_back_to_normal:
            original = format_price(current["final"], current["currency"])

            content = "Steam Price Update"
            embed = {
                "title": f"{current['name']} back to normal price",
                "url": current["url"],
                "description": f"Now costs **{original}**",
                "color": 0xFF0000,
                "thumbnail": {"url": current["image"]}
            }

        if embed:
            send_discord(content or "", embed)
            state["steam"][app_id] = current
            changed = True
        else:
            state["steam"][app_id] = current

    return changed


def main():
    state = load_state()

    yt_changed = check_youtube(state)
    steam_changed = check_steam(state)

    if yt_changed or steam_changed:
        save_state(state)


if __name__ == "__main__":
    main()