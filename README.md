# HomeBase — a relocation agent powered by the Qloo Taste AI API

Find the city that fits your taste, then feel at home in it.

HomeBase is built for the Qloo Agentic Hackathon on Devpost. It is for
international students, immigrants and remote workers who are relocating:
people who know what they love, but not where they belong yet.

Built with Python (Flask) + vanilla JavaScript. The backend is Python;
the frontend is plain HTML/CSS/JS served by Flask.

## How it works

**Act 1 — Where should I live?** Enter 3 to 5 things you love (an artist,
a film, a cuisine, a brand). HomeBase resolves each to a Qloo entity and
asks Qloo's taste graph to rank cities by cultural fit. You get the top 5
cities, each with a fit score and the reason it fits, taken from Qloo's own
explainability data — never invented by the language model.

**Act 2 — Feel at home.** Pick a city and HomeBase maps your home
favourites to local equivalents in the new city, then builds a first-month
plan: four weeks of places to eat, go out, and find your people. Every
place comes from a real Qloo API call for that city. If Qloo's data for a
city is thin, HomeBase says so instead of inventing places.

Gemini only composes the story from Qloo results. It never invents a place.

## Run it

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # then fill in your keys
python -m server.app             # http://localhost:3000
```

Both keys are required: without the Qloo key the app cannot rank cities
or find places, and without the Gemini key there is no narration. There is
no demo mode — every result comes from the live Qloo API.

## Environment variables

| Variable | What it is |
| --- | --- |
| `QLOO_API_KEY` | Qloo Taste AI API key (hackathon key, base URL `https://hackathon.api.qloo.com`). Required. |
| `GEMINI_API_KEY` | Google Gemini API key (free tier) for first-month narration. Required. |
| `GEMINI_MODEL` | Optional, default `gemini-2.5-flash`. |
| `PORT` | Optional, default `3000`. Render sets this automatically. |

Keys are never committed. See `.env.example`.

## API

- `GET /api/health` — `{ok, qlooLive, llmLive}`
- `POST /api/cities` — body `{tastes: [{kind, label}]}` (3 to 5 tastes), returns `{ok, meta, cities: [...], note}`
- `POST /api/first-month` — body `{destination, tastes}`, returns `{ok, meta, plan: {weeks: [...]}, matches: [...]}`

## Deploy

Render free tier, Python web service (see `render.yaml`):

- Build command: `pip install -r requirements.txt`
- Start command: `gunicorn server.app:app --bind 0.0.0.0:$PORT`
- Set `QLOO_API_KEY` and `GEMINI_API_KEY` in the Render dashboard (never in the repo).

## Licence

MIT — see `LICENSE`.
