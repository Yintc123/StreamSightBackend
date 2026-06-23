from fastapi import FastAPI

def create_app() -> FastAPI:
    app: FastAPI = FastAPI(
        title = "fastapi-foundation-template",
        version = "1.0.0"
    )

    return app