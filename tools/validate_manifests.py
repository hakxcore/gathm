#!/usr/bin/env python3
"""
Gathm Enterprise — Tool Manifest Validator
Validates every tools/*/tool.yaml against a strict Pydantic schema.

Usage:
    python3 tools/validate_manifests.py           # validate all manifests
    python3 tools/validate_manifests.py dns geo   # validate specific tools

Exit code 0 = all valid, 1 = one or more errors.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Literal, Optional

try:
    import yaml
except ImportError:
    print("ERROR: pyyaml is required. Run: pip install pyyaml", file=sys.stderr)
    sys.exit(1)

try:
    from pydantic import BaseModel, Field, field_validator, model_validator
    from pydantic import ValidationError
    PYDANTIC_V2 = True
except ImportError:
    print("ERROR: pydantic>=2.0 is required. Run: pip install pydantic", file=sys.stderr)
    sys.exit(1)


TOOLS_DIR = Path(__file__).resolve().parent
GATHM_ROOT = TOOLS_DIR.parent

VALID_CATEGORIES = {
    "security", "networking", "data", "media",
    "utility", "finance", "science", "productivity",
}

VALID_TYPES = {"string", "integer", "number", "boolean", "array", "object"}


# ---------------------------------------------------------------------------
# Schema models
# ---------------------------------------------------------------------------

class Argument(BaseModel):
    name: str
    type: str = "string"
    required: bool = False
    description: str = ""

    @field_validator("type")
    @classmethod
    def valid_type(cls, v: str) -> str:
        if v not in VALID_TYPES:
            raise ValueError(f"type must be one of {sorted(VALID_TYPES)}, got '{v}'")
        return v


class Flag(BaseModel):
    flag: str
    description: str = ""
    has_argument: bool = False


class InputSchema(BaseModel):
    arguments: list[Argument] = []
    flags: list[Flag] = []


class JsonField(BaseModel):
    name: str
    type: str = "string"

    @field_validator("type")
    @classmethod
    def valid_type(cls, v: str) -> str:
        if v not in VALID_TYPES:
            raise ValueError(f"type must be one of {sorted(VALID_TYPES)}, got '{v}'")
        return v


class OutputSchema(BaseModel):
    text: str = ""
    json_fields: list[JsonField] = []


class ApiDep(BaseModel):
    name: str
    base_url: str
    health_endpoint: str = ""
    auth_required: bool = False


class Dependencies(BaseModel):
    system: list[str] = []
    python: list[str] = []


class ToolManifest(BaseModel):
    name: str = Field(..., min_length=1)
    version: str = Field(..., pattern=r"^\d+\.\d+\.\d+$")
    description: str = Field(..., min_length=5)
    category: str | list[str]

    dependencies: Dependencies = Field(default_factory=Dependencies)
    apis: list[ApiDep] = []
    # Whether the tool needs an internet connection for its core function.
    # Consumed by Pilot to decide which tools are usable while offline.
    # Defaults to True so a new tool is treated as online-only until proven
    # otherwise — safer than silently offering a tool that will fail offline.
    requires_internet: bool = True
    input_schema: Optional[InputSchema] = None
    output_schema: Optional[OutputSchema] = None
    fallback_tool: Optional[str] = None
    tags: list[str] = []

    @field_validator("category")
    @classmethod
    def valid_category(cls, v: str | list) -> str | list:
        cats = [v] if isinstance(v, str) else v
        for cat in cats:
            if cat not in VALID_CATEGORIES:
                raise ValueError(
                    f"category '{cat}' is not valid. "
                    f"Must be one of: {sorted(VALID_CATEGORIES)}"
                )
        return v

    @model_validator(mode="after")
    def no_self_fallback(self) -> "ToolManifest":
        if self.fallback_tool and self.fallback_tool == self.name:
            raise ValueError(f"fallback_tool cannot point to itself ('{self.name}')")
        return self


# ---------------------------------------------------------------------------
# Validator runner
# ---------------------------------------------------------------------------

def validate_manifest(path: Path) -> list[str]:
    """Return a list of error strings (empty = valid)."""
    errors: list[str] = []
    try:
        with open(path) as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            return [f"YAML root must be a mapping, got {type(data).__name__}"]
        ToolManifest.model_validate(data)
    except ValidationError as exc:
        for e in exc.errors():
            loc = " → ".join(str(x) for x in e["loc"])
            errors.append(f"{loc}: {e['msg']}")
    except yaml.YAMLError as exc:
        errors.append(f"YAML parse error: {exc}")
    except Exception as exc:
        errors.append(f"Unexpected error: {exc}")
    return errors


def run(tool_names: list[str] | None = None) -> int:
    """Validate manifests. Returns exit code."""
    if tool_names:
        paths = [TOOLS_DIR / name / "tool.yaml" for name in tool_names]
    else:
        paths = sorted(TOOLS_DIR.glob("*/tool.yaml"))

    total = len(paths)
    ok = 0
    failed = 0

    for path in paths:
        rel = path.relative_to(GATHM_ROOT)
        if not path.exists():
            print(f"MISSING  {rel}")
            failed += 1
            continue
        errors = validate_manifest(path)
        if errors:
            print(f"FAIL     {rel}")
            for e in errors:
                print(f"           {e}")
            failed += 1
        else:
            print(f"OK       {rel}")
            ok += 1

    print()
    print(f"Results: {ok}/{total} passed, {failed} failed")

    if failed > 0:
        return 1
    return 0


if __name__ == "__main__":
    names = sys.argv[1:] or None
    sys.exit(run(names))
