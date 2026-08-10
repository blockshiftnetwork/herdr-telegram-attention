#!/usr/bin/env python3
"""Herdr Telegram attention control plane: grouping, prioritization, callbacks."""
import fcntl, hashlib, json, os, pathlib, re, subprocess, sys, time, urllib.parse, urllib.request, uuid

PLUGIN_ID = "blockshiftnetwork.telegram-attention"
CONFIG = pathlib.Path(os.getenv("HERDR_PLUGIN_CONFIG_DIR", pathlib.Path.home()/".config/herdr/plugins/config"/PLUGIN_ID)) / ".env"
# Herdr sets HERDR_PLUGIN_STATE_DIR for event hooks, but direct commands run
# inside an agent pane do not receive that variable. Use Herdr's standard state
# location as the fallback so both paths read the same plugin state.
STATE_DIR = pathlib.Path(os.getenv("HERDR_PLUGIN_STATE_DIR", pathlib.Path(os.getenv("XDG_STATE_HOME", pathlib.Path.home()/".local/state"))/"herdr/plugins"/PLUGIN_ID)); STATE = STATE_DIR / "incidents.json"
MAX_FIELD_LENGTH = 512
MAX_INCIDENTS = 200
MAX_AVAILABLE_AGENTS = 200
MAX_AGENTS_PER_QUEUE = 50
RESOLVED_TTL_SECONDS = 7 * 24 * 60 * 60

TEXT = {
 "es": {"blocked":"Herdr: requiere tu atención", "critical":"🔴 Crítica", "high":"🟠 Alta", "normal":"🟡 Decisión", "ack":"Reconocido", "snooze":"Pospuesto", "resolved":"✅ Resuelto", "agents":"agentes bloqueados", "agent":"Agente", "project":"Proyecto", "branch":"Rama", "task":"Tarea", "reason":"Motivo", "context":"Contexto", "workspace":"Workspace", "tab":"Pestaña", "pane":"Panel", "goal":"Goal", "ack_btn":"✅ Reconocer", "snooze_btn":"⏰ Posponer 30m", "context_btn":"ℹ️ Contexto", "test":"Herdr: Telegram está conectado", "test_body":"Recibirás alertas cuando un agente requiera tu atención.", "goal_pending":"🏁 Agente finalizó · validando goal", "goal_evidence_pending":"⚠️ Goal finalizó · evidencia pendiente", "goal_prompt_failed":"⚠️ Goal finalizó · no se pudo solicitar el reporte", "goal_delivered":"✅ Goal entregado", "goal_review":"⚠️ Goal requiere revisión", "summary":"Resumen", "evidence":"Evidencia", "reference":"Referencia", "goal_timeout":"El agente no entregó un cierre verificable dentro del tiempo configurado."},
 "en": {"blocked":"Herdr: attention required", "critical":"🔴 Critical", "high":"🟠 High", "normal":"🟡 Decision", "ack":"Acknowledged", "snooze":"Snoozed", "resolved":"✅ Resolved", "agents":"blocked agents", "agent":"Agent", "project":"Project", "branch":"Branch", "task":"Task", "reason":"Reason", "context":"Context", "workspace":"Workspace", "tab":"Tab", "pane":"Pane", "goal":"Goal", "ack_btn":"✅ Acknowledge", "snooze_btn":"⏰ Snooze 30m", "context_btn":"ℹ️ Context", "test":"Herdr: Telegram is connected", "test_body":"You will receive alerts when an agent needs your attention.", "goal_pending":"🏁 Agent finished · validating goal", "goal_evidence_pending":"⚠️ Goal finished · evidence pending", "goal_prompt_failed":"⚠️ Goal finished · could not request report", "goal_delivered":"✅ Goal delivered", "goal_review":"⚠️ Goal needs review", "summary":"Summary", "evidence":"Evidence", "reference":"Reference", "goal_timeout":"The agent did not provide a verifiable closure within the configured time."},
 "pt": {"blocked":"Herdr: requer sua atenção", "critical":"🔴 Crítica", "high":"🟠 Alta", "normal":"🟡 Decisão", "ack":"Reconhecido", "snooze":"Adiado", "resolved":"✅ Resolvido", "agents":"agentes bloqueados", "agent":"Agente", "project":"Projeto", "branch":"Ramo", "task":"Tarefa", "reason":"Motivo", "context":"Contexto", "workspace":"Workspace", "tab":"Aba", "pane":"Painel", "goal":"Goal", "ack_btn":"✅ Reconhecer", "snooze_btn":"⏰ Adiar 30m", "context_btn":"ℹ️ Contexto", "test":"Herdr: Telegram conectado", "test_body":"Você receberá alertas quando um agente precisar da sua atenção.", "goal_pending":"🏁 Agente terminou · validando goal", "goal_evidence_pending":"⚠️ Goal terminou · evidência pendente", "goal_prompt_failed":"⚠️ Goal terminou · não foi possível pedir o relatório", "goal_delivered":"✅ Goal entregue", "goal_review":"⚠️ Goal requer revisão", "summary":"Resumo", "evidence":"Evidência", "reference":"Referência", "goal_timeout":"O agente não entregou um encerramento verificável dentro do tempo configurado."}
}

TEXT["es"].update({"available":"🟢 Agentes disponibles", "available_agents":"agentes listos para recibir tarea", "available_reviewed":"✅ Disponibles revisados", "review_btn":"✅ Marcar revisado", "available_context_btn":"ℹ️ Ver agentes", "overflow":"Hay más agentes disponibles que los mostrados."})
TEXT["en"].update({"available":"🟢 Agents available", "available_agents":"agents ready for a new task", "available_reviewed":"✅ Availability reviewed", "review_btn":"✅ Mark reviewed", "available_context_btn":"ℹ️ View agents", "overflow":"More available agents exist than are shown."})
TEXT["pt"].update({"available":"🟢 Agentes disponíveis", "available_agents":"agentes prontos para uma nova tarefa", "available_reviewed":"✅ Disponíveis revisados", "review_btn":"✅ Marcar revisado", "available_context_btn":"ℹ️ Ver agentes", "overflow":"Há mais agentes disponíveis do que os exibidos."})

def config():
    out = {"TELEGRAM_LANGUAGE":"es", "TELEGRAM_SNOOZE_MINUTES":"30", "TELEGRAM_NOTIFY_AVAILABLE":"true", "TELEGRAM_ALLOWED_USER_ID":""}
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
    if not STATE.exists(): return {"offset":0,"incidents":{},"goals":{},"availability":{},"availability_seen":{}}
    try:
        state=json.loads(STATE.read_text()); state.setdefault("incidents",{}); state.setdefault("goals",{}); state.setdefault("availability",{}); state.setdefault("availability_seen",{})
        return state
    except json.JSONDecodeError: return {"offset":0,"incidents":{},"goals":{},"availability":{},"availability_seen":{}}
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
    for aid, available in list(state["availability"].items()):
        if available.get("status") == "reviewed" and now - available.get("updated", available.get("created", now)) > RESOLVED_TTL_SECONDS:
            del state["availability"][aid]
    seen=state["availability_seen"]
    for pane, entry in list(seen.items()):
        if now - entry.get("updated", now) > RESOLVED_TTL_SECONDS:
            del seen[pane]

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

def availability_key(d):
    return hashlib.sha256(f"{d['workspace']}|{d['project'] or d['workspace']}".encode()).hexdigest()[:16]

def render_availability(c, available):
    agents=list(available["agents"].values())
    lines=[title(c, "available"), f"{len(agents)} {title(c, 'available_agents')}"]
    if available.get("project"): lines.append(f"{title(c, 'project')}: {available['project']}")
    if available.get("workspace"): lines.append(f"{title(c, 'workspace')}: {available['workspace']}")
    for agent in agents[:MAX_AGENTS_PER_QUEUE]:
        identity=agent["agent"] or agent["pane"]
        lines.append(f"• {identity} · {title(c, 'pane')}: {agent['pane']}" + (f" · {title(c, 'task')}: {agent['task']}" if agent.get("task") else ""))
    if available.get("overflow"): lines.append(title(c, "overflow"))
    if available.get("status") == "reviewed": lines[0]=title(c, "available_reviewed")
    return "\n".join(lines)[:3900]

def availability_markup(c, available):
    if available.get("status") == "reviewed": return {"inline_keyboard":[]}
    aid=available["id"]
    return {"inline_keyboard":[[{"text":title(c, "review_btn"),"callback_data":f"hta:v:{aid}:clear"},{"text":title(c, "available_context_btn"),"callback_data":f"hta:v:{aid}:context"}]]}

def update_availability_message(c, available):
    rendered=render_availability(c, available); keyboard=json.dumps(availability_markup(c, available),ensure_ascii=False)
    if available.get("rendered") == rendered and available.get("keyboard") == keyboard: return
    data={"chat_id":c["TELEGRAM_CHAT_ID"],"text":rendered,"reply_markup":keyboard}
    if available.get("message_id"): api(c,"editMessageText",{**data,"message_id":available["message_id"]})
    else: available["message_id"]=api(c,"sendMessage",data)["message_id"]
    available["rendered"]=rendered; available["keyboard"]=keyboard

def remove_available_agent(state, pane, now):
    changed=[]
    for available in state["availability"].values():
        if pane in available.get("agents", {}):
            del available["agents"][pane]; available["updated"]=now; changed.append(available)
            if not available["agents"] and available.get("status") != "reviewed": available["status"]="reviewed"
    return changed

def add_available_agent(state, d, now):
    if not d["pane"] or not d["agent"]: return None
    seen=state["availability_seen"].get(d["pane"], {})
    if d.get("seq") and seen.get("done_seq") == d["seq"]: return None
    key=availability_key(d)
    available=next((x for x in state["availability"].values() if x.get("key") == key and x.get("status") != "reviewed"), None)
    if not available:
        available={"id":uuid.uuid4().hex[:12],"key":key,"status":"available","workspace":d["workspace"],"project":d["project"],"created":now,"updated":now,"agents":{},"message_id":None}
        state["availability"][available["id"]]=available
    if d["pane"] not in available["agents"]:
        active=sum(len(x.get("agents",{})) for x in state["availability"].values() if x.get("status") != "reviewed")
        if active >= MAX_AVAILABLE_AGENTS or len(available["agents"]) >= MAX_AGENTS_PER_QUEUE:
            available["overflow"]=min(MAX_AVAILABLE_AGENTS, available.get("overflow", 0)+1)
        else:
            available["agents"][d["pane"]]={k:d.get(k, "") for k in ("agent","workspace","tab","pane","project","task")}
    state["availability_seen"][d["pane"]]={"done_seq":d.get("seq", ""),"updated":now}
    available["updated"]=now
    return available

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
    h=lock()
    try:
        state=load_state(); now=int(time.time()); prune_state(state, now); changed=False
        if d["state"] != "done":
            removed=remove_available_agent(state, d["pane"], now); changed=bool(removed)
            if not dry:
                for available in removed: update_availability_message(c, available)
        if d["state"] in ("working", "blocked"):
            state["availability_seen"].pop(d["pane"], None)
        if d["state"]=="blocked":
            reason=re.sub(r"\s+"," ",d["reason"].lower()).strip(); key=hashlib.sha256(f"{d['workspace']}|{reason or d['pane']}".encode()).hexdigest()[:16]
            i=next((x for x in state["incidents"].values() if x["key"]==key and x["status"]!="resolved"),None)
            if not i:
                if len(state["incidents"]) >= MAX_INCIDENTS: raise RuntimeError("incident state limit reached; resolve or prune existing incidents")
                iid=uuid.uuid4().hex[:12]; i={"id":iid,"key":key,"status":"pending","priority":priority(" ".join(d.values())),"created":now,"agents":{},"message_id":None}; state["incidents"][iid]=i
            d["branch"]=branch(d["cwd"]) if d["cwd"] else ""; i["agents"][d["pane"]]=d
            for k in ("project","branch","task","reason"): i[k]=d.get(k,"") or i.get(k,"")
            i["updated"]=now; changed=True
            if dry: print(render(c,i))
            else: update_message(c,i)
        else:
            for i in state["incidents"].values():
                if d["pane"] in i["agents"] and i["status"]!="resolved":
                    del i["agents"][d["pane"]]
                    if not i["agents"]: i["status"]="resolved"
                    i["updated"]=now; changed=True
                    if not dry: update_message(c,i)
            if d["state"] == "done" and c.get("TELEGRAM_NOTIFY_AVAILABLE", "true").lower() == "true":
                available=add_available_agent(state, d, now)
                if available:
                    changed=True
                    if dry: print(render_availability(c, available))
                    else: update_availability_message(c, available)
        if changed and not dry: save_state(state)
    finally:
        h.close()

def callback(c,q):
    chat=str(q.get("message",{}).get("chat",{}).get("id","")); data=q.get("data","")
    if chat != str(c.get("TELEGRAM_CHAT_ID")): return api(c,"answerCallbackQuery",{"callback_query_id":q["id"],"text":"Unauthorized","show_alert":"true"})
    allowed_user=str(c.get("TELEGRAM_ALLOWED_USER_ID", ""))
    if allowed_user and str(q.get("from",{}).get("id", "")) != allowed_user:
        return api(c,"answerCallbackQuery",{"callback_query_id":q["id"],"text":"Unauthorized","show_alert":"true"})
    incident=re.fullmatch(r"hta:([0-9a-f]{12}):(ack|snooze|context)",data)
    available_match=re.fullmatch(r"hta:v:([0-9a-f]{12}):(clear|context)",data)
    if not incident and not available_match: return api(c,"answerCallbackQuery",{"callback_query_id":q["id"]})
    h=lock()
    try:
        state=load_state(); now=int(time.time()); prune_state(state, now)
        if incident:
            i=state["incidents"].get(incident.group(1)); action=incident.group(2)
            if not i: return api(c,"answerCallbackQuery",{"callback_query_id":q["id"],"text":"Incident expired"})
            if action=="ack": i["status"]="acknowledged"; i["updated"]=now; notice=title(c,"ack")
            elif action=="snooze": i["status"]="snoozed"; i["updated"]=now; i["snoozed_until"]=now+60*int(c.get("TELEGRAM_SNOOZE_MINUTES","30")); notice=title(c,"snooze")
            else: notice=" · ".join(a["agent"] or a["pane"] for a in i["agents"].values())[:180]
            if action!="context": update_message(c,i); save_state(state)
        else:
            available=state["availability"].get(available_match.group(1)); action=available_match.group(2)
            if not available: return api(c,"answerCallbackQuery",{"callback_query_id":q["id"],"text":"Availability expired"})
            if action=="clear":
                available.update({"status":"reviewed","updated":now,"agents":{}}); update_availability_message(c,available); save_state(state); notice=title(c,"available_reviewed")
            else:
                notice=" · ".join(f"{a['agent'] or a['pane']} ({a['pane']})" for a in available["agents"].values())[:180] or title(c,"available_reviewed")
        api(c,"answerCallbackQuery",{"callback_query_id":q["id"],"text":notice})
    finally:
        h.close()

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

def availability_status(c):
    h=lock()
    try:
        state=load_state(); queues=[a for a in state["availability"].values() if a.get("status") != "reviewed"]
        agents=sum(len(a.get("agents",{})) for a in queues)
    finally:
        h.close()
    print(f"configured={bool(c.get('TELEGRAM_BOT_TOKEN') and c.get('TELEGRAM_CHAT_ID'))}\navailability_notifications={c.get('TELEGRAM_NOTIFY_AVAILABLE','true')}\navailable_agents={agents}\navailability_queues={len(queues)}\ndispatcher=run via the dispatcher pane\nlanguage={c.get('TELEGRAM_LANGUAGE')}\n")

def main():
    c=config(); arg=sys.argv[1] if len(sys.argv)>1 else "--help"
    if arg=="--event": handle_event(c,event_data(),"--dry-run" in sys.argv)
    elif arg in ("--goal-register", "--goal-report"):
        print("Managed goal reporting has been retired; no Telegram message or agent prompt was sent.")
    elif arg=="--test": api(c,"sendMessage",{"chat_id":c["TELEGRAM_CHAT_ID"],"text":title(c,"test")+"\n"+title(c,"test_body")})
    elif arg=="--status": availability_status(c)
    elif arg=="--listen": listen(c)
    else: print("Usage: --event | --test | --status | --listen [--dry-run]"); return 2
    return 0
if __name__=="__main__": raise SystemExit(main())
