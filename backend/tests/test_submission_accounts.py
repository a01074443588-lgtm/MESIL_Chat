from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from submission_accounts import (  # noqa: E402
    load_submission_accounts,
    load_submission_password,
)


def test_loads_ascii_finals_credentials_without_exposing_values(tmp_path: Path):
    credentials_path = tmp_path / "credentials.txt"
    credentials_path.write_text(
        "\n".join(
            [
                "[role:care_a]",
                "username: finals-care-a",
                "password: shared-secret",
                "",
                "[role:care_b]",
                "username: finals-care-b",
                "password: shared-secret",
                "",
                "[role:social]",
                "username: finals-social",
                "password: shared-secret",
            ]
        ),
        encoding="utf-8",
    )

    accounts = load_submission_accounts(credentials_path)

    assert accounts.usernames == (
        "finals-care-a",
        "finals-care-b",
        "finals-social",
    )
    assert load_submission_password(credentials_path) == "shared-secret"
