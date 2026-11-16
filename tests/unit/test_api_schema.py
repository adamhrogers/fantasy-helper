"""web/openapi.json is the contract the phone app's types are generated from. If the API changes,
regenerate it: `uv run fh openapi > web/openapi.json`."""

import json
from pathlib import Path

from fantasy_helper.api.app import create_app
from fantasy_helper.config import Settings

ROOT = Path(__file__).parents[2]


def test_the_committed_schema_is_the_apis_current_schema() -> None:
    committed = json.loads((ROOT / "web" / "openapi.json").read_text())
    assert committed == json.loads(json.dumps(create_app(Settings()).openapi()))


def test_every_route_but_livez_requires_a_sign_in() -> None:
    schema = create_app(Settings()).openapi()
    for path, methods in schema["paths"].items():
        for method, spec in methods.items():
            secured = "security" in spec
            assert secured == (path != "/livez"), (method, path)
