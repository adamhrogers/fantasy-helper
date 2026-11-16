"""The operator scripts that touch the cloud or prod, with the cloud tools replaced by fakes: what
they send, what they refuse, and that a secret never lands in a command line (ADR 0025, 0027)."""

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
SCRIPTS = ROOT / "scripts"
KEY = "hc-api-key-very-secret"


def run(
    script: str, *args: str, stdin: str = "", env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 (our own scripts, fixed arguments)
        ["/bin/bash", str(SCRIPTS / script), *args],
        input=stdin, capture_output=True, text=True, timeout=30,
        env={"PATH": "/usr/bin:/bin", "HOME": str(ROOT), **(env or {})},
    )  # fmt: skip


@pytest.fixture
def fake_curl(tmp_path: Path) -> dict[str, str]:
    """A `curl` that records every call and answers like Healthchecks (or fails on demand)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl = bin_dir / "curl"
    curl.write_text(
        """#!/bin/sh
out=""; body=""; args="$*"
while [ $# -gt 0 ]; do
  case "$1" in
    -o) out="$2"; shift ;;
    --data) body="$2"; shift ;;
  esac
  shift
done
cat >> "$FAKE_DIR/config.txt"           # curl -K -: the header comes in on stdin
printf '%s\\n' "$args" >> "$FAKE_DIR/args.txt"
printf '%s\\n' "$body" >> "$FAKE_DIR/bodies.txt"
printf '{"next_ping": "2026-11-16T11:17:00+00:00"}' > "$out"
printf '%s' "${FAKE_CODE:-200}"
"""
    )
    curl.chmod(0o755)
    return {"PATH": f"{bin_dir}:/usr/bin:/bin", "FAKE_DIR": str(tmp_path)}


def test_healthchecks_schedules_are_set_in_eastern_time_from_the_job_table(
    fake_curl: dict[str, str], tmp_path: Path
) -> None:
    done = run("healthchecks-setup.sh", stdin=f"{KEY}\n", env=fake_curl)
    assert done.returncode == 0, done.stderr
    bodies = (tmp_path / "bodies.txt").read_text().splitlines()
    assert len(bodies) == 4
    # A chained job's cron is when the chain starts (06:17), and its grace runs to the deadline:
    # a ping before a cron time would make Healthchecks expect another one at it.
    expected = {
        "snapshot-morning": ("17 6 * * *", 1800),
        "snapshot-evening": ("17 17 * * *", 1800),
        "daily": ("17 6 * * *", 5280),
        "weekly": ("17 6 * * *", 6180),
    }
    for job, (cron, grace) in expected.items():
        body = next(b for b in bodies if f'"slug":"{job}"' in b)
        # The time zone is the point: the website's default is UTC (2026-11-14's false alarm).
        assert '"tz":"America/New_York"' in body
        assert f'"schedule":"{cron}"' in body
        assert f'"grace":{grace}' in body
        assert '"unique":["slug"]' in body  # updates the check the first ping created
        assert '"channels":"*"' in body  # and keeps it wired to the phone
    assert "next ping due 2026-11-16T11:17:00+00:00" in done.stdout


def test_the_healthchecks_key_never_appears_in_a_command_line(
    fake_curl: dict[str, str], tmp_path: Path
) -> None:
    done = run("healthchecks-setup.sh", stdin=f"{KEY}\n", env=fake_curl)
    assert done.returncode == 0
    assert KEY not in (tmp_path / "args.txt").read_text()  # not in `ps` either
    assert (tmp_path / "config.txt").read_text().count(f"X-Api-Key: {KEY}") == 4  # stdin only
    assert KEY not in done.stdout + done.stderr


def test_a_refused_schedule_fails_the_script(fake_curl: dict[str, str]) -> None:
    done = run("healthchecks-setup.sh", stdin=f"{KEY}\n", env={**fake_curl, "FAKE_CODE": "401"})
    assert done.returncode == 1
    assert "HTTP 401" in done.stderr
    assert KEY not in done.stderr


def test_no_key_no_calls(fake_curl: dict[str, str], tmp_path: Path) -> None:
    done = run("healthchecks-setup.sh", stdin="\n", env=fake_curl)
    assert done.returncode == 1
    assert not (tmp_path / "bodies.txt").exists()


@pytest.mark.parametrize("command", ["run", "ingest", "migrate", "snapshot", "keygen", ""])
def test_only_the_data_commands_run_against_prod_from_the_laptop(command: str) -> None:
    done = run("fh-prod.sh", *([command] if command else []))
    assert done.returncode == 2
    assert "usage: scripts/fh-prod.sh league|report|doctor" in done.stderr


def test_prod_secrets_are_read_into_one_process_and_never_written(tmp_path: Path) -> None:
    """With a fake gcloud and a fake uv: the command runs with the prod environment set, and
    nothing is left on disk."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "gcloud").write_text(
        '#!/bin/sh\n[ "$1 $2 $3" = "secrets versions access" ] || exit 9\nprintf "value-of-$6"\n'
    )
    (bin_dir / "uv").write_text(
        '#!/bin/sh\nprintf "ENV=%s DB=%s KEY=%s TOPIC=%s ARGS=%s\\n" "$FH_ENV" "$DATABASE_URL" '
        '"$FH_ENCRYPTION_KEY" "$FH_NTFY_TOPIC" "$*"\n'
    )
    for tool in ("gcloud", "uv"):
        (bin_dir / tool).chmod(0o755)
    before = {p.name for p in ROOT.iterdir()}
    done = run("fh-prod.sh", "report", "open", env={"PATH": f"{bin_dir}:/usr/bin:/bin"})
    assert done.returncode == 0, done.stderr
    assert (
        "ENV=prod DB=value-of-fh-database-url KEY=value-of-fh-encryption-key "
        "TOPIC=value-of-fh-ntfy-topic ARGS=run fh report open"
    ) in done.stdout
    assert "PRODUCTION database" in done.stderr
    assert {p.name for p in ROOT.iterdir()} == before  # no .env, no file of any kind


def test_the_secrets_script_refuses_the_wrong_database_url_and_key(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "gcloud").write_text("#!/bin/sh\nexit 0\n")
    (bin_dir / "gcloud").chmod(0o755)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin"}
    pooler = "postgresql://postgres.abc:pw@aws-0-us-east-1.pooler.supabase.com:6543/postgres"
    done = run("gcp-secrets.sh", "fh-database-url", stdin=pooler + "\n", env=env)
    assert done.returncode == 1
    assert "transaction pooler" in done.stderr
    assert "pw@" not in done.stdout + done.stderr  # the value is never echoed back
    done = run("gcp-secrets.sh", "fh-database-url", stdin="mysql://x\n", env=env)
    assert done.returncode == 1
    assert "isn't a postgresql:// URL" in done.stderr
    done = run("gcp-secrets.sh", "fh-encryption-key", stdin="too-short\n", env=env)
    assert done.returncode == 1
    assert "44 characters" in done.stderr
    session = "postgresql://postgres.abc:pw@aws-0-us-east-1.pooler.supabase.com:5432/postgres"
    ok = run("gcp-secrets.sh", "fh-database-url", stdin=session + "\n", env=env)
    assert ok.returncode == 0, ok.stderr
    assert "new version saved" in ok.stdout
    assert "pw@" not in ok.stdout + ok.stderr


def test_every_script_is_executable_and_parses_under_the_macs_bash() -> None:
    """macOS ships bash 3.2: no associative arrays, no mapfile (the scripts run on the laptop)."""
    for script in sorted(SCRIPTS.glob("*.sh")):
        assert os.access(script, os.X_OK), f"{script.name} isn't executable"
        parsed = subprocess.run(  # noqa: S603 (our own scripts)
            ["/bin/bash", "-n", str(script)], capture_output=True, text=True
        )
        assert parsed.returncode == 0, f"{script.name}: {parsed.stderr}"
        assert "declare -A" not in script.read_text(), script.name
        assert "mapfile" not in script.read_text(), script.name


@pytest.fixture
def fake_cloud(tmp_path: Path) -> dict[str, str]:
    """A `gcloud` that serves the owner's URL and records what's saved, and a `psql` that records
    its environment and input, and answers the way a correctly locked-down login would."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "gcloud").write_text(
        """#!/bin/sh
echo "$*" >> "$FAKE_DIR/gcloud.log"
case "$1 $2 $3" in
  "secrets versions access")
    printf 'postgresql://postgres.abcref:Own%%40er@aws-0-us-east-1.pooler.supabase.com'
    printf ':5432/postgres' ;;
  "secrets describe"*) exit 1 ;;
  "secrets versions list") printf '3\\n' ;;
  "secrets versions add") cat > "$FAKE_DIR/saved_url.txt" ;;
esac
"""
    )
    (bin_dir / "psql").write_text(
        """#!/bin/sh
echo "ARGS: $*" >> "$FAKE_DIR/psql.log"
echo "ENV: $PGHOST $PGPORT $PGUSER $PGPASSWORD $PGDATABASE" >> "$FAKE_DIR/psql.log"
case "$*" in
  *" -c "*)
    case "$*" in
      *zz_probe*|*platform_credentials*) [ -n "$FAKE_WEAK" ] && exit 0; exit 1 ;;
    esac
    exit 0 ;;
  *) cat > "$FAKE_DIR/setup_stdin.txt" ;;
esac
"""
    )
    for tool in ("gcloud", "psql"):
        (bin_dir / tool).chmod(0o755)
    return {"PATH": f"{bin_dir}:/usr/bin:/bin", "FAKE_DIR": str(tmp_path)}


def test_the_readonly_setup_makes_the_login_proves_it_and_saves_only_its_url(
    fake_cloud: dict[str, str], tmp_path: Path
) -> None:
    done = run("readonly-setup.sh", env=fake_cloud)
    assert done.returncode == 0, done.stderr
    assert "new version saved, older version(s) disabled" in done.stdout
    saved = (tmp_path / "saved_url.txt").read_text()
    prefix = "postgresql://fh_readonly.abcref:"
    suffix = "@aws-0-us-east-1.pooler.supabase.com:5432/postgres"
    assert saved.startswith(prefix)
    assert saved.endswith(suffix)
    password = saved.removeprefix(prefix).removesuffix(suffix)
    assert len(password) == 48
    assert "Own" not in saved  # the owner's password isn't reused
    stdin = (tmp_path / "setup_stdin.txt").read_text()
    assert stdin.startswith(f"\\set pw '{password}'\n")
    assert "CREATE ROLE fh_readonly" in stdin
    log = (tmp_path / "psql.log").read_text()
    assert password not in "\n".join(line for line in log.splitlines() if line.startswith("ARGS"))
    envs = [line for line in log.splitlines() if line.startswith("ENV")]
    assert envs[0].startswith(
        "ENV: aws-0-us-east-1.pooler.supabase.com 5432 postgres.abcref Own@er "
    )
    assert all(f"fh_readonly.abcref {password} " in env for env in envs[1:])  # the probes
    assert password not in done.stdout + done.stderr
    gcloud = (tmp_path / "gcloud.log").read_text()
    assert "secrets create fh-readonly-database-url" in gcloud
    assert "secrets versions disable 3 --secret fh-readonly-database-url" in gcloud


def test_the_readonly_setup_saves_nothing_if_the_login_can_do_too_much(
    fake_cloud: dict[str, str], tmp_path: Path
) -> None:
    done = run("readonly-setup.sh", env={**fake_cloud, "FAKE_WEAK": "1"})
    assert done.returncode == 1
    assert "can CREATE a table: not saved" in done.stderr
    assert not (tmp_path / "saved_url.txt").exists()


def test_the_sql_script_fetches_only_the_readonly_url_and_never_writes_it(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "gcloud").write_text(
        '#!/bin/sh\necho "$*" >> "$FAKE_DIR/gcloud.log"\nprintf "value-of-$6"\n'
    )
    (bin_dir / "uv").write_text(
        '#!/bin/sh\nprintf "RO=%s DB=%s KEY=%s ARGS=%s\\n" "$FH_READONLY_DATABASE_URL" '
        '"$DATABASE_URL" "$FH_ENCRYPTION_KEY" "$*"\n'
    )
    for tool in ("gcloud", "uv"):
        (bin_dir / tool).chmod(0o755)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "FAKE_DIR": str(tmp_path)}
    before = {p.name for p in ROOT.iterdir()}
    done = run("fh-sql.sh", "select 1", env=env)
    assert done.returncode == 0, done.stderr
    assert ("RO=value-of-fh-readonly-database-url DB= KEY= ARGS=run fh sql select 1") in done.stdout
    assert (tmp_path / "gcloud.log").read_text().count("--secret") == 1
    assert "fh-database-url" not in (tmp_path / "gcloud.log").read_text()
    assert {p.name for p in ROOT.iterdir()} == before
    assert run("fh-sql.sh", env=env).returncode == 2
