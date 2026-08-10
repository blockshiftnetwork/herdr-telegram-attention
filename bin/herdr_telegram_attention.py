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
 "es": {"blocked":"Herdr: requiere tu atención", "critical":"🔴 Crítica", "high":"🟠 Alta", "normal":"🟡 Decisión", "ack":"Reconocido", "snooze":"Pospuesto", "resolved":"✅ Resuelto", "agents":"agentes bloqueados", "agent":"Agente", "project":"Proyecto", "branch":"Rama", "task":"Tarea", "reason":"Motivo", "context":"Contexto", "workspace":"Workspace", "tab":"Pestaña", "pane":"Panel", "goal":"Goal", "ack_btn":"✅ Reconocer", "snooze_btn":"⏰ Posponer 30m", "context_btn":"ℹ️ Contexto", "test":"Herdr: Telegram está conectado", "test_body":"Recibirás alertas cuando un agente requiera tu atención.", "goal_pending":"🏁 Agente finalizó · validando goal", "goal_evidence_pending":"⚠️ Goal finalizó · evidencia pendiente", "goal_prompt_failed":"⚠️ Goal finalizó · no se pudo solicitar el reporte", "goal_delivered":"✅ Goal entregado", "goal_review":"⚠️ Goal requiere revisión", "summary":"Resumen", "evidence":"Evidencia", "reference":"Referencia", "goal_timeout":"El agente no entregó un cierre verificable dentro del tiempo configurado."},
 "en": {"blocked":"Herdr: attention required", "critical":"🔴 Critical", "high":"🟠 High", "normal":"🟡 Decision", "ack":"Acknowledged", "snooze":"Snoozed", "resolved":"✅ Resolved", "agents":"blocked agents", "agent":"Agent", "project":"Project", "branch":"Branch", "task":"Task", "reason":"Reason", "context":"Context", "workspace":"Workspace", "tab":"Tab", "pane":"Pane", "goal":"Goal", "ack_btn":"✅ Acknowledge", "snooze_btn":"⏰ Snooze 30m", "context_btn":"ℹ️ Context", "test":"Herdr: Telegram is connected", "test_body":"You will receive alerts when an agent needs your attention.", "goal_pending":"🏁 Agent finished · validating goal", "goal_evidence_pending":"⚠️ Goal finished · evidence pending", "goal_prompt_failed":"⚠️ Goal finished · could not request report", "goal_delivered":"✅ Goal delivered", "goal_review":"⚠️ Goal needs review", "summary":"Summary", "evidence":"Evidence", "reference":"Reference", "goal_timeout":"The agent did not provide a verifiable closure within the configured time."},
 "pt": {"blocked":"Herdr: requer sua atenção", "critical":"🔴 Crítica", "high":"🟠 Alta", "normal":"🟡 Decisão", "ack":"Reconhecido", "snooze":"Adiado", "resolved":"✅ Resolvido", "agents":"agentes bloqueados", "agent":"Agente", "project":"Projeto", "branch":"Ramo", "task":"Tarefa", "reason":"Motivo", "context":"Contexto", "workspace":"Workspace", "tab":"Aba", "pane":"Painel", "goal":"Goal", "ack_btn":"✅ Reconhecer", "snooze_btn":"⏰ Adiar 30m", "context_btn":"ℹ️ Contexto", "test":"Herdr: Telegram conectado", "test_body":"Você receberá alertas quando um agente precisar da sua atenção.", "goal_pending":"🏁 Agente terminou · validando goal", "goal_evidence_pending":"⚠️ Goal terminou · evidência pendente", "goal_prompt_failed":"⚠️ Goal terminou · não foi possível pedir o relatório", "goal_delivered":"✅ Goal entregue", "goal_review":"⚠️ Goal requer revisão", "summary":"Resumo", "evidence":"Evidência", "reference":"Referência", "goal_timeout":"O agente não entregou um encerramento verificável dentro do tempo configurado."}
}

def config():
    out = {"TELEGRAM_LANGUAGE":"es", "TELEGRAM_NOTIFY_DONE":"false", "TELEGRAM_SNOOZE_MINUTES":"30", "TELEGRAM_AUTO_REGISTER_GOALS":"true", "TELEGRAM_GOAL_REPORT_TIMEOUT_SECONDS":"180"}
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
    if not STATE.exists(): return {"offset":0,"incidents":{},"goals":{}}
    try:
        state=json.loads(STATE.read_text()); state.setdefault("incidents",{}); state.setdefault("goals",{})
        # Goals saved by versions before stable IDs remain usable and receive a
        # deterministic legacy ID rather than being mistaken for a new goal.
        for pane, goal in state["goals"].items():
            goal.setdefault("pane", pane)
            goal.setdefault("id", hashlib.sha256(f"legacy:{pane}:{goal.get('registered', '')}".encode()).hexdigest()[:12])
            goal.setdefault("workspace", "")
            goal.setdefault("tab", "")
            # Do not emit new timeout alerts for goals created before this
            # version: their message lacked the identity users need to act on.
            goal.setdefault("timeout_eligible", False)
        return state
    except json.JSONDecodeError: return {"offset":0,"incidents":{},"goals":{}}
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

def context_data():
    raw=json.loads(os.getenv("HERDR_PLUGIN_CONTEXT_JSON","{}"))
    return {"pane":str(raw.get("focused_pane_id", "")), "agent":str(raw.get("focused_pane_agent", "")), "workspace":str(raw.get("workspace_id", raw.get("focused_workspace_id", ""))), "tab":str(raw.get("tab_id", raw.get("focused_tab_id", ""))), "project":str(raw.get("workspace_label", "")), "task":str(raw.get("terminal_title_stripped", raw.get("tab_label", "")))}

def request_goal_report(goal):
    binary=os.getenv("HERDR_BIN_PATH", "herdr")
    command=f"{ROOT}/bin/herdr-telegram-attention --goal-report --goal-id {goal['id']} --outcome completed --summary 'short result' --evidence 'tests or PR'"
    prompt=("This is a managed goal. Before you finish, verify your work. If and only if it is complete, "
            f"run this exact command with truthful summary and evidence: {command}. "
            "Use outcome partial or failed instead of completed when appropriate.")
    try:
        return subprocess.run([binary,"agent","prompt",goal["pane"],prompt],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10,check=False).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False

def new_goal(d, now, automatic=False):
    return {"id":uuid.uuid4().hex[:12], "pane":d["pane"], "agent":d["agent"], "workspace":d.get("workspace", ""), "tab":d.get("tab", ""), "project":d.get("project", ""), "task":d.get("task", ""), "phase":"assigned", "registered":now, "automatic":automatic, "timeout_eligible":True}

def goal_identity(c, goal):
    fields=(("goal", "id"), ("workspace", "workspace"), ("tab", "tab"), ("pane", "pane"))
    return "\n".join(f"{title(c, label)}: {goal.get(key, '') or '—'}" for label,key in fields)

def render_goal(c, goal, headline, summary="", evidence="", url="", detail=""):
    lines=[headline, f"{title(c, 'agent')}: {goal['agent']}", f"{title(c, 'project')}: {goal['project']}"]
    if goal.get("task"): lines.append(f"{title(c, 'task')}: {goal['task']}")
    lines.append(goal_identity(c, goal))
    if summary: lines.append(f"{title(c, 'summary')}: {summary}")
    if evidence: lines.append(f"{title(c, 'evidence')}: {evidence}")
    if url: lines.append(f"{title(c, 'reference')}: {url}")
    if detail: lines.append(detail)
    return "\n".join(lines)[:3900]

def update_goal_message(c, goal, text):
    data={"chat_id":c["TELEGRAM_CHAT_ID"],"text":text[:3900]}
    if goal.get("message_id"):
        api(c,"editMessageText",{**data,"message_id":goal["message_id"]})
    else:
        goal["message_id"]=api(c,"sendMessage",data)["message_id"]

def goal_report_timeout(c):
    try: return max(30, min(86400, int(c.get("TELEGRAM_GOAL_REPORT_TIMEOUT_SECONDS", "180"))))
    except ValueError: return 180

def reconcile_goal_timeouts(c, state, now):
    changed=False
    for goal in state["goals"].values():
        if goal.get("timeout_eligible") and goal.get("phase") == "awaiting_report" and now - goal.get("requested", now) >= goal_report_timeout(c):
            goal.update({"phase":"evidence_pending", "timed_out":now})
            update_goal_message(c, goal, render_goal(c, goal, title(c, "goal_evidence_pending"), detail=title(c, "goal_timeout")))
            changed=True
    return changed

def goal_register(c):
    d=context_data()
    if not d["pane"] or not d["agent"]: raise RuntimeError("focus the target agent pane before registering its goal")
    h=lock(); state=load_state(); state["goals"][d["pane"]]=new_goal(d, int(time.time())); save_state(state); h.close()
    print(f"goal registered for {d['agent']} in {d['pane']}")

def goal_report(c, args):
    pane=os.getenv("HERDR_PANE_ID", "")
    if not pane: raise RuntimeError("goal reports must run inside the registered Herdr agent pane")
    h=lock()
    try:
        state=load_state(); goal=state["goals"].get(pane)
        if not goal or goal.get("phase") not in ("awaiting_report", "evidence_pending", "prompt_failed"): raise RuntimeError("no managed goal is awaiting a report in this pane")
        fields=dict(zip(args[::2],args[1::2]))
        goal_id=fields.get("--goal-id", ""); outcome=fields.get("--outcome", ""); summary=fields.get("--summary", "")[:512]; evidence=fields.get("--evidence", "")[:512]; url=fields.get("--url", "")[:512]
        if goal_id and goal_id != goal.get("id"): raise RuntimeError("goal ID does not match the managed goal in this pane")
        if outcome not in ("completed","partial","failed") or not summary or not evidence: raise RuntimeError("report requires outcome, summary, and evidence")
        label=title(c, "goal_delivered") if outcome=="completed" else title(c, "goal_review")
        update_goal_message(c, goal, render_goal(c, goal, label, summary, evidence, url))
        goal.update({"phase":"reported","outcome":outcome,"reported":int(time.time())}); save_state(state)
    finally:
        h.close()
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
    auto_register=c.get("TELEGRAM_AUTO_REGISTER_GOALS", "true").lower() == "true"
    goal=state["goals"].get(d["pane"])
    auto_registered=False
    if auto_register and d["pane"] and d["agent"] and (not goal or (goal.get("phase") == "reported" and d["state"] == "working")):
        state["goals"][d["pane"]]=new_goal(d, now, automatic=True)
        auto_registered=True
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
    elif d["state"]=="done" and d["pane"] in state["goals"]:
        goal=state["goals"][d["pane"]]
        if goal.get("phase")=="assigned":
            goal["phase"]="awaiting_report"; goal["requested"]=now
            if not dry:
                update_goal_message(c, goal, render_goal(c, goal, title(c, "goal_pending")))
                if not request_goal_report(goal):
                    goal.update({"phase":"prompt_failed", "prompt_failed":now})
                    update_goal_message(c, goal, render_goal(c, goal, title(c, "goal_prompt_failed")))
                save_state(state)
    elif d["pane"]:
        for i in state["incidents"].values():
            if d["pane"] in i["agents"] and i["status"]!="resolved":
                del i["agents"][d["pane"]]
                if not i["agents"]: i["status"]="resolved"
                i["updated"]=now
                if not dry: update_message(c,i); save_state(state)
    if not dry and (auto_registered or reconcile_goal_timeouts(c, state, now)): save_state(state)
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
        h=lock()
        try:
            s=load_state(); changed=reconcile_goal_timeouts(c, s, int(time.time()))
            if changed: save_state(s)
        except Exception as e:
            print(f"telegram dispatcher: {e}",file=sys.stderr); time.sleep(5); continue
        finally:
            h.close()
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
    elif arg=="--goal-register": goal_register(c)
    elif arg=="--goal-report": goal_report(c,sys.argv[2:])
    elif arg=="--test": api(c,"sendMessage",{"chat_id":c["TELEGRAM_CHAT_ID"],"text":title(c,"test")+"\n"+title(c,"test_body")})
    elif arg=="--status": print(f"configured={bool(c.get('TELEGRAM_BOT_TOKEN') and c.get('TELEGRAM_CHAT_ID'))}\ndispatcher=run via the dispatcher pane\nlanguage={c.get('TELEGRAM_LANGUAGE')}\n")
    elif arg=="--listen": listen(c)
    else: print("Usage: --event | --goal-register | --goal-report | --test | --status | --listen [--dry-run]"); return 2
    return 0
if __name__=="__main__": raise SystemExit(main())
