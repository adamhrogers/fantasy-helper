# Adding an ESPN league (and finding your cookies)

ESPN's API is unofficial, so private leagues are read with the two login cookies your browser already has. `fh league add` asks for them at a **hidden prompt**, checks them against ESPN by loading the league, then stores them **encrypted** in the database (ADR 0007). Never paste them into chat, a file or `.env`.

## 1. Get the league ID

Open the league on espn.com. The ID is the number after `leagueId=` in the address bar:

```text
https://fantasy.espn.com/basketball/league?leagueId=12345678
```

## 2. Copy the two cookies

Log in at espn.com in your browser, then open the cookie list:

- **Chrome:** View → Developer → Developer Tools (⌥⌘I) → **Application** → Storage → **Cookies** → `https://www.espn.com`
- **Safari:** turn on Settings → Advanced → "Show features for web developers", then Develop → Show Web Inspector → **Storage** → **Cookies** → `espn.com`
- **Firefox:** Tools → Browser Tools → Web Developer Tools (⌥⌘I) → **Storage** → **Cookies**

Copy the **Value** of each:

| Cookie | Looks like |
|---|---|
| `espn_s2` | A long string, around 300 characters, often with `%2B` or `%2F` in it. Copy it exactly as shown. |
| `SWID` | `{8A1B2C3D-…}`, including the curly braces |

## 3. Add the league

```bash
uv run fh league add espn 12345678
```

The first time, it also asks for your email, name and time zone to create your user. Paste each cookie when asked (nothing appears as you paste; that's the hidden prompt). It prints the league name and **your team**, which it finds from your SWID. Your second ESPN league reuses the saved cookies, so it doesn't ask again.

Run this in a real terminal (Terminal.app or the app's terminal panel), because hidden prompts need one.

## When ESPN stops accepting them

Logging out of ESPN in that browser usually invalidates `espn_s2`. If a run reports "ESPN refused the cookies", log in again, copy fresh values and replace them:

```bash
uv run fh league add espn 12345678 --new-cookies
```

## Sleeper

Sleeper needs no login: `uv run fh league add sleeper <league_id> --username <your Sleeper username>`. The league ID is in the league's URL, or under League → Settings in the app.
