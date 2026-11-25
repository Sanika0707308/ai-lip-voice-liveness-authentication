import sys
from pathlib import Path

# Add root path of the project to sys.path to resolve internal backend imports seamlessly
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

import time
import hashlib
from pydantic import BaseModel
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from backend.config import settings
from backend.routes import health, liveness, transcription, monitoring, interview
from backend.services.whisper_service import whisper_service

# Initialize FastAPI App
app = FastAPI(
    title=settings.PROJECT_NAME,
    description="LIVESCAN 2.0: AI-Based Liveness Verification & Online Interview & Examination Monitoring System.",
    version="2.0.0",
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

# Register API Routers (Mount at both /api and root for comprehensive client compatibility)
app.include_router(health.router, prefix=settings.API_V1_STR)
app.include_router(liveness.router, prefix=settings.API_V1_STR)
app.include_router(transcription.router, prefix=settings.API_V1_STR)
app.include_router(monitoring.router, prefix=settings.API_V1_STR)
app.include_router(interview.router, prefix=settings.API_V1_STR)
app.include_router(interview.router)
app.include_router(liveness.router)
app.include_router(monitoring.router)
app.include_router(transcription.router)


class LoginPayload(BaseModel):
    email: str
    password: str

class RegisterPayload(BaseModel):
    name: str
    email: str
    password: str

_in_memory_auth_users = [
    {
        "id": "usr_demo",
        "name": "Alex Morgan",
        "email": "alex.morgan@example.com",
        "password_hash": hashlib.sha256("password123".encode()).hexdigest()
    }
]

@app.post("/login", tags=["Auth Operations"])
@app.post("/api/login", tags=["Auth Operations"])
async def handle_login(data: LoginPayload):
    email_clean = data.email.lower().strip()
    pwd_hash = hashlib.sha256(data.password.encode()).hexdigest()
    user = next((u for u in _in_memory_auth_users if u["email"] == email_clean), None)
    if not user:
        name_part = email_clean.split("@")[0].replace(".", " ").title()
        user = {
            "id": f"usr_{len(_in_memory_auth_users) + 1}",
            "name": name_part or "Candidate",
            "email": email_clean,
            "password_hash": pwd_hash
        }
        _in_memory_auth_users.append(user)
    elif user["password_hash"] != pwd_hash:
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    return {
        "access_token": f"token-{user['id']}-{int(time.time())}",
        "token_type": "bearer",
        "user": {
            "id": user["id"],
            "name": user["name"],
            "email": user["email"]
        }
    }

@app.post("/register", tags=["Auth Operations"])
@app.post("/api/register", tags=["Auth Operations"])
async def handle_register(data: RegisterPayload):
    email_clean = data.email.lower().strip()
    if any(u["email"] == email_clean for u in _in_memory_auth_users):
        raise HTTPException(status_code=400, detail="Email already registered.")
    user = {
        "id": f"usr_{len(_in_memory_auth_users) + 1}",
        "name": data.name.strip(),
        "email": email_clean,
        "password_hash": hashlib.sha256(data.password.encode()).hexdigest()
    }
    _in_memory_auth_users.append(user)
    return {
        "message": "Registration successful",
        "user_id": user["id"]
    }

@app.get("/me", tags=["Auth Operations"])
@app.get("/api/me", tags=["Auth Operations"])
async def handle_me():
    return _in_memory_auth_users[0]


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
