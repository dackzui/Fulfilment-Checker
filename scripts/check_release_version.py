from app.metadata import app_metadata

meta = app_metadata()
print(f"App footer: {meta['name']} v{meta['version']}")
