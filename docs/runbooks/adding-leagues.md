# Adding your leagues

League IDs and logins live in the database, never in `.env` or a file (ADR 0007). You add each league once with `fh league add`. It checks the league with its platform before saving anything.

**About `fh`:** it's this project's own command-line tool (short for Fantasy Helper), defined in `pyproject.toml` and installed into the project's environment by `uv`. Always run it from the project folder as `uv run fh …`. `uv run fh --help` lists every command.

**Where to run these:** a real terminal (Terminal.app, or the app's terminal panel) in the project folder. Prompts, including the hidden cookie prompt, need one.

## Order

1. **An ESPN league first.** The very first `fh league add` also creates your user (it asks for your email, name and time zone), and the first ESPN league asks for your cookies.
2. **Your second ESPN league.** It reuses the saved cookies, so it asks nothing.
3. **Your Sleeper league.** It needs no login.
4. **Check:** `uv run fh league list` shows all of them, with your team in each.

## ESPN

### 1. Get the league ID

Open the league on espn.com. The ID is the number after `leagueId=` in the address bar:

```text
https://fantasy.espn.com/basketball/league?leagueId=12345678
```

### 2. Copy your two cookies

Log in at espn.com in your browser, then open the cookie list:

- **Chrome:** View → Developer → Developer Tools (⌥⌘I) → **Application** → Storage → **Cookies** → `https://www.espn.com`
- **Safari:** turn on Settings → Advanced → "Show features for web developers", then Develop → Show Web Inspector → **Storage** → **Cookies** → `espn.com`
- **Firefox:** Tools → Browser Tools → Web Developer Tools (⌥⌘I) → **Storage** → **Cookies**

Copy the **Value** of each:

| Cookie | Looks like |
|---|---|
| `espn_s2` | A long string, around 300 characters, often with `%2B` or `%2F` in it. Copy it exactly as shown. |
| `SWID` | `{8A1B2C3D-…}`, including the curly braces |

Never paste these into chat, a file or `.env`. They go only into the hidden prompt.

### 3. Add it

```bash
uv run fh league add espn 12345678
```

Paste each cookie when asked. Nothing appears as you paste; that's the hidden prompt. It loads the league to prove the cookies work, finds **your team** from your SWID, then stores the cookies encrypted. It prints the league name, the season, the number of teams and your team.

## Sleeper

Sleeper's league data is public, so there are **no cookies and no hidden prompt**. You need two things:

- **League ID:** a long number of about 18 or 19 digits. The easiest place to find it is the league's web address on sleeper.com, which looks like `https://sleeper.com/leagues/1234567890123456789/…`. In the app, it's under the league's settings (look for **League ID**).
- **Your username:** the Sleeper handle you log in with, shown on your profile. Not your team name. It's how `fh` finds which team in the league is yours.

```bash
uv run fh league add sleeper 1234567890123456789 --username yourhandle
```

It prints the league name, the season, the number of teams and your roster. Neither value is secret, so you can also just send them to Claude to run.

## When ESPN stops accepting the cookies

Logging out of ESPN in that browser usually invalidates `espn_s2`. If a run reports "ESPN refused the cookies", log in again, copy fresh values and replace them:

```bash
uv run fh league add espn 12345678 --new-cookies
```
