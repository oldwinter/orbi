"""GitHub New Issue chooser configuration contract for Issue #918."""
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / ".github" / "ISSUE_TEMPLATE" / "config.yml"
USER_OUTCOME = ROOT / ".github" / "ISSUE_TEMPLATE" / "user-outcome.md"


def test_issue_chooser_guides_questions_and_documentation():
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))

    assert config["blank_issues_enabled"] is True
    assert config["contact_links"] == [
        {
            "name": "装不上 / 跑不通？",
            "url": "https://github.com/orbi-build/orbi/discussions/new?category=q-a",
            "about": "环境、模型接入、工作流的问题在这里问；你踩的坑会变成优先修的 Issue",
        },
        {
            "name": "先看文档",
            "url": "https://docs.orbi.build/",
            "about": "安装与配置的完整说明，提问前请先查",
        },
    ]


def test_internal_user_outcome_template_remains_available():
    assert USER_OUTCOME.is_file()
