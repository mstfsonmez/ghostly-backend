from fastapi import FastAPI, APIRouter, HTTPException, Header, Cookie, Response
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Optional
import uuid
from datetime import datetime, timezone, timedelta
from bson import ObjectId
import socketio
from socketio_server import sio, socket_app
from models import *
from auth import create_access_token, verify_token

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# MongoDB connection (fallback to Atlas if env missing)
mongo_url = os.getenv('MONGO_URL', 'mongodb+srv://sc_db_user:VZgFrRZYD4LXGpUD@cluster0.k3hvo2s.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0')
# Check if using MongoDB Atlas (SSL required) or local MongoDB
if "mongodb+srv" in mongo_url or "mongodb.net" in mongo_url:
    # MongoDB Atlas connection
    import ssl
    client = AsyncIOMotorClient(
        mongo_url,
        tls=True,
        tlsAllowInvalidCertificates=True,
        serverSelectionTimeoutMS=5000,
        connectTimeoutMS=10000,
        socketTimeoutMS=10000
    )
else:
    # Local MongoDB connection (no SSL)
    client = AsyncIOMotorClient(mongo_url)

db = client[os.getenv('DB_NAME', 'sc_chat_db')]

# Create the main app without a prefix
app = FastAPI(title="Social Media API")

# Add CORS middleware to main app (applies to FastAPI routes, NOT to mounted sub-apps)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8081",
        "http://localhost:8082", 
        "http://localhost:8083",
        "http://127.0.0.1:8081",
        "http://127.0.0.1:8082",
        "http://127.0.0.1:8083",
        "*"  # Allow all origins for mobile app
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
    max_age=3600,
)

# Create a router with the /api prefix
api_router = APIRouter(prefix="/api")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Helper function to serialize MongoDB documents
def serialize_doc(doc):
    if doc and "_id" in doc:
        doc["_id"] = str(doc["_id"])
    # Convert datetime to ISO string for JSON serialization
    if doc and "created_at" in doc and isinstance(doc["created_at"], datetime):
        doc["created_at"] = doc["created_at"].isoformat()
    if doc and "expires_at" in doc and isinstance(doc["expires_at"], datetime):
        doc["expires_at"] = doc["expires_at"].isoformat()
    return doc

# Background task to delete room when it expires
async def schedule_room_deletion(room_id: str, expires_at: datetime):
    """Schedule room deletion after expiry time"""
    try:
        import asyncio
        
        # Calculate wait time
        now = datetime.now(timezone.utc)
        wait_seconds = (expires_at - now).total_seconds()
        
        if wait_seconds > 0:
            logger.info(f"Room {room_id} scheduled for deletion in {wait_seconds} seconds")
            await asyncio.sleep(wait_seconds)
        
        # Check if room still exists
        room = await db.chat_rooms.find_one({"_id": ObjectId(room_id)})
        if not room:
            logger.info(f"Room {room_id} already deleted")
            return
        
        # Delete room and all its messages
        await db.chat_rooms.delete_one({"_id": ObjectId(room_id)})
        await db.room_messages.delete_many({"room_id": room_id})
        
        logger.info(f"⏰ Room {room_id} expired and deleted with all messages")
        
        # Notify all users in the room that it expired
        await sio.emit('room_expired', {
            'room_id': room_id,
            'message': 'This room has expired and been deleted.'
        }, room=f"room_{room_id}")
        
        # Also broadcast to ALL clients so room list updates
        await sio.emit('room_list_updated', {
            'action': 'expired',
            'room_id': room_id
        })
        
    except Exception as e:
        logger.error(f"Error in schedule_room_deletion for room {room_id}: {e}")



@api_router.get("/users/{user_id}")
async def get_user(user_id: str):
    """Get user profile"""
    user = await db.users.find_one({"_id": ObjectId(user_id)})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return serialize_doc(user)

@api_router.put("/users/{user_id}")
async def update_user(user_id: str, name: Optional[str] = None, bio: Optional[str] = None, 
                     picture: Optional[str] = None, cover_photo: Optional[str] = None):
    """Update user profile"""
    update_data = {}
    if name: update_data["name"] = name
    if bio is not None: update_data["bio"] = bio
    if picture: update_data["picture"] = picture
    if cover_photo: update_data["cover_photo"] = cover_photo
    
    if not update_data:
        raise HTTPException(status_code=400, detail="No data to update")
    
    await db.users.update_one(
        {"_id": ObjectId(user_id)},
        {"$set": update_data}
    )
    
    user = await db.users.find_one({"_id": ObjectId(user_id)})
    return serialize_doc(user)

@api_router.get("/users")
async def search_users(q: str = ""):
    """Search users"""
    query = {}
    if q:
        query = {
            "$or": [
                {"username": {"$regex": q, "$options": "i"}},
                {"name": {"$regex": q, "$options": "i"}}
            ]
        }
    
    users = await db.users.find(query).limit(20).to_list(20)
    return [serialize_doc(u) for u in users]

# ============= FOLLOW ENDPOINTS =============

@api_router.post("/users/{user_id}/follow")
async def follow_user(user_id: str, follower_id: str):
    """Follow a user"""
    # Add to following list
    await db.users.update_one(
        {"_id": ObjectId(follower_id)},
        {"$addToSet": {"following": user_id}}
    )
    
    # Add to followers list
    await db.users.update_one(
        {"_id": ObjectId(user_id)},
        {"$addToSet": {"followers": follower_id}}
    )
    
    return {"success": True}

@api_router.post("/users/{user_id}/unfollow")
async def unfollow_user(user_id: str, follower_id: str):
    """Unfollow a user"""
    # Remove from following list
    await db.users.update_one(
        {"_id": ObjectId(follower_id)},
        {"$pull": {"following": user_id}}
    )
    
    # Remove from followers list
    await db.users.update_one(
        {"_id": ObjectId(user_id)},
        {"$pull": {"followers": follower_id}}
    )
    
    return {"success": True}

# ============= POST ENDPOINTS =============

@api_router.post("/posts")
async def create_post(content: str, user_id: str, images: List[str] = [], video: Optional[str] = None):
    """Create a new post"""
    user = await db.users.find_one({"_id": ObjectId(user_id)})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    post = {
        "user_id": user_id,
        "username": user["username"],
        "user_picture": user.get("picture"),
        "content": content,
        "images": images,
        "video": video,
        "likes": [],
        "comment_count": 0,
        "share_count": 0,
        "created_at": datetime.now(timezone.utc)
    }
    
    result = await db.posts.insert_one(post)
    post["_id"] = str(result.inserted_id)
    return serialize_doc(post)

@api_router.get("/posts")
async def get_feed(user_id: Optional[str] = None, limit: int = 20, skip: int = 0):
    """Get feed posts"""
    query = {}
    if user_id:
        # Get posts from user and following
        user = await db.users.find_one({"_id": ObjectId(user_id)})
        if user:
            following = user.get("following", [])
            following.append(user_id)
            query = {"user_id": {"$in": following}}
    
    posts = await db.posts.find(query).sort("created_at", -1).skip(skip).limit(limit).to_list(limit)
    return [serialize_doc(p) for p in posts]

@api_router.get("/posts/{post_id}")
async def get_post(post_id: str):
    """Get a single post"""
    post = await db.posts.find_one({"_id": ObjectId(post_id)})
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    return serialize_doc(post)

@api_router.post("/posts/{post_id}/like")
async def like_post(post_id: str, user_id: str):
    """Like a post"""
    await db.posts.update_one(
        {"_id": ObjectId(post_id)},
        {"$addToSet": {"likes": user_id}}
    )
    return {"success": True}

@api_router.post("/posts/{post_id}/unlike")
async def unlike_post(post_id: str, user_id: str):
    """Unlike a post"""
    await db.posts.update_one(
        {"_id": ObjectId(post_id)},
        {"$pull": {"likes": user_id}}
    )
    return {"success": True}

@api_router.delete("/posts/{post_id}")
async def delete_post(post_id: str, user_id: str):
    """Delete a post"""
    post = await db.posts.find_one({"_id": ObjectId(post_id)})
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    
    if post["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    await db.posts.delete_one({"_id": ObjectId(post_id)})
    await db.comments.delete_many({"post_id": post_id})
    
    return {"success": True}

# ============= COMMENT ENDPOINTS =============

@api_router.post("/posts/{post_id}/comments")
async def create_comment(post_id: str, content: str, user_id: str):
    """Create a comment"""
    user = await db.users.find_one({"_id": ObjectId(user_id)})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    comment = {
        "post_id": post_id,
        "user_id": user_id,
        "username": user["username"],
        "user_picture": user.get("picture"),
        "content": content,
        "created_at": datetime.now(timezone.utc)
    }
    
    result = await db.comments.insert_one(comment)
    
    # Increment comment count
    await db.posts.update_one(
        {"_id": ObjectId(post_id)},
        {"$inc": {"comment_count": 1}}
    )
    
    comment["_id"] = str(result.inserted_id)
    return serialize_doc(comment)

@api_router.get("/posts/{post_id}/comments")
async def get_comments(post_id: str):
    """Get post comments"""
    comments = await db.comments.find({"post_id": post_id}).sort("created_at", -1).to_list(100)
    return [serialize_doc(c) for c in comments]

# ============= STORY ENDPOINTS =============

@api_router.post("/stories")
async def create_story(user_id: str, media_type: str, media_url: str):
    """Create a story"""
    user = await db.users.find_one({"_id": ObjectId(user_id)})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    story = {
        "user_id": user_id,
        "username": user["username"],
        "user_picture": user.get("picture"),
        "media_type": media_type,
        "media_url": media_url,
        "views": [],
        "created_at": datetime.now(timezone.utc),
        "expires_at": datetime.now(timezone.utc) + timedelta(hours=24)
    }
    
    result = await db.stories.insert_one(story)
    story["_id"] = str(result.inserted_id)
    return serialize_doc(story)

@api_router.get("/stories")
async def get_stories(user_id: Optional[str] = None):
    """Get active stories"""
    query = {"expires_at": {"$gt": datetime.now(timezone.utc)}}
    
    if user_id:
        user = await db.users.find_one({"_id": ObjectId(user_id)})
        if user:
            following = user.get("following", [])
            following.append(user_id)
            query["user_id"] = {"$in": following}
    
    stories = await db.stories.find(query).sort("created_at", -1).to_list(100)
    return [serialize_doc(s) for s in stories]

@api_router.post("/stories/{story_id}/view")
async def view_story(story_id: str, user_id: str):
    """Mark story as viewed"""
    await db.stories.update_one(
        {"_id": ObjectId(story_id)},
        {"$addToSet": {"views": user_id}}
    )
    return {"success": True}

# ============= MESSAGE ENDPOINTS =============

@api_router.get("/messages/conversations")
async def get_conversations(user_id: str):
    """Get user's conversations"""
    # Get all messages where user is sender or recipient
    messages = await db.messages.find({
        "$or": [
            {"sender_id": user_id},
            {"recipient_id": user_id}
        ]
    }).sort("created_at", -1).to_list(1000)
    
    # Group by conversation partner
    conversations = {}
    for msg in messages:
        other_user = msg["recipient_id"] if msg["sender_id"] == user_id else msg["sender_id"]
        
        if other_user not in conversations:
            conversations[other_user] = {
                "user_id": other_user,
                "last_message": msg,
                "unread_count": 0
            }
        
        if msg["recipient_id"] == user_id and not msg.get("read", False):
            conversations[other_user]["unread_count"] += 1
    
    # Get user details
    result = []
    for conv in conversations.values():
        user = await db.users.find_one({"_id": ObjectId(conv["user_id"])})
        if user:
            conv["username"] = user["username"]
            conv["picture"] = user.get("picture")
            result.append(conv)
    
    return result

@api_router.get("/messages/{other_user_id}")
async def get_messages(other_user_id: str, user_id: str, limit: int = 50):
    """Get messages between two users"""
    messages = await db.messages.find({
        "$or": [
            {"sender_id": user_id, "recipient_id": other_user_id},
            {"sender_id": other_user_id, "recipient_id": user_id}
        ]
    }).sort("created_at", 1).limit(limit).to_list(limit)
    
    # Mark as read
    await db.messages.update_many(
        {"sender_id": other_user_id, "recipient_id": user_id, "read": False},
        {"$set": {"read": True}}
    )
    
    return [serialize_doc(m) for m in messages]

@api_router.post("/messages")
async def save_message(sender_id: str, recipient_id: str, message: str, 
                      image: Optional[str] = None, video: Optional[str] = None):
    """Save message to database"""
    msg = {
        "sender_id": sender_id,
        "recipient_id": recipient_id,
        "message": message,
        "image": image,
        "video": video,
        "read": False,
        "created_at": datetime.now(timezone.utc)
    }
    
    result = await db.messages.insert_one(msg)
    msg["_id"] = str(result.inserted_id)
    return serialize_doc(msg)

# ============= CHAT ROOM ENDPOINTS =============

class RoomCreate(BaseModel):
    name: str
    description: Optional[str] = None
    creator_id: str
    is_public: bool = True
    password: Optional[str] = None
    max_members: Optional[int] = None

@api_router.post("/rooms")
async def create_room(room_data: RoomCreate):
    """Create a chat room"""
    try:
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(hours=12)  # Room expires in 12 hours
        
        room = {
            "name": room_data.name,
            "description": room_data.description,
            "is_public": room_data.is_public,
            "creator_id": room_data.creator_id,
            "admin_id": room_data.creator_id,
            "members": [room_data.creator_id],
            "password": room_data.password,
            "max_members": room_data.max_members,
            "created_at": now,
            "expires_at": expires_at  # Store expiry time explicitly
        }
        
        result = await db.chat_rooms.insert_one(room)
        room["_id"] = str(result.inserted_id)
        
        # Schedule room deletion after 12 hours
        import asyncio
        asyncio.create_task(schedule_room_deletion(str(result.inserted_id), expires_at))
        
        # Emit room_created event to all connected clients
        try:
            await sio.emit('room_created', serialize_doc(room))
            logger.info(f"Room created and broadcasted: {room['name']}, expires at {expires_at}")
        except Exception as emit_error:
            logger.error(f"Error emitting room_created event: {emit_error}")
            # Continue even if emit fails
        
        return serialize_doc(room)
    except Exception as e:
        logger.error(f"Error creating room: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error creating room: {str(e)}")

@api_router.get("/users/{user_id}/channels")
async def get_user_channels(user_id: str):
    """Get user's active channels (rooms where they sent messages)"""
    try:
        # Find all rooms where user sent messages
        user_messages = await db.room_messages.find(
            {"user_id": user_id}
        ).to_list(length=1000)
        
        # Get unique room IDs
        room_ids = list(set([msg["room_id"] for msg in user_messages]))
        
        if not room_ids:
            return []
        
        # Get room details
        rooms = await db.chat_rooms.find(
            {"_id": {"$in": [ObjectId(rid) for rid in room_ids]}}
        ).to_list(length=100)
        
        channels = []
        for room in rooms:
            room_id = str(room["_id"])
            
            # Get last message in this room
            last_message = await db.room_messages.find_one(
                {"room_id": room_id},
                sort=[("created_at", -1)]
            )
            
            # Get unread count (messages after user's last message)
            user_last_msg = await db.room_messages.find_one(
                {"room_id": room_id, "user_id": user_id},
                sort=[("created_at", -1)]
            )
            
            unread_count = 0
            if user_last_msg and last_message:
                unread_count = await db.room_messages.count_documents({
                    "room_id": room_id,
                    "user_id": {"$ne": user_id},
                    "created_at": {"$gt": user_last_msg["created_at"]}
                })
            
            channel = {
                "_id": room_id,
                "name": room["name"],
                "description": room.get("description", ""),
                "members": room.get("members", []),
                "admin_id": room.get("admin_id", ""),
                "created_at": room["created_at"].isoformat() if isinstance(room["created_at"], datetime) else room["created_at"],
                "unread_count": unread_count
            }
            
            if last_message:
                channel["last_message"] = {
                    "message": last_message["message"],
                    "username": last_message["username"],
                    "created_at": last_message["created_at"].isoformat() if isinstance(last_message["created_at"], datetime) else last_message["created_at"]
                }
            
            channels.append(channel)
        
        return channels
        
    except Exception as e:
        logger.error(f"Error getting user channels: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error getting channels: {str(e)}")


@api_router.get("/rooms")
async def get_rooms(is_public: bool = True, search: Optional[str] = None):
    """Get public chat rooms with search"""
    query = {"is_public": is_public}
    
    if search:
        query["name"] = {"$regex": search, "$options": "i"}
    
    rooms = await db.chat_rooms.find(query).sort("created_at", -1).to_list(100)
    return [serialize_doc(r) for r in rooms]

@api_router.get("/rooms/{room_id}")
async def get_room(room_id: str):
    """Get room details"""
    room = await db.chat_rooms.find_one({"_id": ObjectId(room_id)})
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    
    # Check if room has expired
    if "expires_at" in room:
        expires_at = room["expires_at"]
        if isinstance(expires_at, str):
            expires_at = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
        elif isinstance(expires_at, datetime) and expires_at.tzinfo is None:
            # Make timezone-aware if needed
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        
        if expires_at <= datetime.now(timezone.utc):
            # Room expired, delete it
            await db.chat_rooms.delete_one({"_id": ObjectId(room_id)})
            await db.room_messages.delete_many({"room_id": room_id})
            raise HTTPException(status_code=410, detail="Room has expired")
    
    return serialize_doc(room)

class JoinRoomRequest(BaseModel):
    user_id: str
    password: Optional[str] = None

@api_router.post("/rooms/{room_id}/join")
async def join_room(room_id: str, request: JoinRoomRequest):
    """Join a room"""
    room = await db.chat_rooms.find_one({"_id": ObjectId(room_id)})
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    
    # Check if user is banned
    banned_users = room.get("banned_users", [])
    now = datetime.now(timezone.utc)
    
    for ban in banned_users:
        if ban["user_id"] == request.user_id:
            banned_until = ban["banned_until"]
            # Convert to datetime if it's a string
            if isinstance(banned_until, str):
                banned_until = datetime.fromisoformat(banned_until.replace('Z', '+00:00'))
            elif isinstance(banned_until, datetime) and banned_until.tzinfo is None:
                # Make offset-naive datetime offset-aware (assume UTC)
                banned_until = banned_until.replace(tzinfo=timezone.utc)
            
            if banned_until > now:
                # User is still banned
                time_remaining = int((banned_until - now).total_seconds())
                minutes = time_remaining // 60
                seconds = time_remaining % 60
                raise HTTPException(
                    status_code=403, 
                    detail=f"You are banned from this room for {minutes}m {seconds}s"
                )
            else:
                # Ban expired, remove it
                await db.chat_rooms.update_one(
                    {"_id": ObjectId(room_id)},
                    {"$pull": {"banned_users": {"user_id": request.user_id}}}
                )
                logger.info(f"Removed expired ban for user {request.user_id} from room {room_id}")
    
    # Check if user is already a member
    is_already_member = request.user_id in room.get("members", [])
    
    if not is_already_member:
        # Check password
        if room.get("password") and room.get("password") != request.password:
            raise HTTPException(status_code=403, detail="Invalid password")
        
        # Check max members
        if room.get("max_members") and len(room.get("members", [])) >= room["max_members"]:
            raise HTTPException(status_code=403, detail="Room is full")
        
        # Add user to members
        await db.chat_rooms.update_one(
            {"_id": ObjectId(room_id)},
            {"$addToSet": {"members": request.user_id}}
        )
        
        logger.info(f"User {request.user_id} joined room {room_id}")
    else:
        logger.info(f"User {request.user_id} already member of room {room_id}, skipping join")
    
    # Broadcast to ALL clients so room list member count updates
    await sio.emit('room_list_updated', {
        'action': 'member_joined',
        'room_id': room_id
    })
    
    logger.info(f"User {request.user_id} joined room {room_id}")
    
    return {"success": True}

class LeaveRoomRequest(BaseModel):
    user_id: str

@api_router.post("/rooms/{room_id}/leave")
async def leave_room(room_id: str, request: LeaveRoomRequest):
    """Leave a room - If admin leaves without transfer, delete room"""
    room = await db.chat_rooms.find_one({"_id": ObjectId(room_id)})
    
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    
    # Check if user is admin
    is_admin = room.get("admin_id") == request.user_id
    
    if is_admin:
        # Admin is leaving - delete room and all messages
        await db.chat_rooms.delete_one({"_id": ObjectId(room_id)})
        await db.room_messages.delete_many({"room_id": room_id})
        logger.info(f"Room {room_id} deleted - admin {request.user_id} left")
        
        # Notify users IN the room that it's deleted
        await sio.emit('room_deleted', {
            'room_id': room_id,
            'message': 'Room has been deleted because the owner left.'
        }, room=f"room_{room_id}")
        
        # ALSO broadcast to ALL clients so room list updates everywhere
        await sio.emit('room_list_updated', {
            'action': 'deleted',
            'room_id': room_id
        })
        
        return {"success": True, "room_deleted": True}
    else:
        # Regular member leaving - just remove from members list
        await db.chat_rooms.update_one(
            {"_id": ObjectId(room_id)},
            {"$pull": {"members": request.user_id}}
        )
        
        # Broadcast to ALL clients so room list member count updates
        await sio.emit('room_list_updated', {
            'action': 'member_left',
            'room_id': room_id
        })
        
        logger.info(f"User {request.user_id} left room {room_id}")
        
        return {"success": True, "room_deleted": False}

class DeleteRoomRequest(BaseModel):
    user_id: str

@api_router.delete("/rooms/{room_id}")
async def delete_room(room_id: str, request: DeleteRoomRequest):
    """Delete a room (admin only)"""
    room = await db.chat_rooms.find_one({"_id": ObjectId(room_id)})
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    
    if room["admin_id"] != request.user_id:
        raise HTTPException(status_code=403, detail="Only admin can delete room")
    
    # Delete room and its messages
    await db.chat_rooms.delete_one({"_id": ObjectId(room_id)})
    await db.room_messages.delete_many({"room_id": room_id})
    
    # Emit room_deleted event
    await sio.emit('room_deleted', {
        'room_id': room_id,
        'message': 'Room has been deleted by admin.'
    }, room=f"room_{room_id}")
    
    return {"success": True}

class TransferAdminRequest(BaseModel):
    room_id: str
    current_admin_id: str
    new_admin_id: str

class KickUserRequest(BaseModel):
    room_id: str
    admin_id: str
    user_id: str

@api_router.post("/rooms/{room_id}/kick")
async def kick_user(room_id: str, request: KickUserRequest):
    """Kick user from room (admin only)"""
    room = await db.chat_rooms.find_one({"_id": ObjectId(room_id)})
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    
    # Check if requester is admin
    if room["admin_id"] != request.admin_id:
        raise HTTPException(status_code=403, detail="Only admin can kick users")
    
    # Check if user is in room
    if request.user_id not in room.get("members", []):
        raise HTTPException(status_code=400, detail="User not in room")
    
    # Cannot kick yourself
    if request.user_id == request.admin_id:
        raise HTTPException(status_code=400, detail="Cannot kick yourself")
    
    # Delete all messages from this user in this room
    delete_result = await db.room_messages.delete_many({
        "room_id": room_id,
        "user_id": request.user_id
    })
    logger.info(f"Deleted {delete_result.deleted_count} messages from user {request.user_id} in room {room_id}")
    
    # Ban user for 5 minutes
    ban_until = datetime.now(timezone.utc) + timedelta(minutes=5)
    await db.chat_rooms.update_one(
        {"_id": ObjectId(room_id)},
        {
            "$pull": {"members": request.user_id},
            "$push": {
                "banned_users": {
                    "user_id": request.user_id,
                    "banned_until": ban_until
                }
            }
        }
    )
    logger.info(f"User {request.user_id} banned from room {room_id} until {ban_until}")
    
    # Emit kicked event to the user
    await sio.emit('user_kicked', {
        'room_id': room_id,
        'user_id': request.user_id,
        'room_name': room['name'],
        'banned_until': ban_until.isoformat()
    }, room=f"room_{room_id}")
    
    # Broadcast member list updated and messages cleared
    await sio.emit('room_list_updated', {
        'action': 'member_kicked',
        'room_id': room_id
    })
    
    # Emit messages_cleared event so other users refresh their chat
    await sio.emit('messages_cleared', {
        'room_id': room_id,
        'kicked_user_id': request.user_id
    }, room=f"room_{room_id}")
    
    logger.info(f"User {request.user_id} kicked from room {room_id} by {request.admin_id}")
    
    return {"success": True, "messages_deleted": delete_result.deleted_count, "banned_until": ban_until.isoformat()}

@api_router.post("/rooms/{room_id}/transfer-admin")
async def transfer_admin(room_id: str, request: TransferAdminRequest):
    """Transfer admin rights"""
    room = await db.chat_rooms.find_one({"_id": ObjectId(room_id)})
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    
    if room["admin_id"] != request.current_admin_id:
        raise HTTPException(status_code=403, detail="Only admin can transfer rights")
    
    if request.new_admin_id not in room.get("members", []):
        raise HTTPException(status_code=400, detail="New admin must be a member")
    
    await db.chat_rooms.update_one(
        {"_id": ObjectId(room_id)},
        {"$set": {"admin_id": request.new_admin_id}}
    )
    
    # Emit admin transfer event to room
    await sio.emit('admin_transferred', {
        'room_id': room_id,
        'new_admin_id': request.new_admin_id
    }, room=f"room_{room_id}")
    
    logger.info(f"Admin transferred in room {room_id}: {request.current_admin_id} -> {request.new_admin_id}")
    
    return {"success": True}

@api_router.get("/rooms/{room_id}/messages")
async def get_room_messages(room_id: str, limit: int = 50):
    """Get room messages"""
    messages = await db.room_messages.find({"room_id": room_id}).sort("created_at", -1).limit(limit).to_list(limit)
    messages.reverse()
    return [serialize_doc(m) for m in messages]

class SaveMessageRequest(BaseModel):
    user_id: str  # Actually nickname in anonymous system
    message: str

@api_router.post("/rooms/{room_id}/messages")
async def save_room_message(room_id: str, request: SaveMessageRequest):
    """Save room message (anonymous - user_id is nickname)"""
    msg = {
        "room_id": room_id,
        "user_id": request.user_id,  # This is the nickname
        "username": request.user_id,  # This is the nickname
        "message": request.message,
        "created_at": datetime.now(timezone.utc)
    }
    
    result = await db.room_messages.insert_one(msg)
    msg["_id"] = str(result.inserted_id)
    
    # Broadcast to ALL clients so rooms tab updates last message
    await sio.emit('room_list_updated', {
        'action': 'new_message',
        'room_id': room_id
    })
    
    return serialize_doc(msg)

# ============= MEDIA ENDPOINTS =============

@api_router.post("/media")
async def upload_media(user_id: str, title: str, media_type: str, media_url: str,
                      thumbnail: Optional[str] = None, description: Optional[str] = None,
                      privacy: str = "public"):
    """Upload media content"""
    media = {
        "user_id": user_id,
        "title": title,
        "description": description,
        "media_type": media_type,
        "media_url": media_url,
        "thumbnail": thumbnail,
        "likes": [],
        "views": 0,
        "privacy": privacy,
        "comment_count": 0,
        "created_at": datetime.now(timezone.utc)
    }
    
    result = await db.media_content.insert_one(media)
    media["_id"] = str(result.inserted_id)
    return serialize_doc(media)

@api_router.get("/media")
async def get_media(user_id: Optional[str] = None, media_type: Optional[str] = None, limit: int = 20):
    """Get media content"""
    query = {}
    if media_type:
        query["media_type"] = media_type
    
    # Filter based on privacy
    if user_id:
        user = await db.users.find_one({"_id": ObjectId(user_id)})
        if user:
            friends = user.get("following", [])
            # Show public + friends' content + own content
            query["$or"] = [
                {"privacy": "public"},
                {"privacy": "friends", "user_id": {"$in": friends}},
                {"user_id": user_id}
            ]
    else:
        query["privacy"] = "public"
    
    media = await db.media_content.find(query).sort("created_at", -1).limit(limit).to_list(limit)
    return [serialize_doc(m) for m in media]

@api_router.post("/media/{media_id}/like")
async def like_media(media_id: str, user_id: str):
    """Like media"""
    await db.media_content.update_one(
        {"_id": ObjectId(media_id)},
        {"$addToSet": {"likes": user_id}}
    )
    return {"success": True}

@api_router.post("/media/{media_id}/unlike")
async def unlike_media(media_id: str, user_id: str):
    """Unlike media"""
    await db.media_content.update_one(
        {"_id": ObjectId(media_id)},
        {"$pull": {"likes": user_id}}
    )
    return {"success": True}

@api_router.post("/media/{media_id}/comments")
async def create_media_comment(media_id: str, content: str, user_id: str):
    """Create a comment on media"""
    user = await db.users.find_one({"_id": ObjectId(user_id)})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    comment = {
        "media_id": media_id,
        "user_id": user_id,
        "username": user["username"],
        "user_picture": user.get("picture"),
        "content": content,
        "created_at": datetime.now(timezone.utc)
    }
    
    result = await db.media_comments.insert_one(comment)
    
    # Increment comment count
    await db.media_content.update_one(
        {"_id": ObjectId(media_id)},
        {"$inc": {"comment_count": 1}}
    )
    
    comment["_id"] = str(result.inserted_id)
    return serialize_doc(comment)

@api_router.get("/media/{media_id}/comments")
async def get_media_comments(media_id: str):
    """Get media comments"""
    comments = await db.media_comments.find({"media_id": media_id}).sort("created_at", -1).to_list(100)
    return [serialize_doc(c) for c in comments]

@api_router.get("/media/{media_id}")
async def get_media_detail(media_id: str):
    """Get media detail and increment views"""
    media = await db.media_content.find_one({"_id": ObjectId(media_id)})
    if not media:
        raise HTTPException(status_code=404, detail="Media not found")
    
    # Increment views
    await db.media_content.update_one(
        {"_id": ObjectId(media_id)},
        {"$inc": {"views": 1}}
    )
    
    return serialize_doc(media)

# ============= ROOT ENDPOINT =============

@api_router.get("/")
async def root():
    return {"message": "Social Media API v1.0"}

# Include the router in the main app
app.include_router(api_router)

# Mount Socket.IO at root level
app.mount("/socket.io", socket_app)

@app.on_event("startup")
async def startup_db_indexes():
    """Create TTL indexes for automatic data cleanup"""
    try:
        # TTL index for chat_rooms: 12 saatte otomatik silinir
        await db.chat_rooms.create_index("created_at", expireAfterSeconds=43200)  # 12 hours
        logger.info("✅ TTL index created for chat_rooms (12 hours)")
        
        # TTL index for room_messages: 24 saatte otomatik silinir  
        await db.room_messages.create_index("created_at", expireAfterSeconds=86400)  # 24 hours
        logger.info("✅ TTL index created for room_messages (24 hours)")
        
        # Index for faster room queries
        await db.chat_rooms.create_index([("is_public", 1), ("created_at", -1)])
        
        # Index for faster message queries
        await db.room_messages.create_index([("room_id", 1), ("created_at", -1)])
        
        logger.info("✅ All database indexes created successfully")
        
        # Schedule deletion for existing rooms
        import asyncio
        now = datetime.now(timezone.utc)
        existing_rooms = await db.chat_rooms.find({}).to_list(1000)
        
        for room in existing_rooms:
            room_id = str(room["_id"])
            
            # Add expires_at if missing (for old rooms)
            if "expires_at" not in room:
                created_at = room["created_at"]
                
                # Make timezone-aware if needed
                if isinstance(created_at, datetime) and created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=timezone.utc)
                
                expires_at = created_at + timedelta(hours=12)
                await db.chat_rooms.update_one(
                    {"_id": room["_id"]},
                    {"$set": {"expires_at": expires_at}}
                )
                logger.info(f"Added expires_at to room {room_id}")
            else:
                expires_at = room["expires_at"]
            
            # Convert to datetime if string
            if isinstance(expires_at, str):
                expires_at = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
            elif isinstance(expires_at, datetime) and expires_at.tzinfo is None:
                # Make timezone-aware if needed
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            
            # If already expired, delete immediately
            if expires_at <= now:
                await db.chat_rooms.delete_one({"_id": room["_id"]})
                await db.room_messages.delete_many({"room_id": room_id})
                logger.info(f"Deleted expired room {room_id}")
            else:
                # Schedule deletion
                asyncio.create_task(schedule_room_deletion(room_id, expires_at))
                logger.info(f"Scheduled room {room_id} for deletion at {expires_at}")
        
    except Exception as e:
        logger.error(f"❌ Error in startup: {e}")

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
