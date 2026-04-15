# Web3 Builder Daily Digest — Telegram Bot

Daily technical crypto digest aggregated from **free primary sources** (EthResear.ch, Ethereum Magicians, Vitalik's blog, The Block, DeFiLlama, GitHub trending), summarized by **Google Gemini 2.5 Flash Lite** (free tier) with **Google Search grounding**, and delivered to your Telegram every morning.

After the daily push, just chat with the bot in Telegram — it has today's data as context and Gemini can websearch EIPs, docs, GitHub, DeFiLlama, etc. to enrich answers.

> **Why not Messari?** Messari's API content (News, Research, Intel) is gated behind enterprise sales — no self-serve tier gives access. For a _builder_ profile, primary sources (EthResearch, EIPs, blogs, DeFiLlama) are strictly more technical than Messari's news aggregator anyway.

## Features

- **Daily digest push** on Telegram — focus DeFi / L2 / infra / security / tokenomics / governance
- **Interactive chat** — any text message = question over today's data, grounded with live Google Search
- **Commands**
    - `/digest` — resend today's digest (generates if missing)
    - `/refresh` — re-fetch all sources and regenerate
    - `/reset` — clear conversation history
- **Per-day storage** (JSON + digest markdown) on a Railway volume
- **Single-user** — only the configured `TELEGRAM_CHAT_ID` is answered

## Sources

| Source             | Type                                            | URL                                        |
| ------------------ | ----------------------------------------------- | ------------------------------------------ |
| EthResear.ch       | research                                        | https://ethresear.ch/latest.rss            |
| Ethereum Magicians | EIP discussions                                 | https://ethereum-magicians.org/latest.rss  |
| Vitalik Buterin    | blog                                            | https://vitalik.eth.limo/feed.xml          |
| The Block          | news (RSS)                                      | https://www.theblock.co/rss.xml            |
| DeFiLlama          | TVL metrics                                     | https://api.llama.fi/protocols             |
| GitHub             | trending repos (topic: ethereum/solana/defi/zk) | https://api.github.com/search/repositories |

All free, no keys required.

## Project layout

```
messari-digest-bot/
├── app/
│   ├── config.py          # env vars (pydantic-settings)
│   ├── storage.py         # daily JSON + digest files
│   ├── sources_client.py  # RSS + DeFiLlama + GitHub fetch (async, httpx)
│   ├── summarizer.py      # Gemini digest + grounded chat
│   ├── prompts.py         # digest + chat system prompts
│   ├── telegram_bot.py    # handlers + daily push
│   └── main.py            # entrypoint (polling + daily JobQueue)
├── requirements.txt
├── .env.example
├── Procfile
├── railway.json
└── README.md
```

## Setup — local

```bash
git clone <this repo>
cd messari-digest-bot
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env with your keys
python -m app.main
```

### Required keys

| Var                  | Where                                                     |
| -------------------- | --------------------------------------------------------- |
| `GEMINI_API_KEY`     | https://aistudio.google.com/apikey (free)                 |
| `TELEGRAM_BOT_TOKEN` | [@BotFather](https://t.me/BotFather) → `/newbot`          |
| `TELEGRAM_CHAT_ID`   | [@userinfobot](https://t.me/userinfobot) — copy your `Id` |

Optional: `DATA_DIR` (default `./data`), `SCHEDULE_HOUR`, `SCHEDULE_MINUTE`, `SCHEDULE_TZ`, `GEMINI_MODEL`.

### Test locally

1. `python -m app.main` → logs show `Daily digest scheduled at 08:00 Asia/Bangkok.`
2. In Telegram → start chat with your bot → `/start`
3. `/refresh` — forces full fetch + Gemini digest right now
4. Send a free-form question, e.g. _"Implications of the latest EIP for a Solana dev?"_ — Gemini answers with today's data + websearch.

## Extending

- Add more sources → append to `RSS_SOURCES` in `app/sources_client.py`
- Change digest structure → edit `DIGEST_PROMPT` in `app/prompts.py`
- Swap Gemini model → `GEMINI_MODEL=gemini-2.5-flash` (may hit rate limits on free tier) or `gemini-flash-latest`
- Multi-day history → replace `app/storage.py` with a Postgres adapter
