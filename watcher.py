import json
import os
from pathlib import Path

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

        last_seen = state["youtube"].get(channel_id)

        # First run: store current latest, don't send old videos
        if last_seen is None:
            state["youtube"][channel_id] = video_id
            changed = True
            continue

        if video_id and video_id != last_seen:
            send_discord(
                f"📺 **New upload by {author}**\n{url}",
                {
                    "title": title,
                    "url": url,
                    "description": f"New YouTube upload from **{author}**",
                    "color": 16711680
                }
            )
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
    return data.get("data", {})


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
            "discount_percent": price.get("discount_percent"),
            "final": price.get("final"),
            "currency": price.get("currency"),
            "url": f"https://store.steampowered.com/app/{app_id}/"
        }

        previous = state["steam"].get(app_id)

        # First run: store current state, don't send old deals
        if previous is None:
            state["steam"][app_id] = current
            changed = True
            continue

        became_free = (not previous.get("is_free")) and current.get("is_free")
        discount_changed = previous.get("discount_percent") != current.get("discount_percent")
        final_changed = previous.get("final") != current.get("final")

        if became_free or discount_changed or final_changed:
            if current["is_free"]:
                content = f"🎮 **Steam update: {current['name']}**\nNow **free** on Steam!\n{current['url']}"
                description = "This game is now free on Steam."
            else:
                content = (
                    f"🎮 **Steam update: {current['name']}**\n"
                    f"Discount: **-{current.get('discount_percent', 0)}%**\n"
                    f"{current['url']}"
                )
                description = (
                    f"Current discount: -{current.get('discount_percent', 0)}%\n"
                    f"Final price: {current.get('final')} {current.get('currency', '')}"
                )

            send_discord(
                content,
                {
                    "title": current["name"],
                    "url": current["url"],
                    "description": description,
                    "color": 1774904
                }
            )

            state["steam"][app_id] = current
            changed = True

    return changed


def main():
    state = load_state()

    yt_changed = check_youtube(state)
    steam_changed = check_steam(state)

    if yt_changed or steam_changed:
        save_state(state)


if __name__ == "__main__":
    main()