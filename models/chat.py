from pydantic import BaseModel, Field
from typing import List, Optional, Literal


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatSession(BaseModel):
    id: str
    messages: List[Message] = Field(default_factory=list)
    

class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None