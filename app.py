"""Default entry point: authenticated workspace; isolated legacy preview for demos."""
import runpy
from pathlib import Path
import config

if config.IS_DEMO:
    runpy.run_path(str(Path(__file__).with_name("legacy_app.py")),run_name="__main__")
else:
    from workspace_app import main
    main()
