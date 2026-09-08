import sys
from pathlib import Path

# Add root path of the project to sys.path to resolve internal backend imports seamlessly
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.config import settings
from backend.routes import health, liveness, transcription
from backend.services.whisper_service import whisper_service

# Initialize FastAPI App
app = FastAPI(
    title=settings.PROJECT_NAME,
    description="Multimodal Biometric Anti-Spoofing system coordinating lip movements & voice patterns.",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# Set CORS origins for local web development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict this to ["http://localhost:3000"] in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register API Routers
app.include_router(health.router, prefix=settings.API_V1_STR)
app.include_router(liveness.router, prefix=settings.API_V1_STR)
app.include_router(transcription.router, prefix=settings.API_V1_STR)

@app.on_event("startup")
async def startup_event():
    # Ensure temp directory exists under backend/temp
    temp_dir = Path(__file__).resolve().parent / "temp"
    temp_dir.mkdir(exist_ok=True)
    # Pre-load Whisper model to avoid transcription request latency
    whisper_service.load_model()

@app.get("/")
async def root_redirect():
    """
    Root redirect returning welcoming payload pointing to API documentation.
    """
    return {
        "message": f"Welcome to the {settings.PROJECT_NAME} API.",
        "api_docs_path": "/docs",
        "health_check_path": f"{settings.API_V1_STR}/health"
    }

if __name__ == "__main__":
    # Runs backend local server when app.py is executed directly
    print(f"Starting server in debug mode at http://{settings.HOST}:{settings.PORT}")
    uvicorn.run("backend.app:app", host=settings.HOST, port=settings.PORT, reload=settings.DEBUG)
