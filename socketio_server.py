import socketio
from typing import Dict, Set
import logging
from auth import verify_token
import asyncio
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create AsyncServer with ASGI async_mode
sio = socketio.AsyncServer(
    async_mode='asgi',
    # Disable CORS for Socket.IO (let FastAPI handle it)
    cors_allowed_origins=[],
    cors_credentials=False,
    logger=True,
    engineio_logger=True,  # Enable to see Engine.IO debug logs
    ping_timeout=60,
    ping_interval=25
)

# Create ASGI application
socket_app = socketio.ASGIApp(
    socketio_server=sio,
    socketio_path='socket.io'
)

# In-memory stores (use Redis in production)
connected_users: Dict[str, str] = {}  # {user_id: socket_id}
user_sessions: Dict[str, dict] = {}  # {socket_id: user_data}
online_users: Set[str] = set()

@sio.event
async def connect(sid, environ, auth):
    """Handle anonymous client connection"""
    logger.info(f"Client connecting: {sid}")
    
    # Anonymous system - no authentication required
    # Store basic session info
    user_sessions[sid] = {
        'sid': sid,
        'connected_at': None
    }
    
    logger.info(f"Anonymous client connected: {sid}")
    return True

@sio.event
async def set_nickname(sid, data):
    """Set nickname for anonymous user"""
    try:
        nickname = data.get('nickname')
        user_id = data.get('user_id')
        
        if nickname and user_id:
            # Store user session with nickname
            user_sessions[sid] = {
                'sid': sid,
                'user_id': user_id,
                'username': nickname
            }
        
        # Update connected users
        if user_id in connected_users:
            # User already connected, disconnect old session
            old_sid = connected_users[user_id]
            if old_sid in user_sessions:
                await sio.disconnect(old_sid)
        
        connected_users[user_id] = sid
        online_users.add(user_id)
        
        # Join personal room for direct messages
        await sio.enter_room(sid, f"user_{user_id}")
        
        logger.info(f"User {nickname} ({user_id}) connected with sid {sid}")
        
        # Notify others about online status
        await sio.emit('user_online', {
            'user_id': user_id,
            'username': nickname
        }, skip_sid=sid)
        
        # Send current online users to the newly connected user
        online_list = [uid for uid in online_users if uid != user_id]
        await sio.emit('online_users', {'users': online_list}, room=sid)
        
        return True
        
    except Exception as e:
        logger.error(f"Connection error for {sid}: {str(e)}")
        return False

@sio.event
async def disconnect(sid):
    """Handle client disconnection"""
    logger.info(f"Client disconnecting: {sid}")
    
    if sid in user_sessions:
        user_data = user_sessions[sid]
        user_id = user_data.get('user_id')
        username = user_data.get('username')
        
        if user_id:
            # Clean up
            connected_users.pop(user_id, None)
            online_users.discard(user_id)
        
        user_sessions.pop(sid, None)
        
        # Notify others if user_id exists
        if user_id and username:
            await sio.emit('user_offline', {
                'user_id': user_id,
                'username': username
            })
        
        logger.info(f"User disconnected and cleaned up: {username} ({user_id})")
    else:
        logger.info(f"Unknown session disconnected: {sid}")

@sio.event
async def send_message(sid, data):
    """Handle direct message between two users"""
    try:
        sender = user_sessions.get(sid)
        if not sender:
            await sio.emit('error', {'message': 'Not authenticated'}, room=sid)
            return
        
        recipient_id = data.get('recipient_id')
        message_text = data.get('message')
        image = data.get('image')
        video = data.get('video')
        
        if not recipient_id or not message_text:
            await sio.emit('error', {'message': 'Invalid message data'}, room=sid)
            return
        
        from datetime import datetime
        # Create message object
        message = {
            'message_id': f"{datetime.utcnow().timestamp()}_{sender['user_id']}",
            'sender_id': sender['user_id'],
            'sender_username': sender['username'],
            'recipient_id': recipient_id,
            'message': message_text,
            'image': image,
            'video': video,
            'timestamp': datetime.utcnow().isoformat(),
            'type': 'direct'
        }
        
        # Send to recipient if online
        recipient_sid = connected_users.get(recipient_id)
        if recipient_sid:
            await sio.emit('receive_message', message, room=recipient_sid)
            logger.info(f"Message sent from {sender['username']} to user {recipient_id}")
            status = 'delivered'
        else:
            logger.info(f"User {recipient_id} offline, message queued")
            status = 'queued'
        
        # Send confirmation to sender
        await sio.emit('message_sent', {
            'message_id': message['message_id'],
            'status': status
        }, room=sid)
        
    except Exception as e:
        logger.error(f"Error sending message: {str(e)}")
        await sio.emit('error', {'message': 'Failed to send message'}, room=sid)

@sio.event
async def join_room(sid, data):
    """Handle anonymous user joining a chat room"""
    try:
        room_id = data.get('room_id')
        nickname = data.get('nickname')
        user_id = data.get('user_id')
        
        if not room_id:
            return
        
        # Store or update user session
        if nickname and user_id:
            user_sessions[sid] = {
                'sid': sid,
                'user_id': user_id,
                'username': nickname
            }
        
        # Join the room
        await sio.enter_room(sid, f"room_{room_id}")
        
        from datetime import datetime
        # Notify room members
        user_info = user_sessions.get(sid, {})
        await sio.emit('user_joined_room', {
            'user_id': user_info.get('user_id', 'anonymous'),
            'username': user_info.get('username', 'Anonymous'),
            'room_id': room_id,
            'timestamp': datetime.utcnow().isoformat()
        }, room=f"room_{room_id}", skip_sid=sid)
        
        logger.info(f"User {user_info.get('username', 'Anonymous')} joined room {room_id}")
        
    except Exception as e:
        logger.error(f"Error joining room: {str(e)}")

@sio.event
async def leave_room(sid, data):
    """Handle user leaving a chat room"""
    try:
        user = user_sessions.get(sid)
        if not user:
            return
        
        room_id = data.get('room_id')
        if not room_id:
            return
        
        from datetime import datetime
        # Notify room members before leaving
        await sio.emit('user_left_room', {
            'user_id': user['user_id'],
            'username': user['username'],
            'room_id': room_id,
            'timestamp': datetime.utcnow().isoformat()
        }, room=f"room_{room_id}", skip_sid=sid)
        
        # Leave the room
        await sio.leave_room(sid, f"room_{room_id}")
        
        logger.info(f"User {user['username']} left room {room_id}")
        
    except Exception as e:
        logger.error(f"Error leaving room: {str(e)}")

@sio.event
async def send_room_message(sid, data):
    """Handle message sent to a room (anonymous group chat)"""
    try:
        room_id = data.get('room_id')
        message_text = data.get('message')
        nickname = data.get('nickname')
        user_id = data.get('user_id')
        
        if not room_id or not message_text:
            await sio.emit('error', {'message': 'Invalid message data'}, room=sid)
            return
        
        # Get or create user session
        sender = user_sessions.get(sid, {})
        if nickname and user_id:
            sender = {
                'user_id': user_id,
                'username': nickname
            }
            user_sessions[sid] = sender
        
        from datetime import datetime
        # Create message object
        message = {
            '_id': f"{datetime.utcnow().timestamp()}_{sender.get('user_id', 'anon')}",
            'room_id': room_id,
            'user_id': sender.get('user_id', 'anonymous'),
            'username': sender.get('username', 'Anonymous'),
            'message': message_text,
            'created_at': datetime.utcnow().isoformat(),
        }
        
        # Broadcast to ALL in room (including sender for instant feedback)
        await sio.emit('receive_room_message', message, room=f"room_{room_id}")
        
        logger.info(f"Room message sent by {sender.get('username', 'Anonymous')} to room {room_id}")
        
    except Exception as e:
        logger.error(f"Error sending room message: {str(e)}")
        await sio.emit('error', {'message': 'Failed to send message'}, room=sid)

@sio.event
async def typing_indicator(sid, data):
    """Handle typing indicator for direct messages"""
    try:
        user = user_sessions.get(sid)
        if not user:
            return
        
        recipient_id = data.get('recipient_id')
        is_typing = data.get('is_typing', False)
        
        if not recipient_id:
            return
        
        recipient_sid = connected_users.get(recipient_id)
        if recipient_sid:
            await sio.emit('user_typing', {
                'user_id': user['user_id'],
                'username': user['username'],
                'is_typing': is_typing
            }, room=recipient_sid)
            
    except Exception as e:
        logger.error(f"Error handling typing indicator: {str(e)}")

@sio.event
async def send_group_message(sid, data):
    """Handle group chat message"""
    try:
        sender = user_sessions.get(sid)
        if not sender:
            await sio.emit('error', {'message': 'Not authenticated'}, room=sid)
            return
        
        group_id = data.get('group_id')
        message_text = data.get('message')
        image = data.get('image')
        
        if not group_id or not message_text:
            await sio.emit('error', {'message': 'Invalid message data'}, room=sid)
            return
        
        from datetime import datetime
        # Create message object
        message = {
            'message_id': f"{datetime.utcnow().timestamp()}_{sender['user_id']}_{group_id}",
            'sender_id': sender['user_id'],
            'sender_username': sender['username'],
            'group_id': group_id,
            'message': message_text,
            'image': image,
            'timestamp': datetime.utcnow().isoformat(),
            'type': 'group'
        }
        
        # Broadcast to group
        await sio.emit('receive_group_message', message, 
                      room=f"group_{group_id}", skip_sid=sid)
        
        # Send confirmation to sender
        await sio.emit('message_sent', {
            'message_id': message['message_id'],
            'status': 'delivered'
        }, room=sid)
        
        logger.info(f"Group message sent by {sender['username']} to group {group_id}")
        
    except Exception as e:
        logger.error(f"Error sending group message: {str(e)}")
        await sio.emit('error', {'message': 'Failed to send message'}, room=sid)

# ============= WebRTC Video Call Signaling =============

@sio.event
async def call_user(sid, data):
    """Initiate a call to another user"""
    try:
        caller = user_sessions.get(sid)
        if not caller:
            return
        
        to_user_id = data.get('to')
        offer = data.get('offer')
        
        to_sid = connected_users.get(to_user_id)
        if to_sid:
            await sio.emit('incoming_call', {
                'from': caller['user_id'],
                'from_username': caller['username'],
                'offer': offer
            }, room=to_sid)
            
            logger.info(f"Call from {caller['username']} to user {to_user_id}")
        else:
            await sio.emit('call_failed', {'reason': 'User offline'}, room=sid)
            
    except Exception as e:
        logger.error(f"Error in call_user: {str(e)}")

@sio.event
async def answer_call(sid, data):
    """Answer an incoming call"""
    try:
        answerer = user_sessions.get(sid)
        if not answerer:
            return
        
        to_user_id = data.get('to')
        answer = data.get('answer')
        
        to_sid = connected_users.get(to_user_id)
        if to_sid:
            await sio.emit('call_answered', {
                'from': answerer['user_id'],
                'answer': answer
            }, room=to_sid)
            
            logger.info(f"Call answered by {answerer['username']}")
            
    except Exception as e:
        logger.error(f"Error in answer_call: {str(e)}")

@sio.event
async def ice_candidate(sid, data):
    """Exchange ICE candidates for WebRTC"""
    try:
        user = user_sessions.get(sid)
        if not user:
            return
        
        to_user_id = data.get('to')
        candidate = data.get('candidate')
        
        to_sid = connected_users.get(to_user_id)
        if to_sid:
            await sio.emit('ice_candidate', {
                'from': user['user_id'],
                'candidate': candidate
            }, room=to_sid)
            
    except Exception as e:
        logger.error(f"Error in ice_candidate: {str(e)}")

@sio.event
async def end_call(sid, data):
    """End an ongoing call"""
    try:
        user = user_sessions.get(sid)
        if not user:
            return
        
        to_user_id = data.get('to')
        
        to_sid = connected_users.get(to_user_id)
        if to_sid:
            await sio.emit('call_ended', {
                'from': user['user_id']
            }, room=to_sid)
            
            logger.info(f"Call ended by {user['username']}")
            
    except Exception as e:
        logger.error(f"Error in end_call: {str(e)}")

# ============= Room Time Monitoring =============

async def monitor_room_times():
    """Background task to check room expiration times every minute"""
    from motor.motor_asyncio import AsyncIOMotorClient
    import os
    from bson import ObjectId
    
    # Get DB connection (same as server.py)
    mongo_url = os.getenv('MONGO_URL', 'mongodb+srv://sc_db_user:VZgFrRZYD4LXGpUD@cluster0.k3hvo2s.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0')
    
    if "mongodb+srv" in mongo_url or "mongodb.net" in mongo_url:
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
        client = AsyncIOMotorClient(mongo_url)
    
    db = client[os.getenv('DB_NAME', 'sc_chat_db')]
    
    logger.info("⏰ Room time monitor started")
    
    while True:
        try:
            await asyncio.sleep(10)  # Check every 10 seconds
            
            now = datetime.now(timezone.utc)
            
            # Get all active rooms (only fetch necessary fields for performance)
            rooms = await db.chat_rooms.find(
                {},
                {"_id": 1, "expires_at": 1}  # Only get _id and expires_at
            ).to_list(1000)
            
            for room in rooms:
                room_id = str(room["_id"])
                
                # Get expires_at
                if "expires_at" not in room:
                    continue
                
                expires_at = room["expires_at"]
                if isinstance(expires_at, str):
                    from datetime import datetime as dt
                    expires_at = dt.fromisoformat(expires_at.replace('Z', '+00:00'))
                elif isinstance(expires_at, datetime) and expires_at.tzinfo is None:
                    # Make timezone-aware if needed
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
                
                # Calculate time remaining
                time_diff = (expires_at - now).total_seconds()
                
                if time_diff <= 0:
                    # Room expired! Delete it
                    await db.chat_rooms.delete_one({"_id": ObjectId(room_id)})
                    await db.room_messages.delete_many({"room_id": room_id})
                    
                    logger.info(f"⏰ Room {room_id} expired and deleted")
                    
                    # Notify all users in the room
                    await sio.emit('room_expired', {
                        'room_id': room_id,
                        'message': 'This room has expired and been deleted.'
                    }, room=f"room_{room_id}")
                    
                    # Broadcast to all clients
                    await sio.emit('room_list_updated', {
                        'action': 'expired',
                        'room_id': room_id
                    })
                else:
                    # Room still active, broadcast time remaining
                    hours = int(time_diff // 3600)
                    minutes = int((time_diff % 3600) // 60)
                    seconds = int(time_diff % 60)
                    
                    # Format time string
                    if hours > 0:
                        time_str = f"{hours}h {minutes}m"
                    elif minutes > 0:
                        time_str = f"{minutes}m {seconds}s"
                    else:
                        time_str = f"{seconds}s"
                    
                    # Broadcast to users in this room
                    await sio.emit('room_time_update', {
                        'room_id': room_id,
                        'time_remaining': time_str,
                        'seconds_remaining': int(time_diff)
                    }, room=f"room_{room_id}")
                    
        except Exception as e:
            logger.error(f"Error in monitor_room_times: {e}")

# Start background task
#asyncio.create_task(monitor_room_times())
