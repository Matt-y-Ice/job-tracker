"""Skill catalog: verified experience collected from outside the resume (GitHub projects).

Stored in config/skill_catalog.json (private). Used as a second source of truth next to the
resume when ranking jobs and writing tailored resumes and cover letters.
"""
from ..config import load_json
from ..paths import CONFIG_DIR

CATALOG_FILE = CONFIG_DIR / "skill_catalog.json"


def load_catalog(path=CATALOG_FILE) -> dict:
    """The catalog with only the projects you left switched on. Empty if there is none."""
    data = load_json(path, {}) or {}
    projects = [p for p in data.get("projects", []) if p.get("include", True)]
    included = {p["id"] for p in projects}
    skills = [s for s in data.get("skills", [])
              if s.get("include", True) and (not s.get("projects") or set(s["projects"]) & included)]
    return {**data, "projects": projects, "skills": skills}


def catalog_skill_names(catalog: dict) -> list[str]:
    return [s["name"] for s in catalog.get("skills", [])]
