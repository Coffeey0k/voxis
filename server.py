import asyncio, hashlib, hmac, os, secrets, sqlite3, threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, HTTPException, Header, Request, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

ROOT=Path(__file__).resolve().parent
DB=Path(os.getenv("VOXIS_DB", str(ROOT/"voxis.sqlite3")))
UPLOADS=Path(os.getenv("VOXIS_UPLOAD_DIR", str(ROOT/"uploads")))
UPLOADS.mkdir(parents=True, exist_ok=True)
app=FastAPI(title="Voxis API", version="0.2.0")
_rate_lock=threading.Lock()
_rate_hits={}
_RATE_RULES=[
    ("/api/login", "POST", 12, 60),
    ("/api/register", "POST", 20, 3600),
    ("/api/attachments", "POST", 30, 60),
    ("/api/groups/join-by-invite", "POST", 20, 60),
]
@app.middleware("http")
async def rate_limit_sensitive_routes(request:Request, call_next):
    client=request.client.host if request.client else "unknown"
    rule=next((r for r in _RATE_RULES if request.url.path==r[0] and request.method==r[1]),None)
    if rule:
        path,method,limit,window=rule; key=(client,path); current=datetime.now(timezone.utc).timestamp()
        with _rate_lock:
            hits=[t for t in _rate_hits.get(key,[]) if current-t<window]
            if len(hits)>=limit:
                _rate_hits[key]=hits
                return JSONResponse(status_code=429,content={"detail":"Слишком много запросов. Попробуй позже."},headers={"Retry-After":str(max(1,int(window-(current-hits[0]))))})
            hits.append(current);_rate_hits[key]=hits
            if len(_rate_hits)>5000:
                for k,v in list(_rate_hits.items()):
                    fresh=[t for t in v if current-t<3600]
                    if fresh:_rate_hits[k]=fresh
                    else:_rate_hits.pop(k,None)
    return await call_next(request)
live: dict[str,set[WebSocket]]={}
live_lock=threading.Lock()
_firebase_app=None
_firebase_lock=threading.Lock()
def now(): return datetime.now(timezone.utc).isoformat(timespec="seconds")
def db():
    c=sqlite3.connect(DB, timeout=20); c.row_factory=sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON"); c.execute("PRAGMA journal_mode=WAL"); return c
def cols(c,t): return {r["name"] for r in c.execute(f"PRAGMA table_info({t})")}
def migrate():
    with db() as c:
        c.execute("CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
        done={r["version"] for r in c.execute("SELECT version FROM schema_migrations")}
        if 1 not in done:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS users(username TEXT PRIMARY KEY COLLATE NOCASE,password_hash TEXT NOT NULL,salt TEXT NOT NULL,created_at TEXT NOT NULL,display_name TEXT NOT NULL DEFAULT '',bio TEXT NOT NULL DEFAULT '',avatar_path TEXT);
            CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY,username TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,device_id TEXT,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT,sender TEXT NOT NULL REFERENCES users(username),recipient TEXT REFERENCES users(username),group_id INTEGER,body TEXT NOT NULL,created_at TEXT NOT NULL,attachment_id INTEGER,encrypted INTEGER NOT NULL DEFAULT 0);
            CREATE INDEX IF NOT EXISTS idx_messages_pair ON messages(sender,recipient,id);
            INSERT INTO schema_migrations VALUES(1,datetime('now'));
            """)
        if 2 not in done:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS groups_chat(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,owner TEXT NOT NULL REFERENCES users(username),created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS group_members(group_id INTEGER NOT NULL REFERENCES groups_chat(id) ON DELETE CASCADE,username TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,role TEXT NOT NULL DEFAULT 'member',joined_at TEXT NOT NULL,PRIMARY KEY(group_id,username));
            CREATE TABLE IF NOT EXISTS devices(id TEXT PRIMARY KEY,username TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,platform TEXT NOT NULL,push_token TEXT,created_at TEXT NOT NULL,last_seen TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS attachments(id INTEGER PRIMARY KEY AUTOINCREMENT,owner TEXT NOT NULL REFERENCES users(username),stored_name TEXT NOT NULL,original_name TEXT NOT NULL,content_type TEXT NOT NULL,size INTEGER NOT NULL,created_at TEXT NOT NULL);
            INSERT INTO schema_migrations VALUES(2,datetime('now'));
            """)
        # Additive upgrades for the original Voxis v0.1 database.
        for n,ddl in [("display_name","TEXT NOT NULL DEFAULT ''"),("bio","TEXT NOT NULL DEFAULT ''"),("avatar_path","TEXT")]:
            if n not in cols(c,"users"): c.execute(f"ALTER TABLE users ADD COLUMN {n} {ddl}")
        if "device_id" not in cols(c,"sessions"): c.execute("ALTER TABLE sessions ADD COLUMN device_id TEXT")
        for n,ddl in [("group_id","INTEGER REFERENCES groups_chat(id)"),("attachment_id","INTEGER REFERENCES attachments(id)"),("encrypted","INTEGER NOT NULL DEFAULT 0"),("reply_to_id","INTEGER REFERENCES messages(id) ON DELETE SET NULL")]:
            if n not in cols(c,"messages"): c.execute(f"ALTER TABLE messages ADD COLUMN {n} {ddl}")
        c.execute("CREATE INDEX IF NOT EXISTS idx_group_messages ON messages(group_id,id)")
        if 3 not in done: c.execute("INSERT INTO schema_migrations VALUES(3,?)",(now(),))
        if 5 not in done:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS group_invites(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER NOT NULL REFERENCES groups_chat(id) ON DELETE CASCADE,
                token_hash TEXT NOT NULL UNIQUE,
                created_by TEXT NOT NULL REFERENCES users(username),
                created_at TEXT NOT NULL,
                expires_at TEXT,
                max_uses INTEGER,
                use_count INTEGER NOT NULL DEFAULT 0,
                revoked_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_group_invites_group ON group_invites(group_id, revoked_at);
            INSERT INTO schema_migrations VALUES(5,datetime('now'));
            """)
        if 4 not in done:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS message_receipts(message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,username TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,delivered_at TEXT,read_at TEXT,PRIMARY KEY(message_id,username));
            CREATE TABLE IF NOT EXISTS message_reactions(message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,username TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,reaction TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(message_id,username,reaction));
            CREATE TABLE IF NOT EXISTS blocked_users(blocker TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,blocked TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,created_at TEXT NOT NULL,PRIMARY KEY(blocker,blocked),CHECK(blocker<>blocked));
            CREATE TABLE IF NOT EXISTS pinned_messages(username TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,created_at TEXT NOT NULL,PRIMARY KEY(username,message_id));
            CREATE INDEX IF NOT EXISTS idx_receipts_user ON message_receipts(username,read_at);
            """)
            c.execute("INSERT INTO schema_migrations VALUES(4,?)",(now(),))

        if 6 not in done:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS polls(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                creator TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
                recipient TEXT REFERENCES users(username) ON DELETE CASCADE,
                group_id INTEGER REFERENCES groups_chat(id) ON DELETE CASCADE,
                question TEXT NOT NULL,
                created_at TEXT NOT NULL,
                CHECK((recipient IS NOT NULL AND group_id IS NULL) OR (recipient IS NULL AND group_id IS NOT NULL))
            );
            CREATE TABLE IF NOT EXISTS poll_options(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                poll_id INTEGER NOT NULL REFERENCES polls(id) ON DELETE CASCADE,
                label TEXT NOT NULL,
                UNIQUE(poll_id,label)
            );
            CREATE TABLE IF NOT EXISTS poll_votes(
                poll_id INTEGER NOT NULL REFERENCES polls(id) ON DELETE CASCADE,
                option_id INTEGER NOT NULL REFERENCES poll_options(id) ON DELETE CASCADE,
                username TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
                created_at TEXT NOT NULL,
                PRIMARY KEY(poll_id,username)
            );
            INSERT INTO schema_migrations VALUES(6,datetime('now'));
            """)
@app.on_event("startup")
def startup(): migrate()

class Credentials(BaseModel):
    username:str=Field(min_length=3,max_length=24,pattern=r"^[A-Za-z0-9_.-]+$")
    password:str=Field(min_length=8,max_length=256)
    device_id:str=Field(default="desktop-default",min_length=3,max_length=100)
    platform:str=Field(default="desktop",max_length=30)
class Profile(BaseModel):
    display_name:str=Field(default="",max_length=60); bio:str=Field(default="",max_length=300)
class GroupIn(BaseModel):
    name:str=Field(min_length=1,max_length=80); members:list[str]=Field(default_factory=list,max_length=100)
class GroupRole(BaseModel):
    role:str=Field(min_length=1,max_length=20)
class GroupRename(BaseModel):
    name:str=Field(min_length=1,max_length=80)
class GroupInviteIn(BaseModel):
    expires_in_hours:Optional[int]=Field(default=168,ge=1,le=8760)
    max_uses:Optional[int]=Field(default=None,ge=1,le=10000)
class GroupJoinInvite(BaseModel):
    token:str=Field(min_length=20,max_length=100)
class PollIn(BaseModel):
    question:str=Field(min_length=1,max_length=240)
    options:list[str]=Field(min_length=2,max_length=10)
    recipient:Optional[str]=None
    group_id:Optional[int]=None
class PollVoteIn(BaseModel):
    option_id:int=Field(gt=0)
class MessageIn(BaseModel):
    recipient:Optional[str]=None; group_id:Optional[int]=None
    body:str=Field(default="",max_length=4000); attachment_id:Optional[int]=None; reply_to_id:Optional[int]=None; encrypted:bool=False
class MessageForward(BaseModel):
    recipient:Optional[str]=None; group_id:Optional[int]=None
class EditMessage(BaseModel):
    body:str=Field(min_length=1,max_length=4000)
class ReactionIn(BaseModel):
    reaction:str=Field(min_length=1,max_length=16)
class BlockIn(BaseModel):
    username:str=Field(min_length=3,max_length=24)
class DeviceIn(BaseModel):
    id:str=Field(min_length=3,max_length=100); platform:str=Field(min_length=1,max_length=30); push_token:Optional[str]=None

def passhash(p,salt=None):
    s=bytes.fromhex(salt) if salt else secrets.token_bytes(16)
    return hashlib.scrypt(p.encode(),salt=s,n=2**14,r=8,p=1).hex(),s.hex()
def tokhash(t): return hashlib.sha256(t.encode()).hexdigest()
def user_token(t):
    if not t:return None
    with db() as c:
        r=c.execute("SELECT username FROM sessions WHERE token_hash=?",(tokhash(t),)).fetchone()
    return r["username"] if r else None
def me_user(auth):
    if not auth or not auth.startswith("Bearer "): raise HTTPException(401,"Требуется авторизация")
    u=user_token(auth[7:].strip())
    if not u: raise HTTPException(401,"Сессия недействительна")
    return u
def session(username,device_id,platform):
    token=secrets.token_urlsafe(40)
    with db() as c:
        c.execute("INSERT INTO sessions VALUES(?,?,?,?)",(tokhash(token),username,device_id,now()))
        c.execute("""INSERT INTO devices VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET username=excluded.username,platform=excluded.platform,last_seen=excluded.last_seen""",(device_id,username,platform,None,now(),now()))
    return token
def profile(r):
    return {"username":r["username"],"display_name":r["display_name"] or r["username"],"bio":r["bio"],"avatar_url":f"/api/avatar/{r['username']}" if r["avatar_path"] else None}
def member(c,g,u):
    if not c.execute("SELECT 1 FROM group_members WHERE group_id=? AND username=?",(g,u)).fetchone(): raise HTTPException(403,"Нет доступа к группе")
async def notify(u,payload):
    with live_lock: sockets=list(live.get(u,set()))
    for ws in sockets:
        try: await ws.send_json(payload)
        except Exception:
            with live_lock: live.get(u,set()).discard(ws)

def push_to_user(username, event_type, data):
    """Best-effort FCM HTTP v1 push. Notification payload never includes message text."""
    global _firebase_app
    if not os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
        return {"sent": 0, "configured": False}
    with db() as c:
        tokens=[r["push_token"] for r in c.execute(
            "SELECT push_token FROM devices WHERE username=? AND push_token IS NOT NULL AND push_token<>''",
            (username,)).fetchall()]
    if not tokens:
        return {"sent": 0, "configured": True}
    try:
        import firebase_admin
        from firebase_admin import messaging
        with _firebase_lock:
            if _firebase_app is None:
                try:
                    _firebase_app=firebase_admin.get_app()
                except ValueError:
                    _firebase_app=firebase_admin.initialize_app()
        message=messaging.MulticastMessage(
            tokens=tokens[:500],
            notification=messaging.Notification(title="Voxis", body="Новое сообщение" if event_type=="message" else "Новое событие"),
            data={"type":str(event_type), **{str(k):str(v) for k,v in data.items()}},
            android=messaging.AndroidConfig(priority="high"),
            apns=messaging.APNSConfig(headers={"apns-priority":"10"}),
        )
        result=messaging.send_each_for_multicast(message, app=_firebase_app)
        invalid=[]
        for token, response in zip(tokens[:500], result.responses):
            code=getattr(response.exception, "code", None) if response.exception else None
            if not response.success and code in {"registration-token-not-registered", "invalid-argument"}:
                invalid.append(token)
        if invalid:
            with db() as c:
                c.executemany("UPDATE devices SET push_token=NULL WHERE push_token=?", [(t,) for t in invalid])
        return {"sent":result.success_count, "failed":result.failure_count, "configured":True}
    except Exception:
        # Provider outages must never break REST/WebSocket message delivery.
        return {"sent":0, "configured":True, "error":"provider_unavailable"}

async def notify_message_targets(targets, msg):
    unique=set(targets)
    for target in unique:
        await notify(target, {"type":"message", "message":msg})
    for target in unique:
        if target != msg["sender"]:
            await asyncio.to_thread(push_to_user, target, "message",
                                    {"message_id":msg["id"], "sender":msg["sender"]})

@app.get("/manifest.webmanifest", include_in_schema=False)
def pwa_manifest():
    return JSONResponse({
        "name":"Voxis — мессенджер","short_name":"Voxis","description":"Личные и групповые разговоры в Voxis.",
        "start_url":"/","scope":"/","display":"standalone","background_color":"#e9eef5","theme_color":"#527da3",
        "icons":[{"src":"/icon.svg","sizes":"any","type":"image/svg+xml","purpose":"any maskable"}]
    },headers={"Cache-Control":"public, max-age=3600"})

@app.get("/icon.svg", include_in_schema=False)
def pwa_icon():
    svg='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512"><rect width="512" height="512" rx="128" fill="#527da3"/><path d="M118 112h90l48 82 48-82h90L300 256l94 144h-90l-48-82-48 82h-90l94-144z" fill="#fff"/></svg>'
    return Response(svg,media_type="image/svg+xml",headers={"Cache-Control":"public, max-age=86400"})

@app.get("/sw.js", include_in_schema=False)
def pwa_service_worker():
    js = """const CACHE='voxis-shell-v1';const SHELL=['/','/manifest.webmanifest','/icon.svg'];
self.addEventListener('install',e=>e.waitUntil(caches.open(CACHE).then(c=>c.addAll(SHELL)).then(()=>self.skipWaiting())));
self.addEventListener('activate',e=>e.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(k=>k!==CACHE).map(k=>caches.delete(k)))).then(()=>self.clients.claim())));
self.addEventListener('fetch',e=>{const u=new URL(e.request.url);if(e.request.method!=='GET'||u.origin!==self.location.origin||u.pathname.startsWith('/api/'))return;if(e.request.mode==='navigate'){e.respondWith(fetch(e.request).then(r=>{const copy=r.clone();caches.open(CACHE).then(c=>c.put('/',copy));return r}).catch(()=>caches.match('/')));return;}if(SHELL.includes(u.pathname))e.respondWith(caches.match(e.request).then(cached=>cached||fetch(e.request)));});
"""
    return Response(js,media_type="application/javascript",headers={"Cache-Control":"no-cache","Service-Worker-Allowed":"/"})

@app.get("/", include_in_schema=False)
def web_home():
    index=ROOT/"web"/"index.html"
    if not index.is_file(): raise HTTPException(404,"Web client is not installed")
    return FileResponse(index, media_type="text/html; charset=utf-8")

@app.get("/health")
def health(): return {"ok":True,"app":"Voxis","version":"0.2.0"}
@app.get("/api/meta")
def meta():
    with db() as c: versions=[r["version"] for r in c.execute("SELECT version FROM schema_migrations ORDER BY version")]
    return {"api_version":"0.2.0","schema_versions":versions,"e2ee":"not_implemented","calls":"signaling_only"}
@app.post("/api/register")
def register(d:Credentials):
    if len(d.password) < 10: raise HTTPException(422,"Для нового аккаунта пароль должен быть не короче 10 символов")
    ph,s=passhash(d.password)
    try:
        with db() as c: c.execute("INSERT INTO users(username,password_hash,salt,created_at,display_name) VALUES(?,?,?,?,?)",(d.username,ph,s,now(),d.username))
    except sqlite3.IntegrityError: raise HTTPException(409,"Имя пользователя занято")
    return {"token":session(d.username,d.device_id,d.platform),"username":d.username}
@app.post("/api/login")
def login(d:Credentials):
    with db() as c:r=c.execute("SELECT * FROM users WHERE username=?",(d.username,)).fetchone()
    if not r: raise HTTPException(401,"Неверный логин или пароль")
    actual,_=passhash(d.password,r["salt"])
    if not hmac.compare_digest(actual,r["password_hash"]): raise HTTPException(401,"Неверный логин или пароль")
    return {"token":session(r["username"],d.device_id,d.platform),"username":r["username"]}
@app.post("/api/logout")
def logout(authorization:Optional[str]=Header(default=None)):
    if authorization and authorization.startswith("Bearer "):
        with db() as c:c.execute("DELETE FROM sessions WHERE token_hash=?",(tokhash(authorization[7:].strip()),))
    return {"ok":True}
@app.get("/api/me")
def get_me(authorization:Optional[str]=Header(default=None)):
    u=me_user(authorization)
    with db() as c:r=c.execute("SELECT * FROM users WHERE username=?",(u,)).fetchone()
    return profile(r)
@app.patch("/api/profile")
def update_profile(d:Profile,authorization:Optional[str]=Header(default=None)):
    u=me_user(authorization)
    with db() as c:
        c.execute("UPDATE users SET display_name=?,bio=? WHERE username=?",(d.display_name.strip(),d.bio.strip(),u))
        r=c.execute("SELECT * FROM users WHERE username=?",(u,)).fetchone()
    return profile(r)
@app.post("/api/avatar")
async def avatar_upload(file:UploadFile=File(...),authorization:Optional[str]=Header(default=None)):
    u=me_user(authorization)
    types={"image/png":".png","image/jpeg":".jpg","image/webp":".webp"}
    if file.content_type not in types: raise HTTPException(415,"Поддерживаются PNG, JPEG, WebP")
    b=await file.read(3*1024*1024+1)
    if len(b)>3*1024*1024: raise HTTPException(413,"Аватар максимум 3 МБ")
    name=secrets.token_hex(20)+types[file.content_type]; (UPLOADS/name).write_bytes(b)
    with db() as c:
        old=c.execute("SELECT avatar_path FROM users WHERE username=?",(u,)).fetchone()["avatar_path"]
        c.execute("UPDATE users SET avatar_path=? WHERE username=?",(name,u))
    if old:
        try:(UPLOADS/old).unlink(missing_ok=True)
        except OSError:pass
    return {"avatar_url":f"/api/avatar/{u}"}
@app.get("/api/avatar/{username}")
def avatar_get(username:str):
    with db() as c:r=c.execute("SELECT avatar_path FROM users WHERE username=?",(username,)).fetchone()
    if not r or not r["avatar_path"] or not (UPLOADS/r["avatar_path"]).exists(): raise HTTPException(404,"Аватар не найден")
    return FileResponse(UPLOADS/r["avatar_path"])
@app.get("/api/users")
def users(authorization:Optional[str]=Header(default=None)):
    u=me_user(authorization)
    with db() as c:rows=c.execute("SELECT * FROM users WHERE username!=? ORDER BY username COLLATE NOCASE",(u,)).fetchall()
    return {"users":[profile(r) for r in rows]}
@app.post("/api/devices")
def device_add(d:DeviceIn,authorization:Optional[str]=Header(default=None)):
    u=me_user(authorization)
    with db() as c:c.execute("""INSERT INTO devices VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET username=excluded.username,platform=excluded.platform,push_token=excluded.push_token,last_seen=excluded.last_seen""",(d.id,u,d.platform,d.push_token,now(),now()))
    return {"ok":True}
@app.get("/api/devices")
def device_list(authorization:Optional[str]=Header(default=None)):
    u=me_user(authorization)
    with db() as c:rows=c.execute("SELECT id,platform,created_at,last_seen FROM devices WHERE username=? ORDER BY last_seen DESC",(u,)).fetchall()
    return {"devices":[dict(r) for r in rows]}
@app.delete("/api/devices/{device_id}")
def device_delete(device_id:str,authorization:Optional[str]=Header(default=None)):
    u=me_user(authorization)
    with db() as c:
        c.execute("DELETE FROM sessions WHERE username=? AND device_id=?",(u,device_id));c.execute("DELETE FROM devices WHERE username=? AND id=?",(u,device_id))
    return {"ok":True}
@app.post("/api/groups")
async def group_create(d:GroupIn,authorization:Optional[str]=Header(default=None)):
    u=me_user(authorization); members=list(dict.fromkeys([u]+d.members))
    with db() as c:
        for m in members:
            if not c.execute("SELECT 1 FROM users WHERE username=?",(m,)).fetchone():raise HTTPException(404,f"Нет пользователя {m}")
        cur=c.execute("INSERT INTO groups_chat(name,owner,created_at) VALUES(?,?,?)",(d.name.strip(),u,now())); gid=cur.lastrowid
        for m in members:c.execute("INSERT INTO group_members VALUES(?,?,?,?)",(gid,m,"owner" if m==u else "member",now()))
    for m in members:await notify(m,{"type":"group_created","group_id":gid,"name":d.name.strip()})
    return {"id":gid,"name":d.name.strip(),"members":members}
@app.get("/api/groups")
def group_list(authorization:Optional[str]=Header(default=None)):
    u=me_user(authorization)
    with db() as c:rows=c.execute("""SELECT g.id,g.name,g.owner,g.created_at,(SELECT COUNT(*) FROM group_members x WHERE x.group_id=g.id) member_count,m.role FROM groups_chat g JOIN group_members m ON m.group_id=g.id WHERE m.username=? ORDER BY g.id DESC""",(u,)).fetchall()
    return {"groups":[dict(r) for r in rows]}
@app.post("/api/groups/{gid}/invites")
def group_invite_create(gid:int,d:GroupInviteIn,authorization:Optional[str]=Header(default=None)):
    u=me_user(authorization)
    raw=secrets.token_urlsafe(32)
    expires=datetime.now(timezone.utc).timestamp()+d.expires_in_hours*3600 if d.expires_in_hours else None
    expires_at=datetime.fromtimestamp(expires,timezone.utc).isoformat(timespec="seconds") if expires else None
    with db() as c:
        g=c.execute("SELECT owner FROM groups_chat WHERE id=?",(gid,)).fetchone()
        if not g:raise HTTPException(404,"Группа не найдена")
        if g["owner"]!=u:raise HTTPException(403,"Только владелец может создавать приглашения")
        cur=c.execute("INSERT INTO group_invites(group_id,token_hash,created_by,created_at,expires_at,max_uses) VALUES(?,?,?,?,?,?)",
                      (gid,tokhash(raw),u,now(),expires_at,d.max_uses))
        invite_id=cur.lastrowid
        name=c.execute("SELECT name FROM groups_chat WHERE id=?",(gid,)).fetchone()["name"]
    return {"id":invite_id,"group_id":gid,"group_name":name,"token":raw,"expires_at":expires_at,"max_uses":d.max_uses}

@app.get("/api/groups/{gid}/invites")
def group_invite_list(gid:int,authorization:Optional[str]=Header(default=None)):
    u=me_user(authorization)
    with db() as c:
        g=c.execute("SELECT owner FROM groups_chat WHERE id=?",(gid,)).fetchone()
        if not g:raise HTTPException(404,"Группа не найдена")
        if g["owner"]!=u:raise HTTPException(403,"Только владелец может просматривать приглашения")
        rows=c.execute("SELECT id,created_by,created_at,expires_at,max_uses,use_count,revoked_at FROM group_invites WHERE group_id=? ORDER BY id DESC",(gid,)).fetchall()
    return {"invites":[dict(r) for r in rows]}

@app.delete("/api/groups/{gid}/invites/{invite_id}")
def group_invite_revoke(gid:int,invite_id:int,authorization:Optional[str]=Header(default=None)):
    u=me_user(authorization)
    with db() as c:
        g=c.execute("SELECT owner FROM groups_chat WHERE id=?",(gid,)).fetchone()
        if not g:raise HTTPException(404,"Группа не найдена")
        if g["owner"]!=u:raise HTTPException(403,"Только владелец может отзывать приглашения")
        cur=c.execute("UPDATE group_invites SET revoked_at=? WHERE id=? AND group_id=? AND revoked_at IS NULL",(now(),invite_id,gid))
        if not cur.rowcount:raise HTTPException(404,"Активное приглашение не найдено")
    return {"ok":True}

@app.post("/api/groups/join-by-invite")
async def group_join_by_invite(d:GroupJoinInvite,authorization:Optional[str]=Header(default=None)):
    u=me_user(authorization); current=now()
    with db() as c:
        invite=c.execute("""SELECT i.*,g.name FROM group_invites i JOIN groups_chat g ON g.id=i.group_id
            WHERE i.token_hash=? AND i.revoked_at IS NULL AND (i.expires_at IS NULL OR i.expires_at>?)
            AND (i.max_uses IS NULL OR i.use_count<i.max_uses)""",(tokhash(d.token),current)).fetchone()
        if not invite:raise HTTPException(404,"Приглашение недействительно, истекло или отозвано")
        if c.execute("SELECT 1 FROM group_members WHERE group_id=? AND username=?",(invite["group_id"],u)).fetchone():
            return {"ok":True,"already_member":True,"group_id":invite["group_id"],"group_name":invite["name"]}
        c.execute("INSERT INTO group_members(group_id,username,role,joined_at) VALUES(?,?,?,?)",(invite["group_id"],u,"member",current))
        c.execute("UPDATE group_invites SET use_count=use_count+1 WHERE id=?",(invite["id"],))
        targets=[r["username"] for r in c.execute("SELECT username FROM group_members WHERE group_id=?",(invite["group_id"],)).fetchall()]
    for target in targets:
        await notify(target,{"type":"group_updated","group_id":invite["group_id"],"action":"joined","username":u})
    return {"ok":True,"already_member":False,"group_id":invite["group_id"],"group_name":invite["name"]}

@app.get("/api/groups/{gid}/members")
def group_members(gid:int,authorization:Optional[str]=Header(default=None)):
    u=me_user(authorization)
    with db() as c:
        if not c.execute("SELECT 1 FROM groups_chat WHERE id=?",(gid,)).fetchone():raise HTTPException(404,"Группа не найдена")
        member(c,gid,u); rows=c.execute("SELECT username,role,joined_at FROM group_members WHERE group_id=?",(gid,)).fetchall()
    return {"members":[dict(r) for r in rows]}
@app.post("/api/groups/{gid}/members")
async def group_add(gid:int,d:GroupIn,authorization:Optional[str]=Header(default=None)):
    u=me_user(authorization)
    with db() as c:
        g=c.execute("SELECT owner FROM groups_chat WHERE id=?",(gid,)).fetchone()
        if not g:raise HTTPException(404,"Группа не найдена")
        actor=c.execute("SELECT role FROM group_members WHERE group_id=? AND username=?",(gid,u)).fetchone()
        if not actor or actor["role"] not in ("owner","moderator"):raise HTTPException(403,"Добавлять участников могут только владелец и модераторы")
        for m in d.members:
            if not c.execute("SELECT 1 FROM users WHERE username=?",(m,)).fetchone():raise HTTPException(404,f"Нет пользователя {m}")
            c.execute("INSERT OR IGNORE INTO group_members VALUES(?,?,?,?)",(gid,m,"member",now()))
    for m in d.members:await notify(m,{"type":"group_updated","group_id":gid})
    return {"ok":True}

@app.patch("/api/groups/{gid}/members/{username}")
async def group_member_role(gid:int,username:str,d:GroupRole,authorization:Optional[str]=Header(default=None)):
    u=me_user(authorization)
    if d.role not in ("member","moderator"):raise HTTPException(422,"Роль должна быть member или moderator")
    with db() as c:
        g=c.execute("SELECT owner FROM groups_chat WHERE id=?",(gid,)).fetchone()
        if not g:raise HTTPException(404,"Группа не найдена")
        if g["owner"]!=u:raise HTTPException(403,"Только владелец может менять роли")
        target=c.execute("SELECT role FROM group_members WHERE group_id=? AND username=?",(gid,username)).fetchone()
        if not target:raise HTTPException(404,"Участник не найден")
        if username==g["owner"]:raise HTTPException(400,"Нельзя изменить роль владельца")
        c.execute("UPDATE group_members SET role=? WHERE group_id=? AND username=?",(d.role,gid,username))
        targets=[x["username"] for x in c.execute("SELECT username FROM group_members WHERE group_id=?",(gid,)).fetchall()]
    for target_user in targets:await notify(target_user,{"type":"group_updated","group_id":gid})
    return {"ok":True,"username":username,"role":d.role}

@app.delete("/api/groups/{gid}/members/{username}")
async def group_remove_member(gid:int,username:str,authorization:Optional[str]=Header(default=None)):
    u=me_user(authorization)
    with db() as c:
        g=c.execute("SELECT owner FROM groups_chat WHERE id=?",(gid,)).fetchone()
        if not g:raise HTTPException(404,"Группа не найдена")
        actor=c.execute("SELECT role FROM group_members WHERE group_id=? AND username=?",(gid,u)).fetchone()
        target=c.execute("SELECT role FROM group_members WHERE group_id=? AND username=?",(gid,username)).fetchone()
        if not actor or actor["role"] not in ("owner","moderator"):raise HTTPException(403,"Удалять участников могут только владелец и модераторы")
        if username==g["owner"]:raise HTTPException(400,"Владельца нельзя удалить из группы")
        if not target:raise HTTPException(404,"Участник не найден")
        if actor["role"]=="moderator" and target["role"]!="member":raise HTTPException(403,"Модератор может удалять только обычных участников")
        cur=c.execute("DELETE FROM group_members WHERE group_id=? AND username=?",(gid,username))
        if not cur.rowcount:raise HTTPException(404,"Участник не найден")
        targets=[x["username"] for x in c.execute("SELECT username FROM group_members WHERE group_id=?",(gid,)).fetchall()]
    await notify(username,{"type":"group_updated","group_id":gid,"action":"removed"})
    for target_user in targets:await notify(target_user,{"type":"group_updated","group_id":gid})
    return {"ok":True}

@app.patch("/api/groups/{gid}")
async def group_rename(gid:int,d:GroupRename,authorization:Optional[str]=Header(default=None)):
    u=me_user(authorization); name=d.name.strip()
    if not name:raise HTTPException(422,"Название не может быть пустым")
    with db() as c:
        g=c.execute("SELECT owner FROM groups_chat WHERE id=?",(gid,)).fetchone()
        if not g:raise HTTPException(404,"Группа не найдена")
        if g["owner"]!=u:raise HTTPException(403,"Только владелец может переименовать группу")
        c.execute("UPDATE groups_chat SET name=? WHERE id=?",(name,gid))
        targets=[x["username"] for x in c.execute("SELECT username FROM group_members WHERE group_id=?",(gid,)).fetchall()]
    for target in targets:await notify(target,{"type":"group_updated","group_id":gid,"name":name})
    return {"ok":True,"id":gid,"name":name}

@app.delete("/api/groups/{gid}")
async def group_leave_or_delete(gid:int,authorization:Optional[str]=Header(default=None)):
    u=me_user(authorization)
    with db() as c:
        g=c.execute("SELECT owner,name FROM groups_chat WHERE id=?",(gid,)).fetchone()
        if not g:raise HTTPException(404,"Группа не найдена")
        row=c.execute("SELECT role FROM group_members WHERE group_id=? AND username=?",(gid,u)).fetchone()
        if not row:raise HTTPException(403,"Ты не состоишь в этой группе")
        targets=[x["username"] for x in c.execute("SELECT username FROM group_members WHERE group_id=?",(gid,)).fetchall()]
        if g["owner"]==u:
            c.execute("DELETE FROM groups_chat WHERE id=?",(gid,))
            action="deleted"
        else:
            c.execute("DELETE FROM group_members WHERE group_id=? AND username=?",(gid,u))
            action="left"
    for target in targets:await notify(target,{"type":"group_updated","group_id":gid,"action":action})
    return {"ok":True,"action":action}

@app.get("/api/search/messages")
def search_messages(q:str,authorization:Optional[str]=Header(default=None),limit:int=100):
    u=me_user(authorization); query=q.strip()
    if not query: return {"messages":[]}
    limit=max(1,min(limit,200))
    with db() as c:
        rows=c.execute("""SELECT m.id,m.sender,m.recipient,m.group_id,m.body,m.created_at,m.attachment_id,a.original_name AS attachment_name
          FROM messages m LEFT JOIN attachments a ON a.id=m.attachment_id
          WHERE m.body LIKE ? AND ((m.group_id IS NULL AND (m.sender=? OR m.recipient=?))
          OR (m.group_id IS NOT NULL AND EXISTS(SELECT 1 FROM group_members gm WHERE gm.group_id=m.group_id AND gm.username=?)))
          ORDER BY m.id DESC LIMIT ?""",("%"+query.replace("%","\\%").replace("_","\\_")+"%",u,u,u,limit)).fetchall()
    return {"messages":[dict(r) for r in rows]}

@app.get("/api/messages/{peer}")
def direct_history(peer:str,authorization:Optional[str]=Header(default=None),limit:int=100,around_id:Optional[int]=None):
    u=me_user(authorization);limit=max(1,min(limit,200))
    with db() as c:
        if not c.execute("SELECT 1 FROM users WHERE username=?",(peer,)).fetchone():raise HTTPException(404,"Пользователь не найден")
        stamp=now()
        c.execute("INSERT OR IGNORE INTO message_receipts(message_id,username,delivered_at,read_at) SELECT id,?,?,NULL FROM messages WHERE sender=? AND recipient=? AND group_id IS NULL",(u,stamp,peer,u))
        c.execute("UPDATE message_receipts SET delivered_at=COALESCE(delivered_at,?) WHERE username=? AND message_id IN (SELECT id FROM messages WHERE sender=? AND recipient=?)",(stamp,u,peer,u))
        rows=c.execute("""SELECT m.id,m.sender,m.recipient,m.group_id,m.body,m.created_at,m.attachment_id,m.reply_to_id,m.encrypted,a.original_name AS attachment_name,a.size AS attachment_size,a.content_type AS attachment_content_type,rm.body AS reply_body,rm.sender AS reply_sender,(SELECT r.read_at FROM message_receipts r WHERE r.message_id=m.id AND r.username=CASE WHEN m.sender=? THEN m.recipient ELSE ? END) AS read_at,(SELECT r.delivered_at FROM message_receipts r WHERE r.message_id=m.id AND r.username=CASE WHEN m.sender=? THEN m.recipient ELSE ? END) AS delivered_at FROM messages m LEFT JOIN attachments a ON a.id=m.attachment_id LEFT JOIN messages rm ON rm.id=m.reply_to_id WHERE m.group_id IS NULL AND ((m.sender=? AND m.recipient=?) OR (m.sender=? AND m.recipient=?)) AND (? IS NULL OR m.id<=?) ORDER BY m.id DESC LIMIT ?""",(u,u,u,u,u,peer,peer,u,around_id,around_id,limit)).fetchall()
    return {"messages":[dict(r) for r in reversed(rows)]}
@app.get("/api/group-messages/{gid}")
def group_history(gid:int,authorization:Optional[str]=Header(default=None),limit:int=100,around_id:Optional[int]=None):
    u=me_user(authorization);limit=max(1,min(limit,200))
    with db() as c:
        member(c,gid,u);rows=c.execute("SELECT m.id,m.sender,m.recipient,m.group_id,m.body,m.created_at,m.attachment_id,m.reply_to_id,m.encrypted,a.original_name AS attachment_name,a.size AS attachment_size,a.content_type AS attachment_content_type,rm.body AS reply_body,rm.sender AS reply_sender FROM messages m LEFT JOIN attachments a ON a.id=m.attachment_id LEFT JOIN messages rm ON rm.id=m.reply_to_id WHERE m.group_id=? AND (? IS NULL OR m.id<=?) ORDER BY m.id DESC LIMIT ?",(gid,around_id,around_id,limit)).fetchall()
    return {"messages":[dict(r) for r in reversed(rows)]}
@app.post("/api/attachments")
async def attachment_upload(file:UploadFile=File(...),authorization:Optional[str]=Header(default=None)):
    u=me_user(authorization)
    # Stream to disk in chunks instead of loading large video files into RAM.
    name=secrets.token_hex(24); path=UPLOADS/name; size=0; limit=100*1024*1024
    original=Path(file.filename or "file").name[:180]
    try:
        with path.open("wb") as out:
            while True:
                chunk=await file.read(1024*1024)
                if not chunk:break
                size+=len(chunk)
                if size>limit:
                    out.close();path.unlink(missing_ok=True)
                    raise HTTPException(413,"Максимальный размер файла 100 МБ")
                out.write(chunk)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    finally:
        await file.close()
    with db() as c:
        cur=c.execute("INSERT INTO attachments(owner,stored_name,original_name,content_type,size,created_at) VALUES(?,?,?,?,?,?)",(u,name,original,file.content_type or "application/octet-stream",size,now()))
    return {"id":cur.lastrowid,"name":original,"size":size,"content_type":file.content_type,"download_url":f"/api/attachments/{cur.lastrowid}"}
@app.get("/api/attachments/{aid}")
def attachment_download(aid:int,authorization:Optional[str]=Header(default=None)):
    u=me_user(authorization)
    with db() as c:
        a=c.execute("SELECT * FROM attachments WHERE id=?",(aid,)).fetchone()
        if not a:raise HTTPException(404,"Файл не найден")
        allowed=a["owner"]==u or c.execute("""SELECT 1 FROM messages m LEFT JOIN group_members gm ON gm.group_id=m.group_id WHERE m.attachment_id=? AND (m.recipient=? OR gm.username=?) LIMIT 1""",(aid,u,u)).fetchone()
        if not allowed:raise HTTPException(403,"Нет доступа")
    p=UPLOADS/a["stored_name"]
    if not p.exists():raise HTTPException(404,"Файл отсутствует")
    return FileResponse(p,media_type=a["content_type"],filename=a["original_name"])
@app.post("/api/messages")
async def send(d:MessageIn,authorization:Optional[str]=Header(default=None)):
    u=me_user(authorization);body=d.body.strip()
    if not body and not d.attachment_id:raise HTTPException(400,"Пустое сообщение")
    if bool(d.recipient)==bool(d.group_id):raise HTTPException(400,"Укажи recipient или group_id")
    if d.reply_to_id is not None:
        with db() as c: accessible_message(c,d.reply_to_id,u)
    with db() as c:
        if d.recipient:
            if not c.execute("SELECT 1 FROM users WHERE username=?",(d.recipient,)).fetchone():raise HTTPException(404,"Получатель не найден")
            if c.execute("SELECT 1 FROM blocked_users WHERE (blocker=? AND blocked=?) OR (blocker=? AND blocked=?)",(u,d.recipient,d.recipient,u)).fetchone():
                raise HTTPException(403,"Отправка сообщений этому пользователю недоступна")
        else:member(c,d.group_id,u)
        if d.attachment_id:
            a=c.execute("SELECT owner FROM attachments WHERE id=?",(d.attachment_id,)).fetchone()
            if not a or a["owner"]!=u:raise HTTPException(403,"Вложение не принадлежит отправителю")
        cur=c.execute("INSERT INTO messages(sender,recipient,group_id,body,created_at,attachment_id,reply_to_id,encrypted) VALUES(?,?,?,?,?,?,?,?)",(u,d.recipient,d.group_id,body,now(),d.attachment_id,d.reply_to_id,int(d.encrypted)))
        msg={"id":cur.lastrowid,"sender":u,"recipient":d.recipient,"group_id":d.group_id,"body":body,"created_at":now(),"attachment_id":d.attachment_id,"reply_to_id":d.reply_to_id,"encrypted":d.encrypted}
        targets=[u,d.recipient] if d.recipient else [r["username"] for r in c.execute("SELECT username FROM group_members WHERE group_id=?",(d.group_id,)).fetchall()]
    await notify_message_targets(targets,msg)
    return {"message":msg}
def accessible_message(c, message_id, username):
    row=c.execute("SELECT * FROM messages WHERE id=?",(message_id,)).fetchone()
    if not row: raise HTTPException(404,"Сообщение не найдено")
    if row["group_id"] is not None:
        member(c,row["group_id"],username)
    elif username not in (row["sender"],row["recipient"]):
        raise HTTPException(403,"Нет доступа к сообщению")
    return row

@app.post("/api/messages/{message_id}/forward")
async def forward_message(message_id:int,d:MessageForward,authorization:Optional[str]=Header(default=None)):
    u=me_user(authorization)
    if bool(d.recipient)==bool(d.group_id):raise HTTPException(400,"Укажи получателя или группу")
    with db() as c:
        source=accessible_message(c,message_id,u)
        if d.recipient:
            if not c.execute("SELECT 1 FROM users WHERE username=?",(d.recipient,)).fetchone():raise HTTPException(404,"Получатель не найден")
            if c.execute("SELECT 1 FROM blocked_users WHERE (blocker=? AND blocked=?) OR (blocker=? AND blocked=?)",(u,d.recipient,d.recipient,u)).fetchone():raise HTTPException(403,"Отправка сообщений этому пользователю недоступна")
        else:member(c,d.group_id,u)
        cur=c.execute("INSERT INTO messages(sender,recipient,group_id,body,created_at,attachment_id,encrypted) VALUES(?,?,?,?,?,?,0)",(u,d.recipient,d.group_id,source["body"],now(),source["attachment_id"]))
        msg={"id":cur.lastrowid,"sender":u,"recipient":d.recipient,"group_id":d.group_id,"body":source["body"],"created_at":now(),"attachment_id":source["attachment_id"],"forwarded":True}
        targets=[u,d.recipient] if d.recipient else [r["username"] for r in c.execute("SELECT username FROM group_members WHERE group_id=?",(d.group_id,)).fetchall()]
    await notify_message_targets(targets,msg)
    return {"message":msg}

@app.patch("/api/messages/{message_id}")
async def edit_message(message_id:int,d:EditMessage,authorization:Optional[str]=Header(default=None)):
    u=me_user(authorization)
    with db() as c:
        row=accessible_message(c,message_id,u)
        if row["sender"]!=u: raise HTTPException(403,"Можно редактировать только свои сообщения")
        c.execute("UPDATE messages SET body=? WHERE id=?",(d.body.strip(),message_id))
        updated=dict(c.execute("SELECT id,sender,recipient,group_id,body,created_at,attachment_id,encrypted FROM messages WHERE id=?",(message_id,)).fetchone())
        targets=[row["sender"],row["recipient"]] if row["group_id"] is None else [x["username"] for x in c.execute("SELECT username FROM group_members WHERE group_id=?",(row["group_id"],)).fetchall()]
    for target in set(x for x in targets if x): await notify(target,{"type":"message_edited","message":updated})
    return {"message":updated}

@app.delete("/api/messages/{message_id}")
async def delete_message(message_id:int,authorization:Optional[str]=Header(default=None)):
    u=me_user(authorization)
    with db() as c:
        row=accessible_message(c,message_id,u)
        if row["sender"]!=u: raise HTTPException(403,"Можно удалять только свои сообщения")
        targets=[row["sender"],row["recipient"]] if row["group_id"] is None else [x["username"] for x in c.execute("SELECT username FROM group_members WHERE group_id=?",(row["group_id"],)).fetchall()]
        c.execute("DELETE FROM messages WHERE id=?",(message_id,))
    for target in set(x for x in targets if x): await notify(target,{"type":"message_deleted","message_id":message_id})
    return {"ok":True}

@app.post("/api/messages/{message_id}/reactions")
async def react_message(message_id:int,d:ReactionIn,authorization:Optional[str]=Header(default=None)):
    u=me_user(authorization); reaction=d.reaction.strip()
    if not reaction or len(reaction)>16: raise HTTPException(422,"Некорректная реакция")
    with db() as c:
        row=accessible_message(c,message_id,u)
        if row["group_id"] is None:
            other=row["recipient"] if row["sender"]==u else row["sender"]
            if not other: raise HTTPException(400,"Некорректный чат")
            blocked=c.execute("SELECT 1 FROM blocked_users WHERE (blocker=? AND blocked=?) OR (blocker=? AND blocked=?)",(u,other,other,u)).fetchone()
            if blocked: raise HTTPException(403,"Взаимодействие недоступно")
        c.execute("INSERT OR IGNORE INTO message_reactions VALUES(?,?,?,?)",(message_id,u,reaction,now()))
        reactions=[dict(x) for x in c.execute("SELECT username,reaction FROM message_reactions WHERE message_id=? ORDER BY created_at",(message_id,)).fetchall()]
    return {"message_id":message_id,"reactions":reactions}

@app.delete("/api/messages/{message_id}/reactions")
def unreact_message(message_id:int,reaction:str,authorization:Optional[str]=Header(default=None)):
    u=me_user(authorization)
    with db() as c:
        accessible_message(c,message_id,u)
        c.execute("DELETE FROM message_reactions WHERE message_id=? AND username=? AND reaction=?",(message_id,u,reaction))
        reactions=[dict(x) for x in c.execute("SELECT username,reaction FROM message_reactions WHERE message_id=? ORDER BY created_at",(message_id,)).fetchall()]
    return {"message_id":message_id,"reactions":reactions}

@app.post("/api/messages/{message_id}/read")
async def mark_read(message_id:int,authorization:Optional[str]=Header(default=None)):
    u=me_user(authorization)
    with db() as c:
        row=accessible_message(c,message_id,u)
        if row["sender"]==u: return {"ok":True,"read_at":None}
        stamp=now()
        c.execute("INSERT INTO message_receipts(message_id,username,delivered_at,read_at) VALUES(?,?,?,?) ON CONFLICT(message_id,username) DO UPDATE SET delivered_at=COALESCE(message_receipts.delivered_at,excluded.delivered_at),read_at=excluded.read_at",(message_id,u,stamp,stamp))
        sender=row["sender"]
    await notify(sender,{"type":"message_read","message_id":message_id,"username":u,"read_at":stamp})
    return {"ok":True,"read_at":stamp}

@app.get("/api/messages/{message_id}/reactions")
def list_reactions(message_id:int,authorization:Optional[str]=Header(default=None)):
    u=me_user(authorization)
    with db() as c:
        accessible_message(c,message_id,u)
        rows=[dict(x) for x in c.execute("SELECT username,reaction FROM message_reactions WHERE message_id=? ORDER BY created_at",(message_id,)).fetchall()]
    return {"reactions":rows}

@app.get("/api/blocks")
def list_blocks(authorization:Optional[str]=Header(default=None)):
    u=me_user(authorization)
    with db() as c: rows=[dict(x) for x in c.execute("SELECT blocked,created_at FROM blocked_users WHERE blocker=? ORDER BY created_at DESC",(u,)).fetchall()]
    return {"blocks":rows}

@app.post("/api/blocks")
def block_user(d:BlockIn,authorization:Optional[str]=Header(default=None)):
    u=me_user(authorization)
    if u.casefold()==d.username.casefold(): raise HTTPException(400,"Нельзя заблокировать себя")
    with db() as c:
        target=c.execute("SELECT username FROM users WHERE username=?",(d.username,)).fetchone()
        if not target: raise HTTPException(404,"Пользователь не найден")
        c.execute("INSERT OR IGNORE INTO blocked_users VALUES(?,?,?)",(u,target["username"],now()))
    return {"ok":True,"blocked":target["username"]}

@app.delete("/api/blocks/{username}")
def unblock_user(username:str,authorization:Optional[str]=Header(default=None)):
    u=me_user(authorization)
    with db() as c:c.execute("DELETE FROM blocked_users WHERE blocker=? AND blocked=?",(u,username))
    return {"ok":True}

@app.get("/api/chat-media/{peer}")
def chat_media(peer:str,authorization:Optional[str]=Header(default=None),group_id:Optional[int]=None):
    u=me_user(authorization)
    with db() as c:
        if group_id is not None:
            member(c,group_id,u)
            rows=c.execute("""SELECT m.id,m.sender,m.body,m.created_at,m.attachment_id,a.original_name AS attachment_name,a.size AS attachment_size,a.content_type AS attachment_content_type FROM messages m JOIN attachments a ON a.id=m.attachment_id WHERE m.group_id=? ORDER BY m.id DESC LIMIT 300""",(group_id,)).fetchall()
        else:
            if not c.execute("SELECT 1 FROM users WHERE username=?",(peer,)).fetchone(): raise HTTPException(404,"Пользователь не найден")
            if c.execute("SELECT 1 FROM blocked_users WHERE (blocker=? AND blocked=?) OR (blocker=? AND blocked=?)",(u,peer,peer,u)).fetchone(): raise HTTPException(403,"Медиа недоступны")
            rows=c.execute("""SELECT m.id,m.sender,m.body,m.created_at,m.attachment_id,a.original_name AS attachment_name,a.size AS attachment_size,a.content_type AS attachment_content_type FROM messages m JOIN attachments a ON a.id=m.attachment_id WHERE m.group_id IS NULL AND ((m.sender=? AND m.recipient=?) OR (m.sender=? AND m.recipient=?)) ORDER BY m.id DESC LIMIT 300""",(u,peer,peer,u)).fetchall()
    return {"media":[dict(x) for x in rows]}

@app.get("/api/unread")
def unread_counts(authorization:Optional[str]=Header(default=None)):
    u=me_user(authorization)
    with db() as c:
        direct=c.execute("""SELECT sender AS peer,COUNT(*) AS count FROM messages m WHERE recipient=? AND group_id IS NULL AND NOT EXISTS(SELECT 1 FROM message_receipts r WHERE r.message_id=m.id AND r.username=? AND r.read_at IS NOT NULL) GROUP BY sender""",(u,u)).fetchall()
        groups=c.execute("""SELECT m.group_id,COUNT(*) AS count FROM messages m JOIN group_members gm ON gm.group_id=m.group_id AND gm.username=? WHERE m.sender<>? AND m.created_at>=gm.joined_at AND NOT EXISTS(SELECT 1 FROM message_receipts r WHERE r.message_id=m.id AND r.username=? AND r.read_at IS NOT NULL) GROUP BY m.group_id""",(u,u,u)).fetchall()
    return {"users":{x["peer"]:x["count"] for x in direct},"groups":{str(x["group_id"]):x["count"] for x in groups}}


def _poll_payload(c, poll_id, username):
    poll=c.execute("SELECT * FROM polls WHERE id=?",(poll_id,)).fetchone()
    if not poll: raise HTTPException(404,"Опрос не найден")
    if poll["group_id"] is not None:
        member(c,poll["group_id"],username)
    elif username not in (poll["creator"],poll["recipient"]):
        raise HTTPException(403,"Нет доступа к опросу")
    options=c.execute("""SELECT o.id,o.label,COUNT(v.username) AS votes,
        MAX(CASE WHEN v.username=? THEN 1 ELSE 0 END) AS selected
        FROM poll_options o LEFT JOIN poll_votes v ON v.option_id=o.id
        WHERE o.poll_id=? GROUP BY o.id ORDER BY o.id""",(username,poll_id)).fetchall()
    total=sum(o["votes"] for o in options)
    return {"id":poll["id"],"creator":poll["creator"],"recipient":poll["recipient"],
            "group_id":poll["group_id"],"question":poll["question"],"created_at":poll["created_at"],
            "total_votes":total,"options":[{"id":o["id"],"label":o["label"],"votes":o["votes"],
            "selected":bool(o["selected"]),"percent":round(o["votes"]*100/total) if total else 0} for o in options]}

@app.post("/api/polls")
def create_poll(d:PollIn,authorization:Optional[str]=Header(default=None)):
    u=me_user(authorization)
    question=d.question.strip()
    options=[x.strip() for x in d.options]
    if not question: raise HTTPException(422,"Вопрос не может быть пустым")
    if bool(d.recipient)==bool(d.group_id): raise HTTPException(400,"Укажи recipient или group_id")
    if any(not x or len(x)>120 for x in options): raise HTTPException(422,"Варианты должны содержать от 1 до 120 символов")
    if len({x.casefold() for x in options})!=len(options): raise HTTPException(422,"Варианты должны быть уникальными")
    with db() as c:
        if d.recipient:
            if d.recipient==u or not c.execute("SELECT 1 FROM users WHERE username=?",(d.recipient,)).fetchone():
                raise HTTPException(404,"Получатель не найден")
            if c.execute("SELECT 1 FROM blocked_users WHERE (blocker=? AND blocked=?) OR (blocker=? AND blocked=?)",(u,d.recipient,d.recipient,u)).fetchone():
                raise HTTPException(403,"Опрос этому пользователю недоступен")
        else: member(c,d.group_id,u)
        cur=c.execute("INSERT INTO polls(creator,recipient,group_id,question,created_at) VALUES(?,?,?,?,?)",(u,d.recipient,d.group_id,question,now()))
        pid=cur.lastrowid
        c.executemany("INSERT INTO poll_options(poll_id,label) VALUES(?,?)",[(pid,x) for x in options])
        result=_poll_payload(c,pid,u)
        targets=[u,d.recipient] if d.recipient else [r["username"] for r in c.execute("SELECT username FROM group_members WHERE group_id=?",(d.group_id,)).fetchall()]
    # Keep notifications generic; poll text may be private.
    for target in targets:
        awaitable_payload={"type":"poll_created","poll_id":pid}
        try:
            loop=asyncio.get_running_loop()
            loop.create_task(notify(target,awaitable_payload))
        except RuntimeError: pass
    return {"poll":result}

@app.get("/api/polls/{poll_id}")
def get_poll(poll_id:int,authorization:Optional[str]=Header(default=None)):
    u=me_user(authorization)
    with db() as c: return {"poll":_poll_payload(c,poll_id,u)}

@app.post("/api/polls/{poll_id}/vote")
def vote_poll(poll_id:int,d:PollVoteIn,authorization:Optional[str]=Header(default=None)):
    u=me_user(authorization)
    with db() as c:
        poll=c.execute("SELECT * FROM polls WHERE id=?",(poll_id,)).fetchone()
        if not poll: raise HTTPException(404,"Опрос не найден")
        if poll["group_id"] is not None: member(c,poll["group_id"],u)
        elif u not in (poll["creator"],poll["recipient"]): raise HTTPException(403,"Нет доступа к опросу")
        if not c.execute("SELECT 1 FROM poll_options WHERE id=? AND poll_id=?",(d.option_id,poll_id)).fetchone():
            raise HTTPException(400,"Вариант ответа не относится к этому опросу")
        c.execute("INSERT INTO poll_votes(poll_id,option_id,username,created_at) VALUES(?,?,?,?) ON CONFLICT(poll_id,username) DO UPDATE SET option_id=excluded.option_id,created_at=excluded.created_at",(poll_id,d.option_id,u,now()))
        result=_poll_payload(c,poll_id,u)
    return {"poll":result}

@app.get("/api/pinned-messages")
def list_pinned(authorization:Optional[str]=Header(default=None)):
    u=me_user(authorization)
    with db() as c:
        rows=[dict(x) for x in c.execute("""SELECT m.id,m.sender,m.recipient,m.group_id,m.body,m.created_at,m.attachment_id FROM pinned_messages p JOIN messages m ON m.id=p.message_id WHERE p.username=? ORDER BY p.created_at DESC""",(u,)).fetchall()]
    return {"messages":rows}

@app.post("/api/messages/{message_id}/pin")
def pin_message(message_id:int,authorization:Optional[str]=Header(default=None)):
    u=me_user(authorization)
    with db() as c:
        accessible_message(c,message_id,u)
        c.execute("INSERT OR IGNORE INTO pinned_messages VALUES(?,?,?)",(u,message_id,now()))
    return {"ok":True}

@app.delete("/api/messages/{message_id}/pin")
def unpin_message(message_id:int,authorization:Optional[str]=Header(default=None)):
    u=me_user(authorization)
    with db() as c:
        accessible_message(c,message_id,u)
        c.execute("DELETE FROM pinned_messages WHERE username=? AND message_id=?",(u,message_id))
    return {"ok":True}

@app.get("/api/presence/{username}")
def get_presence(username:str,authorization:Optional[str]=Header(default=None)):
    me=me_user(authorization)
    with db() as c:
        if not c.execute("SELECT 1 FROM users WHERE username=?",(username,)).fetchone(): raise HTTPException(404,"Пользователь не найден")
        blocked=c.execute("SELECT 1 FROM blocked_users WHERE (blocker=? AND blocked=?) OR (blocker=? AND blocked=?)",(me,username,username,me)).fetchone()
    if blocked: return {"username":username,"online":False}
    with live_lock: online=bool(live.get(username))
    return {"username":username,"online":online}

@app.websocket("/ws")
async def ws_endpoint(ws:WebSocket,token:str=""):
    u=user_token(token)
    if not u:await ws.close(code=1008,reason="Unauthorized");return
    await ws.accept()
    with live_lock:live.setdefault(u,set()).add(ws)
    try:
        with db() as c:
            recipients=[r["username"] for r in c.execute("SELECT username FROM users WHERE username<>?",(u,)).fetchall()]
            c.execute("UPDATE devices SET last_seen=? WHERE username=?",(now(),u))
        for recipient in recipients:
            await notify(recipient,{"type":"presence","username":u,"online":True})
        while True:
            event=await ws.receive_json()
            kind=event.get("type")
            target=event.get("to")
            if kind=="call_signal" and isinstance(target,str):
                await notify(target,{"type":"call_signal","from":u,"payload":event.get("payload",{})})
            elif kind=="typing" and isinstance(target,str) and target!=u:
                with db() as c:
                    exists=c.execute("SELECT 1 FROM users WHERE username=?",(target,)).fetchone()
                    blocked=c.execute("SELECT 1 FROM blocked_users WHERE (blocker=? AND blocked=?) OR (blocker=? AND blocked=?)",(u,target,target,u)).fetchone()
                if exists and not blocked:
                    await notify(target,{"type":"typing","from":u,"active":bool(event.get("active",True))})
    except (WebSocketDisconnect,Exception):pass
    finally:
        with live_lock:
            live.get(u,set()).discard(ws)
            still_online=bool(live.get(u))
        if not still_online:
            with db() as c:
                c.execute("UPDATE devices SET last_seen=? WHERE username=?",(now(),u))
                recipients=[r["username"] for r in c.execute("SELECT username FROM users WHERE username<>?",(u,)).fetchall()]
            for recipient in recipients:
                await notify(recipient,{"type":"presence","username":u,"online":False})
