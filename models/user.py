from pydantic import BaseModel, EmailStr
from typing import List
from models.chat import Message

class User(BaseModel):
    email: EmailStr
    password: str  # hashed password
    history: List[List[Message]] = []  # List of previous chat sessions
