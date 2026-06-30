import json
import os
import requests
import feedparser

STATE_FILE = "state.json"
DISCORD_WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]

CHANNELS = [
    {
        "name": "Fireship",
        "feed_url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCsBjURrPoezykLs9EqgamOA"
    },
    {
        "name": "ThePrimeTime",
        "feed_url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCUyeluBRhGPCW4rPe_UvBZQ"
    }
]

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)

def send_discord(title, url, author):
    payload = {
        "content": f"📺 **New upload by {author}**\n{url}",
        "embeds": [
            {
                "title": title,
                "url": url,
                "description": f"New YouTube upload from **{author}**",
                "color": 16711680
            }
        ]
    }
    r = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=20)
    r.raise_for_status()

def main():
    state = load_state()

    for channel in CHANNELS:
        feed = feedparser.parse(channel["feed_url"])
        if not feed.entries:
            continue

        latest = feed.entries[0]
        video_id = latest.get("yt_videoid") or latest.get("videoid")
        title = latest.get("title", "New upload")
        url = latest.get("link")
        author = latest.get("author", channel["name"])

        last_seen = state.get(channel["feed_url"])

        if last_seen is None:
            state[channel["feed_url"]] = video_id
            continue

        if video_id and video_id != last_seen:
            send_discord(title, url, author)
            state[channel["feed_url"]] = video_id

    save_state(state)

if __name__ == "__main__":
    main()