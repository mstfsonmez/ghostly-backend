from pydantic import BaseModel, Field, EmailStr
from typing import Optional, List
from datetime import datetime
from bson import ObjectId

class PyObjectId(str):
    @classmethod
    def __get_validators__(cls):
        yield cls.validate

    @classmethod
    def validate(cls, v):
        if not ObjectId.is_valid(v):
            raise ValueError("Invalid ObjectId")
        return str(v)

# User Models
class User(BaseModel):
    id: Optional[str] = Field(default=None, alias="_id")
    email: EmailStr
    name: str
    username: Optional[str] = None
    picture: Optional[str] = None
    bio: Optional[str] = None
    cover_photo: Optional[str] = None
    followers: List[str] = []
    following: List[str] = []
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str}

class Session(BaseModel):
    user_id: str
    session_token: str
    expires_at: datetime
    created_at: datetime = Field(default_factory=datetime.utcnow)

# Post Models
class Post(BaseModel):
    id: Optional[str] = Field(default=None, alias="_id")
    user_id: str
    username: str
    user_picture: Optional[str] = None
    content: str
    images: List[str] = []
    video: Optional[str] = None
    likes: List[str] = []
    comment_count: int = 0
    share_count: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str}

class Comment(BaseModel):
    id: Optional[str] = Field(default=None, alias="_id")
    post_id: str
    user_id: str
    username: str
    user_picture: Optional[str] = None
    content: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str}

# Story Models
class Story(BaseModel):
    id: Optional[str] = Field(default=None, alias="_id")
    user_id: str
    username: str
    user_picture: Optional[str] = None
    media_type: str  # 'image' or 'video'
    media_url: str
    views: List[str] = []
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime
    
    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str}

# Message Models
class Message(BaseModel):
    id: Optional[str] = Field(default=None, alias="_id")
    sender_id: str
    recipient_id: str
    message: str
    image: Optional[str] = None
    video: Optional[str] = None
    read: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str}

class GroupChat(BaseModel):
    id: Optional[str] = Field(default=None, alias="_id")
    name: str
    members: List[str]
    admin_id: str
    picture: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str}

class GroupMessage(BaseModel):
    id: Optional[str] = Field(default=None, alias="_id")
    group_id: str
    sender_id: str
    sender_name: str
    message: str
    image: Optional[str] = None
    video: Optional[str] = None
    read_by: List[str] = []
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str}

# Chat Room Models (mIRC style)
class ChatRoom(BaseModel):
    id: Optional[str] = Field(default=None, alias="_id")
    name: str
    description: Optional[str] = None
    is_public: bool = True
    creator_id: str
    admin_id: str  # Current admin (can be transferred)
    members: List[str] = []
    password: Optional[str] = None  # Room password
    max_members: Optional[int] = None  # Maximum members (None = unlimited)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str}

class RoomMessage(BaseModel):
    id: Optional[str] = Field(default=None, alias="_id")
    room_id: str
    user_id: str
    username: str
    message: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str}

# Media Models (Spotify/YouTube style)
class MediaContent(BaseModel):
    id: Optional[str] = Field(default=None, alias="_id")
    user_id: str
    title: str
    description: Optional[str] = None
    media_type: str  # 'audio' or 'video'
    media_url: str
    thumbnail: Optional[str] = None
    duration: Optional[int] = None
    likes: List[str] = []
    views: int = 0
    privacy: str = "public"  # 'public' or 'friends'
    comment_count: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str}

class MediaComment(BaseModel):
    id: Optional[str] = Field(default=None, alias="_id")
    media_id: str
    user_id: str
    username: str
    user_picture: Optional[str] = None
    content: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str}

class Playlist(BaseModel):
    id: Optional[str] = Field(default=None, alias="_id")
    user_id: str
    name: str
    description: Optional[str] = None
    media_ids: List[str] = []
    is_public: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str}

# Notification Model
class Notification(BaseModel):
    id: Optional[str] = Field(default=None, alias="_id")
    user_id: str
    type: str  # 'like', 'comment', 'follow', 'message', etc.
    from_user_id: str
    from_username: str
    content: str
    link: Optional[str] = None
    read: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str}
