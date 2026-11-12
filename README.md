# OsteoCare AI – Backend

FastAPI backend for the OsteoCare AI website
(https://osteocare-ai-mobility-monitor.packedsesame1.chatgpt.site/).
It matches the exact API the website already calls, so no frontend change is needed.

- **Login:** Supabase Auth (the website signs users in; the backend checks their token)
- **Database:** Supabase Postgres (or a local SQLite file for testing)
- **Sensor data:** ESP32 insole → `POST /api/ingest` → stored → pushed live to the website over WebSocket
- **Reports:** rule-based analysis of heel / ball / toe signals + PDF download (no diagnosis)

## API

| Method | Path | Used by | What it does |
|---|---|---|---|
| GET | `/api/devices` | website | Your paired insoles (`id`, `name`, `online`, …) |
| POST | `/api/devices` | register script | Pair an insole, returns its `device_key` once |
| DELETE | `/api/devices/{id}` | – | Remove an insole and its data |
| POST | `/api/ingest` | ESP32 | Store readings. Header `X-Device-Key`. Body `{heel, ball, toe}` or `{readings:[{heel,ball,toe,age_ms}]}` |
| GET | `/api/readings?device_id=&days=1\|7\|30` | website | `{readings:[…newest first], truncated}` (max 50,000) |
| GET/POST | `/api/sessions` | website | List / start walking session (`{device_id}`; insole must be online) |
| POST | `/api/sessions/{id}/stop` | website | Stop a session |
| GET/POST | `/api/reports` | website | List / generate report (`{device_id, period: day\|week\|month}`) |
| GET | `/api/reports/{id}/pdf` | website | Download the report as PDF |
| GET/PUT | `/api/profile` | website | `{name, daily_goal}` (goal 1–240 min) |
| WS | `/ws/live/{device_id}` | website | Send `{token}`, receive `{online, reading}` (heartbeat every 3 s) |

All `/api/*` routes except `/api/ingest` need `Authorization: Bearer <Supabase access token>`.
Interactive docs: `https://<your-backend>/docs`.

## 1. Create the Supabase project (free)
1. supabase.com → New project. Save the database password.
2. **Project Settings → API**: copy the *Project URL* and the *anon / publishable* key.
3. **Project Settings → Database → Connection string → URI** (choose *Session pooler*): copy it, put your password in it.
4. **Authentication → Providers → Email**: keep enabled. For testing you can turn off "Confirm email".

Tables are created automatically the first time the backend starts.

## 2. Run locally
```bash
cd osteocare-backend
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # fill in SUPABASE_URL, SUPABASE_ANON_KEY (DATABASE_URL optional locally)
pytest -q                   # runs the API tests on a temporary SQLite database
uvicorn app.main:app --reload --port 8000
```
Open http://localhost:8000/docs.

## 3. Deploy (Render, free)
1. Push this folder to a GitHub repo.
2. render.com → New → Blueprint → pick the repo (it reads `render.yaml`).
3. Set `DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`.
4. When it's live you get a URL like `https://osteocare-api.onrender.com`. Opening it should show `{"status":"ok"}`.

Keep **one worker** (`--workers 1`): live updates are shared in memory.
Render's free plan sleeps after 15 min idle; the first request then takes ~30 s.

## 4. Connect the website
On the website click **Set up connection** and enter:
- FastAPI backend URL → your Render URL
- Supabase project URL → from step 1
- Supabase anon key → from step 1

Then create an account / sign in on the website.

## 5. Pair the insole
```bash
python scripts/register_device.py --api https://osteocare-api.onrender.com \
  --supabase https://your-project.supabase.co --anon YOUR_ANON_KEY \
  --email you@example.com --name "My insole"
```
It prints a `device_key` (shown only once).

## 6. Flash the ESP32
Open `firmware/osteocare_insole/osteocare_insole.ino` in Arduino IDE, set Wi-Fi name/password,
`API_URL` (`https://<your-backend>/api/ingest`) and `DEVICE_KEY`, then upload.
Wiring: `3V3 – FSR – GPIO – 10 kΩ – GND` for heel (GPIO34), ball (GPIO35), toe (GPIO32).
The Serial Monitor (115200) shows `sent 10 samples …` once it works, and the website switches to online.

## Notes
- 10 samples/s ≈ 36,000 rows per hour of wearing. Supabase free tier holds 500 MB, so delete old
  readings now and then, or lower the rate in the firmware (`SAMPLE_MS`).
- Reports describe sensor patterns only. They are not a medical diagnosis or a measure of knee force.
