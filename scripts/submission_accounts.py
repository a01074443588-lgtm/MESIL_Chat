"""Git에서 제외된 제출 시연계정 파일을 역할 기준으로 읽습니다.

계정 아이디와 비밀번호를 소스코드, 문서, 검증 결과에 하드코딩하지 않기 위한
공통 도우미입니다. 계정 파일은 ``data/SUBMISSION_JUDGE_CREDENTIALS.txt``에
두고 Git에서 제외합니다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CREDENTIALS_PATH = (
    PROJECT_ROOT / "data" / "SUBMISSION_JUDGE_CREDENTIALS.txt"
)


@dataclass(frozen=True)
class SubmissionAccounts:
    care_a: str
    care_b: str
    social: str

    @property
    def usernames(self) -> tuple[str, str, str]:
        return (self.care_a, self.care_b, self.social)


def load_submission_accounts(
    path: Path = DEFAULT_CREDENTIALS_PATH,
) -> SubmissionAccounts:
    text = path.read_text(encoding="utf-8")
    current_role: str | None = None
    values: dict[str, str] = {}

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("[") and line.endswith("]"):
            if "보고 작성자" in line:
                current_role = "care_a"
            elif "실시간 수신자" in line:
                current_role = "care_b"
            elif "업무함 검토자" in line:
                current_role = "social"
            else:
                current_role = None
            continue

        if current_role and line.startswith("아이디:"):
            username = line.partition(":")[2].strip()
            if not username:
                raise RuntimeError("시연계정 파일의 아이디가 비어 있습니다.")
            values[current_role] = username

    missing = {"care_a", "care_b", "social"} - values.keys()
    if missing:
        raise RuntimeError(
            "시연계정 파일에서 역할별 아이디 3개를 모두 찾지 못했습니다."
        )
    if len(set(values.values())) != 3:
        raise RuntimeError("시연계정 파일의 역할별 아이디가 서로 달라야 합니다.")

    return SubmissionAccounts(
        care_a=values["care_a"],
        care_b=values["care_b"],
        social=values["social"],
    )


def load_submission_password(path: Path = DEFAULT_CREDENTIALS_PATH) -> str:
    text = path.read_text(encoding="utf-8")
    passwords = set(re.findall(r"(?m)^비밀번호:\s*(\S+)$", text))
    if len(passwords) != 1:
        raise RuntimeError(
            "시연계정 파일에서 역할 3개가 공유하는 비밀번호 하나를 찾지 못했습니다."
        )
    return passwords.pop()
