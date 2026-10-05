"""Every skill's frontmatter must be YAML a strict parser accepts.

A description with an unquoted ``": "`` in it is a YAML error. Claude Code then loads the skill with
every field dropped, so the description that decides when the skill is offered is lost, and
``claude plugin validate`` does not catch it. This check needs no YAML library: it accepts a
double-quoted value (which must be valid JSON, the subset of YAML we use) or a plain one free of
the sequences that end a plain scalar.
"""

import json
import re
import unittest
from pathlib import Path

SKILLS = Path(__file__).resolve().parents[1] / "skills"
MAX_DESCRIPTION = 500


def frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path}: no frontmatter"
    block = text.split("---\n", 2)[1]
    out = {}
    for line in block.splitlines():
        m = re.match(r"^([A-Za-z_-]+): (.*)$", line)  # exactly one space: "key:value" is not a mapping
        assert m, f"{path}: frontmatter line is not 'key: value': {line!r}"
        out[m.group(1)] = m.group(2)
    return out


def value(raw: str, where: str) -> str:
    if raw.startswith('"'):
        return json.loads(raw)  # raises on a broken double-quoted scalar
    assert ": " not in raw and " #" not in raw and "\t#" not in raw, (
        f"{where}: quote this value, it contains ': ' or ' #'"
    )
    assert raw and not raw.endswith(":") and raw[:1] not in "[]{}&*!|>'%@`,#-?:", f"{where}: quote this value"
    return raw


class SkillFrontmatter(unittest.TestCase):
    def test_every_skill_has_valid_frontmatter(self):
        files = sorted(SKILLS.glob("*/SKILL.md"))
        self.assertEqual(len(files), 2)  # a deleted or stray skill is caught
        for f in files:
            fm = frontmatter(f)
            self.assertEqual(set(fm), {"name", "description"}, f)
            self.assertEqual(value(fm["name"], str(f)), f.parent.name, f)
            desc = value(fm["description"], str(f))
            self.assertTrue(40 <= len(desc) <= MAX_DESCRIPTION, f"{f}: description is {len(desc)} characters")
            self.assertIn("Use when", desc, f"{f}: say when the skill applies")
            self.assertIn("Not for", desc, f"{f}: say when it does not")


if __name__ == "__main__":
    unittest.main()
