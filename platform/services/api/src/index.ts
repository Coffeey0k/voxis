import "dotenv/config";
import http from "node:http";
import crypto from "node:crypto";
import express, { type Request, type Response, type NextFunction } from "express";
import cors from "cors";
import helmet from "helmet";
import rateLimit from "express-rate-limit";
import jwt from "jsonwebtoken";
import { Pool } from "pg";
import { Redis } from "ioredis";
import { Server } from "socket.io";
import { createAdapter } from "@socket.io/redis-adapter";
import { S3Client, PutObjectCommand } from "@aws-sdk/client-s3";
import { getSignedUrl } from "@aws-sdk/s3-request-presigner";
import { z } from "zod";

const config = z.object({
 NODE_ENV:z.enum(["development","test","production"]).default("development"),
 PORT:z.coerce.number().int().positive().default(4000),
 WEB_ORIGIN:z.string().default("http://localhost:5173"),
 DATABASE_URL:z.string().default("postgres://voxis:voxis_local_password@localhost:5432/voxis"),
 REDIS_URL:z.string().default("redis://localhost:6379"),
 JWT_SECRET:z.string().min(32).default("development-only-secret-change-this-32"),
 OTP_HASH_SECRET:z.string().min(32).default("development-only-otp-secret-change-this"),
 OTP_TTL_SECONDS:z.coerce.number().int().min(60).max(900).default(300),
 OTP_DEV_MODE:z.string().default("true"),
 TWILIO_ACCOUNT_SID:z.string().optional(), TWILIO_AUTH_TOKEN:z.string().optional(), TWILIO_FROM_NUMBER:z.string().optional(),
 S3_ENDPOINT:z.string().default("http://localhost:9000"), S3_PUBLIC_ENDPOINT:z.string().default("http://localhost:9000"), S3_REGION:z.string().default("us-east-1"),
 S3_BUCKET:z.string().default("voxis"), S3_ACCESS_KEY:z.string().default("minioadmin"),
 S3_SECRET_KEY:z.string().default("change-this-minio-secret"), MAX_UPLOAD_BYTES:z.coerce.number().int().positive().default(104857600)
}).parse(process.env);
if(config.NODE_ENV==="production"&&(config.JWT_SECRET.startsWith("development-")||config.OTP_HASH_SECRET.startsWith("development-")||config.OTP_DEV_MODE==="true"))throw new Error("Production requires strong secrets and OTP_DEV_MODE=false.");
const pool=new Pool({connectionString:config.DATABASE_URL,max:20});
const redis=new Redis(config.REDIS_URL),pub=new Redis(config.REDIS_URL),sub=new Redis(config.REDIS_URL);
const s3=new S3Client({region:config.S3_REGION,endpoint:config.S3_PUBLIC_ENDPOINT,forcePathStyle:true,credentials:{accessKeyId:config.S3_ACCESS_KEY,secretAccessKey:config.S3_SECRET_KEY}});
const app=express(),server=http.createServer(app);
const io=new Server(server,{cors:{origin:config.WEB_ORIGIN.split(",").map(x=>x.trim()),credentials:true},maxHttpBufferSize:1e6});
io.adapter(createAdapter(pub,sub));
app.disable("x-powered-by");app.use(helmet({crossOriginResourcePolicy:{policy:"cross-origin"}}));
app.use(cors({origin:config.WEB_ORIGIN.split(",").map(x=>x.trim()),credentials:true}));app.use(express.json({limit:"1mb"}));
app.use("/api/v1/auth",rateLimit({windowMs:900000,limit:15,standardHeaders:true,legacyHeaders:false}));
app.use("/api/v1",rateLimit({windowMs:60000,limit:240,standardHeaders:true,legacyHeaders:false}));
type Req=Request&{userId?:string;sessionId?:string};
const route=(fn:(req:Req,res:Response)=>Promise<unknown>)=>(req:Request,res:Response,next:NextFunction)=>Promise.resolve(fn(req as Req,res)).catch(next);
const phoneSchema=z.string().regex(/^\+[1-9]\d{7,14}$/);
const sha=(v:string)=>crypto.createHash("sha256").update(v).digest("hex");
const codeHash=(phone:string,code:string)=>crypto.createHmac("sha256",config.OTP_HASH_SECRET).update(phone+":"+code).digest("hex");
const sign=(uid:string,sid:string)=>jwt.sign({sub:uid,sid},config.JWT_SECRET,{expiresIn:"30d",issuer:"voxis-api",audience:"voxis-client"});
async function auth(req:Req,res:Response,next:NextFunction){
 try{const h=req.headers.authorization;if(!h?.startsWith("Bearer "))return res.status(401).json({error:"Authentication required"});
 const token=h.slice(7),claims=jwt.verify(token,config.JWT_SECRET,{issuer:"voxis-api",audience:"voxis-client"}) as jwt.JwtPayload;
 const q=await pool.query("SELECT id FROM sessions WHERE id=$1 AND user_id=$2 AND token_hash=$3 AND revoked_at IS NULL AND expires_at>now()",[claims.sid,claims.sub,sha(token)]);
 if(!q.rowCount)return res.status(401).json({error:"Session expired or revoked"});req.userId=String(claims.sub);req.sessionId=String(claims.sid);next();
 }catch{return res.status(401).json({error:"Invalid session"});}
}
async function member(chatId:string,userId:string){const q=await pool.query("SELECT 1 FROM chat_members WHERE chat_id=$1 AND user_id=$2",[chatId,userId]);return Boolean(q.rowCount);}
const api=express.Router();
api.get("/health/live",(_req,res)=>res.json({ok:true,service:"voxis-api"}));
api.get("/health/ready",route(async(_req,res)=>{await pool.query("SELECT 1");await redis.ping();return res.json({ok:true,database:"ok",redis:"ok"});}));
api.post("/auth/request-code",route(async(req,res)=>{
 const p=z.object({phone:phoneSchema}).safeParse(req.body);if(!p.success)return res.status(400).json({error:"Phone must be E.164 formatted, e.g. +421900123456"});
 const phone=p.data.phone;const recent=await pool.query("SELECT count(*)::int n FROM auth_challenges WHERE phone_e164=$1 AND created_at>now()-interval '1 hour'",[phone]);
 if((recent.rows[0]?.n??0)>=5)return res.status(429).json({error:"Too many code requests"});
 const code=String(crypto.randomInt(0,1000000)).padStart(6,"0");
 await pool.query("UPDATE auth_challenges SET consumed_at=now() WHERE phone_e164=$1 AND consumed_at IS NULL",[phone]);
 await pool.query("INSERT INTO auth_challenges(phone_e164,code_hash,expires_at) VALUES($1,$2,now()+($3*interval '1 second'))",[phone,codeHash(phone,code),config.OTP_TTL_SECONDS]);
 if(config.OTP_DEV_MODE==="true"&&config.NODE_ENV!=="production")return res.json({ok:true,expiresIn:config.OTP_TTL_SECONDS,developmentCode:code});
 const {TWILIO_ACCOUNT_SID:sid,TWILIO_AUTH_TOKEN:secret,TWILIO_FROM_NUMBER:from}=config;
 if(!sid||!secret||!from)return res.status(503).json({error:"SMS provider is not configured"});
 const body=new URLSearchParams({To:phone,From:from,Body:"Your Voxis verification code is "+code});
 const response=await fetch("https://api.twilio.com/2010-04-01/Accounts/"+sid+"/Messages.json",{method:"POST",headers:{Authorization:"Basic "+Buffer.from(sid+":"+secret).toString("base64"),"Content-Type":"application/x-www-form-urlencoded"},body});
 if(!response.ok){await pool.query("UPDATE auth_challenges SET consumed_at=now() WHERE phone_e164=$1 AND consumed_at IS NULL",[phone]);return res.status(502).json({error:"SMS delivery failed"});}
 return res.json({ok:true,expiresIn:config.OTP_TTL_SECONDS});
}));
api.post("/auth/verify-code",route(async(req,res)=>{
 const p=z.object({phone:phoneSchema,code:z.string().regex(/^\d{6}$/),deviceName:z.string().trim().min(1).max(120).default("Web")}).safeParse(req.body);
 if(!p.success)return res.status(400).json({error:"Invalid phone, code or device name"});
 const client=await pool.connect();
 try{
  await client.query("BEGIN");const q=await client.query("SELECT id,code_hash,attempts FROM auth_challenges WHERE phone_e164=$1 AND consumed_at IS NULL AND expires_at>now() ORDER BY created_at DESC LIMIT 1 FOR UPDATE",[p.data.phone]);const c=q.rows[0];
  if(!c||c.attempts>=8){await client.query("ROLLBACK");return res.status(400).json({error:"Code expired or invalid"});}
  const expected=Buffer.from(String(c.code_hash)),provided=Buffer.from(codeHash(p.data.phone,p.data.code));
  if(expected.length!==provided.length||!crypto.timingSafeEqual(expected,provided)){await client.query("UPDATE auth_challenges SET attempts=attempts+1 WHERE id=$1",[c.id]);await client.query("COMMIT");return res.status(400).json({error:"Code expired or invalid"});}
  await client.query("UPDATE auth_challenges SET consumed_at=now() WHERE id=$1",[c.id]);
  const u=(await client.query("INSERT INTO users(phone_e164,display_name) VALUES($1,$2) ON CONFLICT(phone_e164) DO UPDATE SET updated_at=now() RETURNING id,phone_e164,username,display_name,bio",[p.data.phone,p.data.phone.slice(-4)])).rows[0];
  const sid=(await client.query("INSERT INTO sessions(user_id,token_hash,device_name,expires_at) VALUES($1,'pending-'||gen_random_uuid()::text,$2,now()+interval '30 days') RETURNING id",[u.id,p.data.deviceName])).rows[0].id;
  const token=sign(u.id,sid);await client.query("UPDATE sessions SET token_hash=$1 WHERE id=$2",[sha(token),sid]);await client.query("COMMIT");
  return res.json({token,user:{id:u.id,phone:u.phone_e164,username:u.username,display_name:u.display_name,bio:u.bio}});
 }catch(e){await client.query("ROLLBACK");throw e;}finally{client.release();}
}));
api.post("/auth/logout",auth,route(async(req,res)=>{await pool.query("UPDATE sessions SET revoked_at=now() WHERE id=$1",[req.sessionId]);return res.json({ok:true});}));
api.get("/users/me",auth,route(async(req,res)=>{const q=await pool.query("SELECT id,phone_e164,username,display_name,bio,avatar_key,last_seen_at,created_at FROM users WHERE id=$1",[req.userId]);return res.json({user:q.rows[0]});}));
api.patch("/users/me",auth,route(async(req,res)=>{
 const p=z.object({username:z.string().regex(/^[A-Za-z0-9_]{4,32}$/).optional(),display_name:z.string().trim().min(1).max(80).optional(),bio:z.string().max(300).optional()}).refine(v=>Object.keys(v).length>0).safeParse(req.body);
 if(!p.success)return res.status(400).json({error:"Invalid profile fields"});const v=p.data;
 try{const q=await pool.query("UPDATE users SET username=COALESCE($2,username),display_name=COALESCE($3,display_name),bio=COALESCE($4,bio),updated_at=now() WHERE id=$1 RETURNING id,phone_e164,username,display_name,bio",[req.userId,v.username??null,v.display_name??null,v.bio??null]);return res.json({user:q.rows[0]});}
 catch(e){if((e as {code?:string}).code==="23505")return res.status(409).json({error:"Username already taken"});throw e;}
}));
api.get("/users/search",auth,route(async(req,res)=>{const p=z.string().trim().min(2).max(64).safeParse(req.query.q);if(!p.success)return res.status(400).json({error:"Search query must contain 2–64 characters"});const q=await pool.query("SELECT id,username,display_name,avatar_key,last_seen_at FROM users WHERE id<>$1 AND (username ILIKE $2 OR display_name ILIKE $2) ORDER BY username NULLS LAST LIMIT 30",[req.userId,"%"+p.data.replace(/[\\%_]/g,"\\$&")+"%"]);return res.json({users:q.rows});}));
api.get("/chats",auth,route(async(req,res)=>{const q=await pool.query("SELECT c.id,c.kind,c.title,c.owner_id,cm.role,c.created_at,(SELECT json_build_object('body',m.body,'createdAt',m.created_at) FROM messages m WHERE m.chat_id=c.id AND m.deleted_at IS NULL ORDER BY m.created_at DESC LIMIT 1) last_message FROM chats c JOIN chat_members cm ON cm.chat_id=c.id WHERE cm.user_id=$1 ORDER BY COALESCE((SELECT max(created_at) FROM messages WHERE chat_id=c.id),c.created_at) DESC LIMIT 500",[req.userId]);return res.json({chats:q.rows});}));
api.post("/chats",auth,route(async(req,res)=>{
 const p=z.object({kind:z.enum(["direct","group","channel","secret"]),title:z.string().trim().max(120).default(""),memberIds:z.array(z.string().uuid()).max(2000).default([])}).safeParse(req.body);if(!p.success)return res.status(400).json({error:"Invalid chat details"});
 if(p.data.kind==="direct"&&p.data.memberIds.length!==1)return res.status(400).json({error:"A direct chat requires one other member"});
 const ids=[...new Set([req.userId!,...p.data.memberIds])];const client=await pool.connect();
 try{await client.query("BEGIN");if(p.data.kind==="direct"){const old=await client.query("SELECT c.id,c.kind,c.title FROM chats c JOIN chat_members a ON a.chat_id=c.id AND a.user_id=$1 JOIN chat_members b ON b.chat_id=c.id AND b.user_id=$2 WHERE c.kind='direct' AND (SELECT count(*) FROM chat_members WHERE chat_id=c.id)=2 LIMIT 1",[req.userId,p.data.memberIds[0]]);if(old.rowCount){await client.query("COMMIT");return res.json({chat:old.rows[0],existing:true});}}
 const c=(await client.query("INSERT INTO chats(kind,title,owner_id) VALUES($1,$2,$3) RETURNING id,kind,title,owner_id,created_at",[p.data.kind,p.data.title,req.userId])).rows[0];
 for(const id of ids)await client.query("INSERT INTO chat_members(chat_id,user_id,role) VALUES($1,$2,$3)",[c.id,id,id===req.userId?"owner":"member"]);
 await client.query("COMMIT");return res.status(201).json({chat:c});
 }catch(e){await client.query("ROLLBACK");throw e;}finally{client.release();}
}));
api.get("/chats/:id/messages",auth,route(async(req,res)=>{
 if(!z.string().uuid().safeParse(String(req.params.id)).success)return res.status(400).json({error:"Invalid chat id"});
 if(!await member(String(req.params.id),req.userId!))return res.status(403).json({error:"Not a chat member"});
 const before=typeof req.query.before==="string"?req.query.before:null;const limit=Math.min(100,Math.max(1,Number(req.query.limit)||50));
 const q=await pool.query("SELECT m.id,m.chat_id,m.sender_id,m.body,m.encrypted,m.reply_to_id,m.forwarded_from_id,m.edited_at,m.created_at,u.username,u.display_name FROM messages m JOIN users u ON u.id=m.sender_id WHERE m.chat_id=$1 AND m.deleted_at IS NULL AND ($2::timestamptz IS NULL OR m.created_at<$2) ORDER BY m.created_at DESC LIMIT $3",[String(req.params.id),before,limit]);return res.json({messages:q.rows.reverse()});
}));
api.post("/chats/:id/messages",auth,route(async(req,res)=>{
 const p=z.object({body:z.string().max(50000).default(""),replyToId:z.string().uuid().optional(),forwardedFromId:z.string().uuid().optional(),encrypted:z.boolean().default(false)}).refine(v=>v.body.trim().length>0||v.encrypted).safeParse(req.body);
 if(!p.success)return res.status(400).json({error:"Message must contain text or encrypted payload"});if(!await member(String(req.params.id),req.userId!))return res.status(403).json({error:"Not a chat member"});
 const v=p.data;const q=await pool.query("INSERT INTO messages(chat_id,sender_id,body,reply_to_id,forwarded_from_id,encrypted) VALUES($1,$2,$3,$4,$5,$6) RETURNING id,chat_id,sender_id,body,encrypted,reply_to_id,forwarded_from_id,created_at",[String(req.params.id),req.userId,v.body,v.replyToId??null,v.forwardedFromId??null,v.encrypted]);
 io.to("chat:"+String(req.params.id)).emit("message:new",q.rows[0]);return res.status(201).json({message:q.rows[0]});
}));
api.patch("/messages/:id",auth,route(async(req,res)=>{const p=z.object({body:z.string().trim().min(1).max(50000)}).safeParse(req.body);if(!p.success)return res.status(400).json({error:"Invalid message body"});const q=await pool.query("UPDATE messages m SET body=$2,edited_at=now() WHERE m.id=$1 AND m.sender_id=$3 AND m.deleted_at IS NULL RETURNING m.id,m.chat_id,m.body,m.edited_at",[String(req.params.id),p.data.body,req.userId]);if(!q.rowCount)return res.status(404).json({error:"Message not found or not editable"});io.to("chat:"+q.rows[0].chat_id).emit("message:updated",q.rows[0]);return res.json({message:q.rows[0]});}));
api.delete("/messages/:id",auth,route(async(req,res)=>{const q=await pool.query("UPDATE messages SET deleted_at=now(),body='' WHERE id=$1 AND sender_id=$2 AND deleted_at IS NULL RETURNING id,chat_id",[String(req.params.id),req.userId]);if(!q.rowCount)return res.status(404).json({error:"Message not found or not deletable"});io.to("chat:"+q.rows[0].chat_id).emit("message:deleted",{id:String(req.params.id),chatId:q.rows[0].chat_id});return res.json({ok:true});}));
api.post("/messages/:id/reactions",auth,route(async(req,res)=>{const p=z.object({emoji:z.string().min(1).max(32)}).safeParse(req.body);if(!p.success)return res.status(400).json({error:"Invalid emoji"});const found=await pool.query("SELECT m.chat_id FROM messages m JOIN chat_members cm ON cm.chat_id=m.chat_id AND cm.user_id=$2 WHERE m.id=$1 AND m.deleted_at IS NULL",[String(req.params.id),req.userId]);if(!found.rowCount)return res.status(404).json({error:"Message not found"});await pool.query("INSERT INTO message_reactions(message_id,user_id,emoji) VALUES($1,$2,$3) ON CONFLICT DO NOTHING",[String(req.params.id),req.userId,p.data.emoji]);const q=await pool.query("SELECT r.emoji,r.user_id,u.username FROM message_reactions r JOIN users u ON u.id=r.user_id WHERE r.message_id=$1",[String(req.params.id)]);io.to("chat:"+found.rows[0].chat_id).emit("reaction:updated",{messageId:String(req.params.id),reactions:q.rows});return res.json({reactions:q.rows});}));
api.post("/uploads/presign",auth,route(async(req,res)=>{const p=z.object({chatId:z.string().uuid(),fileName:z.string().trim().min(1).max(255),contentType:z.string().min(1).max(150),sizeBytes:z.number().int().positive().max(config.MAX_UPLOAD_BYTES)}).safeParse(req.body);if(!p.success)return res.status(400).json({error:"Invalid upload metadata or file too large"});if(!await member(p.data.chatId,req.userId!))return res.status(403).json({error:"Not a chat member"});const id=crypto.randomUUID(),key=p.data.chatId+"/"+id;const command=new PutObjectCommand({Bucket:config.S3_BUCKET,Key:key,ContentType:p.data.contentType,ContentLength:p.data.sizeBytes});const uploadUrl=await getSignedUrl(s3,command,{expiresIn:300});await pool.query("INSERT INTO attachments(id,chat_id,uploader_id,object_key,file_name,content_type,size_bytes) VALUES($1,$2,$3,$4,$5,$6,$7)",[id,p.data.chatId,req.userId,key,p.data.fileName,p.data.contentType,p.data.sizeBytes]);return res.status(201).json({attachmentId:id,uploadUrl,expiresIn:300});}));
api.get("/search",auth,route(async(req,res)=>{const p=z.string().trim().min(2).max(200).safeParse(req.query.q);if(!p.success)return res.status(400).json({error:"Search query must contain 2–200 characters"});const q=await pool.query("SELECT m.id,m.chat_id,m.sender_id,m.body,m.created_at FROM messages m JOIN chat_members cm ON cm.chat_id=m.chat_id AND cm.user_id=$1 WHERE m.deleted_at IS NULL AND m.encrypted=false AND m.body ILIKE $2 ORDER BY m.created_at DESC LIMIT 100",[req.userId,"%"+p.data.replace(/[\\%_]/g,"\\$&")+"%"]);return res.json({messages:q.rows});}));
app.use("/api/v1",api);
io.use(async(socket,nextFn)=>{try{const token=socket.handshake.auth?.token;if(typeof token!=="string")return nextFn(new Error("Authentication required"));const c=jwt.verify(token,config.JWT_SECRET,{issuer:"voxis-api",audience:"voxis-client"}) as jwt.JwtPayload;const q=await pool.query("SELECT id FROM sessions WHERE id=$1 AND user_id=$2 AND token_hash=$3 AND revoked_at IS NULL AND expires_at>now()",[c.sid,c.sub,sha(token)]);if(!q.rowCount)return nextFn(new Error("Session expired"));socket.data.userId=String(c.sub);nextFn();}catch{nextFn(new Error("Invalid session"));}});
io.on("connection",socket=>{const uid=String(socket.data.userId);socket.join("user:"+uid);
 socket.on("chat:join",async(p:unknown,ack?:(v:unknown)=>void)=>{const v=z.object({chatId:z.string().uuid()}).safeParse(p);if(!v.success||!await member(v.data.chatId,uid)){ack?.({ok:false,error:"Not a chat member"});return;}socket.join("chat:"+v.data.chatId);ack?.({ok:true});});
 for(const event of ["typing:start","typing:stop"] as const)socket.on(event,async(p:unknown)=>{const v=z.object({chatId:z.string().uuid()}).safeParse(p);if(v.success&&await member(v.data.chatId,uid))socket.to("chat:"+v.data.chatId).emit("typing:changed",{chatId:v.data.chatId,userId:uid,active:event==="typing:start"});});
 socket.on("disconnect",()=>{void pool.query("UPDATE users SET last_seen_at=now() WHERE id=$1",[uid]).catch(()=>undefined);});
});
app.use((err:unknown,_req:Request,res:Response,_next:NextFunction)=>{if(config.NODE_ENV!=="production")console.error(err);const e=err as {status?:number;message?:string};res.status(e.status&&e.status>=400&&e.status<600?e.status:500).json({error:config.NODE_ENV==="production"?"Internal server error":e.message||"Internal server error"});});
server.listen(config.PORT,()=>console.log("Voxis API listening on :"+config.PORT));
async function shutdown(){server.close();io.close();await pool.end();await Promise.all([redis.quit(),pub.quit(),sub.quit()]);process.exit(0);}
process.on("SIGTERM",()=>void shutdown());process.on("SIGINT",()=>void shutdown());