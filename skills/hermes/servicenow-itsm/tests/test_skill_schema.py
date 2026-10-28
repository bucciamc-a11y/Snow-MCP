"""skill.yaml tool schemas must be well-formed (regression for quoted-string property definitions)."""
import unittest
from pathlib import Path

try:
    import yaml  # PyYAML; the scripts themselves stay standard-library only
except ImportError:  # pragma: no cover
    yaml = None

SKILL = Path(__file__).resolve().parent.parent / "skill.yaml"


@unittest.skipIf(yaml is None, "PyYAML not installed")
class SkillSchema(unittest.TestCase):
    def setUp(self):
        self.tools = yaml.safe_load(SKILL.read_text(encoding="utf-8"))["tools"]

    def test_every_property_is_a_mapping(self):
        bad = [f"{t['id']}.{k}" for t in self.tools
               for k, v in t.get("parameters", {}).get("properties", {}).items() if not isinstance(v, dict)]
        self.assertEqual(bad, [])

    def test_required_names_exist_as_properties(self):
        for t in self.tools:
            params = t.get("parameters", {})
            missing = set(params.get("required", [])) - set(params.get("properties", {}))
            self.assertEqual(missing, set(), t["id"])


if __name__ == "__main__":
    unittest.main()
