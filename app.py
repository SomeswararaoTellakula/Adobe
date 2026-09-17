"""Render-compatible entrypoint for the nested Flask application."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


_APP_PATH = Path(__file__).parent / "brand-ai-readiness-audit" / "app.py"
_spec = spec_from_file_location("brand_ai_readiness_app", _APP_PATH)
_module = module_from_spec(_spec)
_spec.loader.exec_module(_module)
app = _module.app
