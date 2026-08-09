#!/usr/bin/env python3
"""Herdr Telegram attention control plane: grouping, prioritization, callbacks."""
import fcntl, hashlib, json, os, pathlib, re, subprocess, sys, time, urllib.parse, urllib.request, uuid

PLUGIN_ID = "blockshiftnetwork.telegram-attention"
ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG = pathlib.Path(os.getenv("HERDR_PLUGIN_CONFIG_DIR", pathlib.Path.home()/".config/herdr/plugins/config"/PLUGIN_ID)) / ".env"
STATE_DIR = pathlib.Path(os.getenv("HERDR_PLUGIN_STATE_DIR", CONFIG.parent/"state")); STATE = STATE_DIR / "incidents.json"
MAX_FIELD_LENGTH = 512
MAX_INCIDENTS = 200
RESOLVED_TTL_SECONDS = 7 * 24 * 60 * 60

TEXT = {
 "es": {"blocked":"Herdr: requiere tu atención", "critical":"🔴 Crítica", "high":"🟠 Alta", "normal":"🟡 Decisión", "ack":"Reconocido", "snooze":"Pospuesto", "resolved":"✅ Resuelto", "agents":"agentes bloqueados", "agent":"Agente", "project":"Proyecto", "branch":"Rama", "task":"Tarea", "reason":"Motivo", "context":"Contexto", "ack_btn":"✅ Reconocer", "snooze_btn":"⏰ Posponer 30m", "context_btn":"ℹ️ Contexto", "test":"Herdr: Telegram está conectado", "test_body":"Recibirás alertas cuando un agente requiera tu atención."},
 "en": {"blocked":"Herdr: attention required", "critical":"🔴 Critical", "high":"🟠 High", "normal":"🟡 Decision", "ack":"Acknowledged", "snooze":"Snoozed", "resolved":"✅ Resolved", "agents":"blocked agents", "agent":"Agent", "project":"Project", "branch":"Branch", "task":"Task", "reason":"Reason", "context":"Context", "ack_btn":"✅ Acknowledge", "snooze_btn":"⏰ Snooze 30m", "context_btn":"ℹ️ Context", "test":"Herdr: Telegram is connected", "test_body":"You will receive alerts when an agent needs your attention."}
}

def config():
    out = {"TELEGRAM_LANGUAGE":"es", "TELEGRAM_NOTIFY_DONE":"false", "TELEGRAM_SNOOZE_MINUTES":"30"}
    for source in (CONFIG,):
        if source.exists():
            for line in source.read_text().splitlines():
                if "=" in line and not line.lstrip().startswith("#"):
                    k,v=line.split("=",1); out[k.strip()]=v.strip()
    for k in out.copy():
        if os.getenv(k): out[k]=os.environ[k]
    return out

def api(c, method, data):
    token=c.get("TELEGRAM_BOT_TOKEN", "");
    if not token: raise RuntimeError("Telegram is not configured")
    base=c.get('TELEGRAM_API_BASE','https://api.telegram.org').rstrip('/')
    parsed=urllib.parse.urlparse(base)
    if parsed.scheme != "https" or parsed.hostname != "api.telegram.org" or parsed.port or parsed.username or parsed.password:
        raise RuntimeError("TELEGRAM_API_BASE must be exactly https://api.telegram.org")
    url=f"{base}/bot{token}/{method}"
    request=urllib.request.Request(url, urllib.parse.urlencode(data).encode(), method="POST")
    with urllib.request.urlopen(request, timeout=35) as r: payload=json.load(r)
    if not payload.get("ok"): raise RuntimeError(payload.get("description", "Telegram request failed"))
    return payload["result"]

def load_state():
    if not STATE.exists(): return {"offset":0,"incidents":{}}
    try: return json.loads(STATE.read_text())
    except json.JSONDecodeError: return {"offset":0,"incidents":{}}
def save_state(state):
    STATE_DIR.mkdir(parents=True, exist_ok=True); os.chmod(STATE_DIR,0o700)
    tmp=STATE.with_suffix(".tmp"); tmp.write_text(json.dumps(state, separators=(",",":"))); os.chmod(tmp,0o600); tmp.replace(STATE)
def lock():
    STATE_DIR.mkdir(parents=True, exist_ok=True); p=STATE_DIR/".lock"; h=open(p,"a+"); fcntl.flock(h,fcntl.LOCK_EX); return h

def prune_state(state, now):
    incidents=state["incidents"]
    for iid, incident in list(incidents.items()):
        if incident.get("status") == "resolved" and now - incident.get("updated", incident.get("created", now)) > RESOLVED_TTL_SECONDS:
            del incidents[iid]
    if len(incidents) > MAX_INCIDENTS:
        resolved=sorted((i.get("updated", i.get("created", 0)), iid) for iid,i in incidents.items() if i.get("status") == "resolved")
        for _, iid in resolved:
            if len(incidents) <= MAX_INCIDENTS: break
            del incidents[iid]

def event_data():
    e=json.loads(os.environ["HERDR_PLUGIN_EVENT_JSON"]); ctx=json.loads(os.getenv("HERDR_PLUGIN_CONTEXT_JSON","{}"))
    sources=[]
    for x in (e,ctx):
        if isinstance(x,dict):
            sources.append(x); sources += [x[k] for k in ("event","payload","data","pane") if isinstance(x.get(k),dict)]
    def get(*keys):
        for s in sources:
            for k in keys:
                if s.get(k) not in (None,""): return str(s[k])[:MAX_FIELD_LENGTH]
        return ""
    return {"state":get("agent_status","status","state"),"agent":get("agent","agent_name","display_agent"),"workspace":get("workspace_id","workspace"),"tab":get("tab_id","tab"),"pane":get("pane_id","pane"),"reason":get("message","reason"),"seq":get("state_change_seq","revision","sequence"),"project":get("workspace_label","project","workspace_name"),"cwd":get("foreground_cwd","workspace_cwd","cwd"),"task":get("terminal_title_stripped","terminal_title","title")}
def branch(cwd):
    try: return subprocess.check_output(["git","-C",cwd,"branch","--show-current"],text=True,stderr=subprocess.DEVNULL,timeout=2).strip()
    except Exception: return ""
def priority(text):
    t=text.lower()
    if re.search(r"security|seguridad|production|producci.n|outage|incidente|ca.da",t): return "critical"
    if re.search(r"deploy|despliegue|approval|aprobaci.n|migration|migraci.n",t): return "high"
    return "normal"
def title(c,k): return TEXT.get(c.get("TELEGRAM_LANGUAGE","es"),TEXT["es"])[k]
def render(c,i):
    t=lambda x:title(c,x); count=len(i["agents"]); lines=[f"{t(i['priority'])} · {t('blocked')}",f"{count} {t('agents')} · {i['status']}"]
    for label,key in (("project","project"),("branch","branch"),("task","task"),("reason","reason")):
        if i.get(key): lines.append(f"{t(label)}: {i[key]}")
    lines.append(f"{t('context')}: {', '.join(sorted(a['agent'] or a['pane'] for a in i['agents'].values()))}")
    return "\n".join(lines)[:3900]
def markup(c,i):
    if i["status"]=="resolved": return {"inline_keyboard":[]}
    p=i["id"]; return {"inline_keyboard":[[{"text":title(c,"ack_btn"),"callback_data":f"hta:{p}:ack"},{"text":title(c,"snooze_btn"),"callback_data":f"hta:{p}:snooze"}],[{"text":title(c,"context_btn"),"callback_data":f"hta:{p}:context"}]]}
def update_message(c,i):
    rendered=render(c,i); keyboard=json.dumps(markup(c,i),ensure_ascii=False)
    if i.get("rendered")==rendered and i.get("keyboard")==keyboard: return
    data={"chat_id":c["TELEGRAM_CHAT_ID"],"text":rendered,"reply_markup":keyboard}
    if i.get("message_id"): api(c,"editMessageText",{**data,"message_id":i["message_id"]})
    else: i["message_id"]=api(c,"sendMessage",data)["message_id"]
    i["rendered"]=rendered; i["keyboard"]=keyboard

def handle_event(c,d,dry=False):
    h=lock(); state=load_state(); now=int(time.time()); prune_state(state, now)
    if d["state"]=="blocked":
        reason=re.sub(r"\s+"," ",d["reason"].lower()).strip(); key=hashlib.sha256(f"{d['workspace']}|{reason or d['pane']}".encode()).hexdigest()[:16]
        i=next((x for x in state["incidents"].values() if x["key"]==key and x["status"]!="resolved"),None)
        if not i:
            if len(state["incidents"]) >= MAX_INCIDENTS:
                raise RuntimeError("incident state limit reached; resolve or prune existing incidents")
            iid=uuid.uuid4().hex[:12]; i={"id":iid,"key":key,"status":"pending","priority":priority(" ".join(d.values())),"created":now,"agents":{},"message_id":None}; state["incidents"][iid]=i
        d["branch"]=branch(d["cwd"]) if d["cwd"] else ""; i["agents"][d["pane"]]=d
        for k in ("project","branch","task","reason"):
            i[k]=d.get(k,"") or i.get(k,"")
        i["updated"]=now
        if dry: print(render(c,i))
        else: update_message(c,i); save_state(state)
    elif d["pane"]:
        for i in state["incidents"].values():
            if d["pane"] in i["agents"] and i["status"]!="resolved":
                del i["agents"][d["pane"]]
                if not i["agents"]: i["status"]="resolved"
                i["updated"]=now
                if not dry: update_message(c,i); save_state(state)
    h.close()

def callback(c,q):
    chat=str(q.get("message",{}).get("chat",{}).get("id","")); data=q.get("data","")
    if chat != str(c.get("TELEGRAM_CHAT_ID")): return api(c,"answerCallbackQuery",{"callback_query_id":q["id"],"text":"Unauthorized","show_alert":"true"})
    m=re.fullmatch(r"hta:([0-9a-f]{12}):(ack|snooze|context)",data)
    if not m: return api(c,"answerCallbackQuery",{"callback_query_id":q["id"]})
    h=lock(); state=load_state(); now=int(time.time()); prune_state(state, now); i=state["incidents"].get(m.group(1)); action=m.group(2)
    if not i: return api(c,"answerCallbackQuery",{"callback_query_id":q["id"],"text":"Incident expired"})
    if action=="ack": i["status"]="acknowledged"; i["updated"]=now; notice=title(c,"ack")
    elif action=="snooze": i["status"]="snoozed"; i["updated"]=now; i["snoozed_until"]=now+60*int(c.get("TELEGRAM_SNOOZE_MINUTES","30")); notice=title(c,"snooze")
    else: notice=" · ".join(a["agent"] or a["pane"] for a in i["agents"].values())[:180]
    if action!="context": update_message(c,i); save_state(state)
    api(c,"answerCallbackQuery",{"callback_query_id":q["id"],"text":notice}); h.close()

def listen(c):
    while True:
        h=lock(); s=load_state(); h.close()
        try: updates=api(c,"getUpdates",{"offset":s["offset"],"timeout":25,"allowed_updates":json.dumps(["callback_query"])})
        except Exception as e: print(f"telegram dispatcher: {e}",file=sys.stderr); time.sleep(5); continue
        for u in updates:
            if "callback_query" in u: callback(c,u["callback_query"])
            s["offset"]=u["update_id"]+1
        if updates:
            h=lock(); current=load_state(); current["offset"]=max(current.get("offset",0),s["offset"]); save_state(current); h.close()

def main():
    c=config(); arg=sys.argv[1] if len(sys.argv)>1 else "--help"
    if arg=="--event": handle_event(c,event_data(),"--dry-run" in sys.argv)
    elif arg=="--test": api(c,"sendMessage",{"chat_id":c["TELEGRAM_CHAT_ID"],"text":title(c,"test")+"\n"+title(c,"test_body")})
    elif arg=="--status": print(f"configured={bool(c.get('TELEGRAM_BOT_TOKEN') and c.get('TELEGRAM_CHAT_ID'))}\ndispatcher=run via the dispatcher pane\nlanguage={c.get('TELEGRAM_LANGUAGE')}\n")
    elif arg=="--listen": listen(c)
    else: print("Usage: --event | --test | --status | --listen [--dry-run]"); return 2
    return 0
if __name__=="__main__": raise SystemExit(main())
