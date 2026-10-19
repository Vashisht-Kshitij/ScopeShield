"""Supported production launcher. No workers/reload/proxy-header overrides."""
from app.database import database_url
from app.deployment import Deployment


def main():
    config = Deployment.load()
    if not config.production:
        raise ValueError("This launcher requires SCOPESHIELD_ENV=production")
    config.validate_storage(database_url())
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, workers=1,
                proxy_headers=False, server_header=False, access_log=False)


if __name__ == "__main__":
    main()
