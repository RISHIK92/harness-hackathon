"""The suite runs as development: the default APP_ENV is production, which
refuses to import app.main with the placeholder secrets a checkout (or CI)
has. Set before any test imports app.config, whose settings are cached."""
import os

os.environ.setdefault("APP_ENV", "development")
