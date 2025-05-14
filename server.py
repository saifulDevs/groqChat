import uuid
import json
import asyncio
import logging
from typing import Dict, List, Optional
import uvicorn
from fastapi import (
    FastAPI, WebSocket, WebSocketDisconnect,
    HTTPException, Depends, Body
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import EmailStr, BaseModel

from models.chat import ChatSession, Message, ChatRequest
from models.user import User
from services.llm_service import LLMService
from config import HOST, PORT

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# JWT setup
SECRET_KEY = "your-secret-key"
ALGORITHM = "HS256"
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def create_access_token(data: dict):
    return jwt.encode(data, SECRET_KEY, algorithm=ALGORITHM)

def get_current_user(token: str = Depends(oauth2_scheme)) -> User:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email = payload.get("sub")
        if email is None or email not in registered_users:
            raise HTTPException(status_code=401, detail="Invalid token")
        return registered_users[email]
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

# In-memory stores
registered_users: Dict[str, User] = {}
websocket_users: Dict[str, WebSocket] = {}
chat_sessions: Dict[str, ChatSession] = {}

# LLM service
llm_service = LLMService()

# Connection Manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}

    async def connect(self, websocket: WebSocket) -> str:
        await websocket.accept()
        session_id = str(uuid.uuid4())
        self.active_connections[session_id] = websocket
        chat_sessions[session_id] = ChatSession(id=session_id)
        return session_id

    def disconnect(self, session_id: str):
        self.active_connections.pop(session_id, None)

manager = ConnectionManager()

# Register endpoint
@app.post("/register")
async def register(email: EmailStr = Body(...), password: str = Body(...)):
    if email in registered_users:
        raise HTTPException(status_code=400, detail="User already exists")
    registered_users[email] = User(email=email, password=hash_password(password))
    return {"message": "User registered"}

# Login endpoint
@app.post("/login")
async def login(email: EmailStr = Body(...), password: str = Body(...)):
    user = registered_users.get(email)
    if not user or not verify_password(password, user.password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_access_token({"sub": email})
    return {"token": token}

# Chat endpoint
@app.post("/chat")
async def chat_endpoint(request: ChatRequest, current_user: User = Depends(get_current_user)):
    session_id = request.session_id or str(uuid.uuid4())
    if session_id not in chat_sessions:
        chat_sessions[session_id] = ChatSession(id=session_id)

    chat = chat_sessions[session_id]
    chat.messages.append(Message(role="user", content=request.message))

    full_response = ""
    async for chunk in llm_service.generate_response_stream(chat.messages):
        full_response += chunk

    chat.messages.append(Message(role="assistant", content=full_response))
    current_user.history.append(chat.messages.copy())

    return {
        "session_id": session_id,
        "response": full_response,
        "messages": chat.messages
    }

# Get chat history
@app.get("/history")
async def get_history(current_user: User = Depends(get_current_user)):
    return {"history": current_user.history}

# WebSocket endpoint
@app.websocket("/ws/chat")
async def websocket_endpoint(websocket: WebSocket):
    try:
        session_id = await manager.connect(websocket)
        await websocket.send_json({"type": "session_id", "session_id": session_id})

        welcome = "Hello! I'm your AI assistant. How can I help you?"
        chat_sessions[session_id].messages.append(Message(role="assistant", content=welcome))
        await websocket.send_json({"type": "initial_message", "content": welcome})

        while True:
            data = await websocket.receive_text()
            msg_data = json.loads(data)
            user_message = msg_data.get("message", "")

            chat_sessions[session_id].messages.append(Message(role="user", content=user_message))
            await websocket.send_json({"type": "message_received", "status": "processing"})

            try:
                full_response = ""
                async for chunk in llm_service.generate_response_stream(chat_sessions[session_id].messages):
                    await websocket.send_json({"type": "stream", "content": chunk})
                    full_response += chunk

                chat_sessions[session_id].messages.append(Message(role="assistant", content=full_response))
                await websocket.send_json({"type": "stream_end", "session_id": session_id})
            except Exception as e:
                logger.error(f"LLM error: {e}", exc_info=True)
                await websocket.send_json({
                    "type": "error",
                    "message": "LLM processing error."
                })

    except WebSocketDisconnect:
        manager.disconnect(session_id)
    except Exception as e:
        logger.error(f"WebSocket error: {e}", exc_info=True)
        manager.disconnect(session_id)

@app.get("/health")
async def health_check():
    return {"status": "ok"}

if __name__ == "__main__":
    uvicorn.run("server:app", host=HOST, port=PORT, reload=True)
