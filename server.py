import hashlib, hmac, os, secrets, sqlite3, threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, HTTPException, Header, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

ROOT=Path(__file__).resolve().parent
DB=Path(os.getenv("VOXIS_DB", str(ROOT/"voxis.sqlite3")))
UPLOADS=Path(os.getenv("VOXIS_UPLOAD_DIR", str(ROOT/"uploads")))
UPLOADS.mkdir(parents=True, exist_ok=True)
app=FastAPI(title="Voxis API", version="0.2.0")
live: dict[str,set[WebSocket]]={}
live_lock=threading.Lock()
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
        for n,ddl in [("group_id","INTEGER REFERENCES groups_chat(id)"),("attachment_id","INTEGER REFERENCES attachments(id)"),("encrypted","INTEGER NOT NULL DEFAULT 0")]:
            if n not in cols(c,"messages"): c.execute(f"ALTER TABLE messages ADD COLUMN {n} {ddl}")
        c.execute("CREATE INDEX IF NOT EXISTS idx_group_messages ON messages(group_id,id)")
        if 3 not in done: c.execute("INSERT INTO schema_migrations VALUES(3,?)",(now(),))
        if 4 not in done:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS message_receipts(message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,username TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,delivered_at TEXT,read_at TEXT,PRIMARY KEY(message_id,username));
            CREATE TABLE IF NOT EXISTS message_reactions(message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,username TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,reaction TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(message_id,username,reaction));
            CREATE TABLE IF NOT EXISTS blocked_users(blocker TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,blocked TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,created_at TEXT NOT NULL,PRIMARY KEY(blocker,blocked),CHECK(blocker<>blocked));
            CREATE TABLE IF NOT EXISTS pinned_messages(username TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,created_at TEXT NOT NULL,PRIMARY KEY(username,message_id));
            CREATE INDEX IF NOT EXISTS idx_receipts_user ON message_receipts(username,read_at);
            """)
            c.execute("INSERT INTO schema_migrations VALUES(4,?)",(now(),))
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
class MessageIn(BaseModel):
    recipient:Optional[str]=None; group_id:Optional[int]=None
    body:str=Field(default="",max_length=4000); attachment_id:Optional[int]=None; encrypted:bool=False
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
    with db() as c:rows=c.execute("""SELECT g.id,g.name,g.owner,g.created_at,(SELECT COUNT(*) FROM group_members x WHERE x.group_id=g.id) member_count FROM groups_chat g JOIN group_members m ON m.group_id=g.id WHERE m.username=? ORDER BY g.id DESC""",(u,)).fetchall()
    return {"groups":[dict(r) for r in rows]}
@app.get("/api/groups/{gid}/members")
def group_members(gid:int,authorization:Optional[str]=Header(default=None)):
    u=me_user(authorization)
    with db() as c:
        member(c,gid,u); rows=c.execute("SELECT username,role,joined_at FROM group_members WHERE group_id=?",(gid,)).fetchall()
    return {"members":[dict(r) for r in rows]}
@app.post("/api/groups/{gid}/members")
async def group_add(gid:int,d:GroupIn,authorization:Optional[str]=Header(default=None)):
    u=me_user(authorization)
    with db() as c:
        g=c.execute("SELECT owner FROM groups_chat WHERE id=?",(gid,)).fetchone()
        if not g:raise HTTPException(404,"Группа не найдена")
        if g["owner"]!=u:raise HTTPException(403,"Только владелец может добавлять участников")
        for m in d.members:
            if not c.execute("SELECT 1 FROM users WHERE username=?",(m,)).fetchone():raise HTTPException(404,f"Нет пользователя {m}")
            c.execute("INSERT OR IGNORE INTO group_members VALUES(?,?,?,?)",(gid,m,"member",now()))
    for m in d.members:await notify(m,{"type":"group_updated","group_id":gid})
    return {"ok":True}
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
def direct_history(peer:str,authorization:Optional[str]=Header(default=None),limit:int=100):
    u=me_user(authorization);limit=max(1,min(limit,200))
    with db() as c:
        if not c.execute("SELECT 1 FROM users WHERE username=?",(peer,)).fetchone():raise HTTPException(404,"Пользователь не найден")
        rows=c.execute("""SELECT m.id,m.sender,m.recipient,m.group_id,m.body,m.created_at,m.attachment_id,m.encrypted,a.original_name AS attachment_name,a.size AS attachment_size,a.content_type AS attachment_content_type FROM messages m LEFT JOIN attachments a ON a.id=m.attachment_id WHERE m.group_id IS NULL AND ((sender=? AND recipient=?) OR (sender=? AND recipient=?)) ORDER BY m.id DESC LIMIT ?""",(u,peer,peer,u,limit)).fetchall()
    return {"messages":[dict(r) for r in reversed(rows)]}
@app.get("/api/group-messages/{gid}")
def group_history(gid:int,authorization:Optional[str]=Header(default=None),limit:int=100):
    u=me_user(authorization);limit=max(1,min(limit,200))
    with db() as c:
        member(c,gid,u);rows=c.execute("SELECT m.id,m.sender,m.recipient,m.group_id,m.body,m.created_at,m.attachment_id,m.encrypted,a.original_name AS attachment_name,a.size AS attachment_size,a.content_type AS attachment_content_type FROM messages m LEFT JOIN attachments a ON a.id=m.attachment_id WHERE m.group_id=? ORDER BY m.id DESC LIMIT ?",(gid,limit)).fetchall()
    return {"messages":[dict(r) for r in reversed(rows)]}
@app.post("/api/attachments")
async def attachment_upload(file:UploadFile=File(...),authorization:Optional[str]=Header(default=None)):
    u=me_user(authorization); b=await file.read(20*1024*1024+1)
    if len(b)>20*1024*1024:raise HTTPException(413,"Максимальный размер файла 20 МБ")
    name=secrets.token_hex(24);(UPLOADS/name).write_bytes(b);original=Path(file.filename or "file").name[:180]
    with db() as c:
        cur=c.execute("INSERT INTO attachments(owner,stored_name,original_name,content_type,size,created_at) VALUES(?,?,?,?,?,?)",(u,name,original,file.content_type or "application/octet-stream",len(b),now()))
    return {"id":cur.lastrowid,"name":original,"size":len(b),"content_type":file.content_type,"download_url":f"/api/attachments/{cur.lastrowid}"}
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
    with db() as c:
        if d.recipient:
            if not c.execute("SELECT 1 FROM users WHERE username=?",(d.recipient,)).fetchone():raise HTTPException(404,"Получатель не найден")
            if c.execute("SELECT 1 FROM blocked_users WHERE (blocker=? AND blocked=?) OR (blocker=? AND blocked=?)",(u,d.recipient,d.recipient,u)).fetchone():
                raise HTTPException(403,"Отправка сообщений этому пользователю недоступна")
        else:member(c,d.group_id,u)
        if d.attachment_id:
            a=c.execute("SELECT owner FROM attachments WHERE id=?",(d.attachment_id,)).fetchone()
            if not a or a["owner"]!=u:raise HTTPException(403,"Вложение не принадлежит отправителю")
        cur=c.execute("INSERT INTO messages(sender,recipient,group_id,body,created_at,attachment_id,encrypted) VALUES(?,?,?,?,?,?,?)",(u,d.recipient,d.group_id,body,now(),d.attachment_id,int(d.encrypted)))
        msg={"id":cur.lastrowid,"sender":u,"recipient":d.recipient,"group_id":d.group_id,"body":body,"created_at":now(),"attachment_id":d.attachment_id,"encrypted":d.encrypted}
        targets=[u,d.recipient] if d.recipient else [r["username"] for r in c.execute("SELECT username FROM group_members WHERE group_id=?",(d.group_id,)).fetchall()]
    for target in set(targets):await notify(target,{"type":"message","message":msg})
    return {"message":msg}
def accessible_message(c, message_id, username):
    row=c.execute("SELECT * FROM messages WHERE id=?",(message_id,)).fetchone()
    if not row: raise HTTPException(404,"Сообщение не найдено")
    if row["group_id"] is not None:
        member(c,row["group_id"],username)
    elif username not in (row["sender"],row["recipient"]):
        raise HTTPException(403,"Нет доступа к сообщению")
    return row

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

@app.websocket("/ws")
async def ws_endpoint(ws:WebSocket,token:str=""):
    u=user_token(token)
    if not u:await ws.close(code=1008,reason="Unauthorized");return
    await ws.accept()
    with live_lock:live.setdefault(u,set()).add(ws)
    try:
        while True:
            event=await ws.receive_json()
            if event.get("type")=="call_signal":
                target=event.get("to")
                if isinstance(target,str):await notify(target,{"type":"call_signal","from":u,"payload":event.get("payload",{})})
    except (WebSocketDisconnect,Exception):pass
    finally:
        with live_lock:live.get(u,set()).discard(ws)
