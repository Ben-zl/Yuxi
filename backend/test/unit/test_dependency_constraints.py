from pathlib import Path
import tomllib


def test_transformers_security_constraint_is_at_least_510():
    pyproject = tomllib.loads(
        (Path(__file__).parents[2] / "pyproject.toml").read_text(encoding="utf-8")
    )
    constraints = pyproject["tool"]["uv"]["constraint-dependencies"]
    assert "transformers>=5.10.0" in constraints
