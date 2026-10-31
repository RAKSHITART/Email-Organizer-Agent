"""Gmail-style web UI for the Email Organizer (pure HTML/CSS, no external assets).

Renders from a LocalDashboard controller. Every dynamic value is HTML-escaped.
The UI can only call owner actions; it has no code path that dispatches mail.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone
from email.utils import parseaddr
from html import escape as esc
from urllib.parse import quote

CSS = """
*{box-sizing:border-box}body{margin:0;font-family:'Google Sans',Roboto,Arial,sans-serif;color:#202124;background:#f6f8fc;font-size:14px}
a{color:inherit;text-decoration:none}
.top{height:64px;display:flex;align-items:center;gap:16px;padding:0 16px;background:#f6f8fc}
.brand{display:flex;align-items:center;gap:10px;width:240px;font-size:22px;color:#5f6368}
.brand svg{width:32px;height:32px}
.search{flex:1;max-width:720px;background:#eaf1fb;border-radius:24px;height:48px;display:flex;align-items:center;padding:0 18px}
.search input{border:0;background:transparent;outline:0;width:100%;font-size:16px}
.badge{padding:6px 12px;border-radius:16px;font-size:12px;font-weight:600;white-space:nowrap}
.live{background:#c4eed0;color:#0b6a2b}.fixture{background:#fce8b2;color:#7a4b00}
.avatar{width:36px;height:36px;border-radius:50%;color:#fff;display:flex;align-items:center;justify-content:center;font-weight:600;flex:none}
.shell{display:flex;min-height:calc(100vh - 64px)}
.side{width:256px;padding:8px 12px 8px 8px;flex:none}
.compose{display:block;width:100%;border:0;border-radius:16px;background:#c2e7ff;color:#001d35;font-size:14px;font-weight:600;padding:16px 20px;margin:8px 0 16px;cursor:pointer;box-shadow:0 1px 2px rgba(60,64,67,.3);text-align:left}
.compose:hover{box-shadow:0 2px 6px rgba(60,64,67,.4)}
.nav a{display:flex;justify-content:space-between;align-items:center;height:32px;padding:0 12px 0 26px;border-radius:0 16px 16px 0;margin-right:-4px;color:#202124}
.nav a:hover{background:#e8eaed}.nav a.on{background:#d3e3fd;font-weight:700}
.nav .n{font-size:12px;color:#5f6368}.nav a.on .n{color:#202124}
.nav hr{border:0;border-top:1px solid #dadce0;margin:10px 0}
.main{flex:1;background:#fff;border-radius:16px 0 0 0;margin-right:0;min-width:0;overflow:hidden}
.tabs{display:flex;border-bottom:1px solid #e0e0e0;padding:0 8px;overflow-x:auto}
.tabs a{padding:14px 18px;color:#5f6368;border-bottom:3px solid transparent;white-space:nowrap;font-weight:500}
.tabs a:hover{background:#f1f3f4}.tabs a.on{color:#0b57d0;border-bottom-color:#0b57d0}
.toolbar{display:flex;justify-content:space-between;align-items:center;padding:8px 16px;color:#5f6368;font-size:13px;border-bottom:1px solid #f1f3f4}
.row{display:flex;align-items:center;gap:12px;padding:0 16px;height:44px;border-bottom:1px solid #f1f3f4;background:#f2f6fc;cursor:pointer}
.row.unread{background:#fff;font-weight:700}.row:hover{box-shadow:inset 1px 0 0 #dadce0,inset -1px 0 0 #dadce0,0 1px 2px 0 rgba(60,64,67,.3);z-index:1;position:relative}
.row .who{width:210px;flex:none;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.row .subj{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0}
.row .snip{color:#5f6368;font-weight:400}.row .when{width:70px;text-align:right;flex:none;font-size:12px}
.dot{width:10px;height:10px;border-radius:50%;flex:none}
.chip{display:inline-block;padding:2px 8px;border-radius:8px;font-size:11px;font-weight:600;margin-right:6px;vertical-align:middle}
.c-deadline{background:#fad2cf;color:#a50e0e}.c-meeting{background:#e9d7fd;color:#5b2d9a}.c-student{background:#d3e3fd;color:#0842a0}
.c-admin{background:#fde293;color:#7a4b00}.c-routine{background:#e8eaed;color:#3c4043}.c-automated{background:#f1f3f4;color:#80868b}
.c-ok{background:#c4eed0;color:#0b6a2b}.c-warn{background:#fad2cf;color:#a50e0e}.c-info{background:#d3e3fd;color:#0842a0}
.empty{padding:60px;text-align:center;color:#5f6368}
.flash{margin:12px 16px;padding:12px 16px;border-radius:8px}.flash.ok{background:#c4eed0;color:#0b6a2b}.flash.err{background:#fad2cf;color:#a50e0e}
.thread{display:flex;gap:20px;padding:16px 24px;align-items:flex-start}
.convo{flex:1;min-width:0}.assist{width:380px;flex:none;display:flex;flex-direction:column;gap:14px}
h1.sub{font-size:22px;font-weight:400;margin:4px 0 12px}
.msg{border:1px solid #e0e0e0;border-radius:12px;padding:14px 16px;margin-bottom:12px}
.msg .hd{display:flex;gap:12px;align-items:center;margin-bottom:10px}.msg .hd small{color:#5f6368;display:block}
.msg pre{white-space:pre-wrap;word-wrap:break-word;font-family:inherit;margin:0;line-height:1.5}
.card{border:1px solid #dadce0;border-radius:12px;padding:14px 16px;background:#fff}
.card h3{margin:0 0 8px;font-size:14px;color:#3c4043}.card p{margin:6px 0;line-height:1.45}
textarea{width:100%;min-height:210px;border:1px solid #dadce0;border-radius:8px;padding:10px;font:inherit;line-height:1.45;resize:vertical}
button,.btn{font:inherit;border-radius:18px;border:1px solid #dadce0;background:#fff;color:#0b57d0;padding:8px 18px;cursor:pointer;font-weight:600}
button:hover{background:#f6f8fc}button.primary{background:#0b57d0;color:#fff;border-color:#0b57d0}button.primary:hover{background:#0a4cb8}
button.danger{color:#a50e0e}.actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}
input[type=date]{font:inherit;padding:7px;border:1px solid #dadce0;border-radius:8px}
.kv{display:flex;justify-content:space-between;padding:4px 0;color:#3c4043}.muted{color:#5f6368;font-size:12px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:14px;padding:20px}
.stat{border:1px solid #dadce0;border-radius:12px;padding:16px}.stat b{display:block;font-size:30px;font-weight:400;color:#0b57d0}
.bar{height:10px;border-radius:5px;background:#d3e3fd;margin-top:4px}
.sec{padding:14px 20px 4px;font-weight:700;color:#3c4043}
.brief{margin:12px 16px;padding:14px 18px;border-radius:16px;background:linear-gradient(90deg,#e8f0fe,#f3e8fd);display:flex;gap:10px;flex-wrap:wrap;align-items:center}
.brief b{margin-right:6px;color:#1f1f1f}.pill{background:#fff;border-radius:14px;padding:5px 12px;font-size:13px;box-shadow:0 1px 1px rgba(60,64,67,.2)}
.pill.hot{background:#fad2cf;color:#a50e0e;font-weight:600}.pill.go{background:#c4eed0;color:#0b6a2b;font-weight:600}
.tview{display:flex;gap:28px;padding:8px 24px 40px;align-items:flex-start}.tmain{flex:1;min-width:0;max-width:860px}.tside{width:330px;flex:none;display:flex;flex-direction:column;gap:12px}
.summary{background:#e8f0fe;border-radius:12px;padding:10px 14px;margin:6px 0 14px;color:#174ea6}
.gm{display:flex;gap:14px;padding:16px 0;border-bottom:1px solid #eceff1}.gm .av{margin-top:2px}.gm .bd{flex:1;min-width:0}
.gm .top2{display:flex;justify-content:space-between;gap:10px}.gm pre{white-space:pre-wrap;word-wrap:break-word;font-family:inherit;margin:8px 0 0;line-height:1.55;font-size:14px}
.replybox{margin-top:22px;border:1px solid #dadce0;border-radius:18px;box-shadow:0 1px 3px rgba(60,64,67,.25);overflow:hidden}
.replybox .rh{padding:10px 16px;background:#f6f8fc;color:#5f6368;font-size:13px;display:flex;justify-content:space-between}
.replybox textarea{border:0;border-radius:0;min-height:230px;padding:14px 16px;outline:0}
.replybox .rf{padding:10px 14px;display:flex;gap:10px;align-items:center;border-top:1px solid #f1f3f4;flex-wrap:wrap}
.mini{padding:12px 14px;border-radius:14px;border:1px solid #dadce0;background:#fff}.mini h3{margin:0 0 6px;font-size:13px;color:#5f6368;text-transform:uppercase;letter-spacing:.4px}
.mini .big{font-size:16px;font-weight:600;margin:2px 0}.ok{color:#0b6a2b}.bad{color:#a50e0e}
details.mini summary{cursor:pointer;color:#5f6368;font-weight:600}
@media(max-width:1000px){.side{display:none}.thread{flex-direction:column}.assist{width:100%}.brand{width:auto}}
"""

JS = """
const q=document.getElementById('q');
if(q){q.addEventListener('input',()=>{const t=q.value.toLowerCase();
document.querySelectorAll('[data-text]').forEach(r=>{r.style.display=r.dataset.text.includes(t)?'':'none'})})}
"""

CATEGORIES = ("all", "deadline", "meeting", "student", "admin", "routine", "automated")
COLORS = {"deadline": "#d93025", "meeting": "#8e24aa", "student": "#1a73e8", "admin": "#f9ab00",
          "routine": "#9aa0a6", "automated": "#dadce0"}
VIEWS = (("inbox", "Inbox"), ("needs", "Needs reply"), ("later", "Reply later"), ("actions", "Action items"),
         ("meetings", "Meeting requests"), ("calendar", "Calendar"), ("drafts", "Draft replies"),
         ("archived", "Archived"), ("insights", "Insights"), ("audit", "Audit trail"), ("settings", "Rules & replies"))


def _color(text: str) -> str:
    return ["#1a73e8", "#e8710a", "#188038", "#a142f4", "#d93025", "#12b5cb", "#e52592"][
        int(hashlib.md5(text.encode()).hexdigest(), 16) % 7]


def _name(sender: str) -> str:
    n, a = parseaddr(sender)
    return n or a or sender or "(unknown)"


def _when(ts: str) -> str:
    try:
        dt = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone()
    now = datetime.now().astimezone()
    return dt.strftime("%I:%M %p").lstrip("0") if dt.date() == now.date() else dt.strftime("%b %d").replace(" 0", " ")


def _full_when(ts: str) -> str:
    try:
        dt = datetime.fromisoformat(ts)
        return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone().strftime("%a, %b %d, %Y, %I:%M %p")
    except (TypeError, ValueError):
        return ""


def _chip(cls: str | None) -> str:
    cls = cls or "routine"
    return f'<span class="chip c-{esc(cls)}">{esc(cls.title())}</span>'


def _href(view: str, thread: str = "", **extra) -> str:
    parts = [f"view={quote(view)}"] + ([f"thread={quote(thread)}"] if thread else [])
    parts += [f"{k}={quote(str(v))}" for k, v in extra.items() if v]
    return "/?" + "&".join(parts)


def render_app(dash, *, view: str = "inbox", thread_id: str = "", csrf: str = "", msg: str = "",
               err: str = "", category: str = "all", sort: str = "priority") -> str:
    workers = list(dash.snapshot().triaged_inbox)
    infos = {w.thread_id: dash.info(w.thread_id) for w in workers}
    live = "LIVE" in dash.mode_label
    counts = {
        "inbox": len(workers),
        "needs": sum(1 for w in workers if w.followup_required),
        "later": len(dash._store.reply_later()),
        "actions": sum(1 for i in dash.action_items() if not i["done"]),
        "meetings": sum(1 for w in workers if w.classification == "meeting"),
        "drafts": sum(1 for w in workers if w.draft_reply),
    }
    nav = "".join(
        f'<a class="{"on" if view == key and not thread_id else ""}" href="{_href(key)}"><span>{esc(label)}</span>'
        f'<span class="n">{counts.get(key, "") if counts.get(key) else ""}</span></a>'
        + ('<hr>' if key == "archived" else "")
        for key, label in VIEWS if key != "archived" or dash.can_archive)
    flash = (f'<div class="flash ok">{esc(msg)}</div>' if msg else "") + (f'<div class="flash err">{esc(err)}</div>' if err else "")
    if thread_id:
        body = _thread_page(dash, thread_id, csrf, view)
    elif view == "insights":
        body = _insights(dash, workers)
    elif view == "audit":
        body = _audit(dash)
    elif view == "calendar":
        body = _calendar_view(dash, csrf)
    elif view == "archived":
        body = _archived(dash, csrf)
    elif view == "settings":
        body = _settings(dash)
    elif view == "later":
        body = _followups(dash, infos)
    elif view == "actions":
        body = _actions(dash, csrf)
    elif view in ("needs", "meetings", "drafts"):
        body = _filtered(dash, workers, infos, view, csrf)
    else:
        body = _inbox(dash, workers, infos, category, sort)
    initial = esc((dash.faculty_email[:1] or "?").upper())
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Email Organizer - {esc(dash.faculty_email)}</title><style>{CSS}</style></head><body>
<div class="top"><div class="brand"><svg viewBox="0 0 24 24"><path fill="#ea4335" d="M2 6l10 7 10-7v-1a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2z"/><path fill="#c5221f" d="M2 7v11a2 2 0 0 0 2 2h2V10zM22 7v11a2 2 0 0 1-2 2h-2V10z"/><path fill="#fbbc04" d="M6 20h12V10l-6 4.5L6 10z"/></svg>Email Organizer</div>
<div class="search"><input id="q" placeholder="Search mail on this page" autocomplete="off"></div>
<span class="badge {'live' if live else 'fixture'}">{esc(dash.mode_label)}</span>
<div class="avatar" style="background:{_color(dash.faculty_email)}" title="{esc(dash.faculty_email)}">{initial}</div></div>
<div class="shell"><div class="side">
<form method="post"><input type="hidden" name="csrf" value="{esc(csrf)}"><input type="hidden" name="view" value="{esc(view)}">
<button class="compose" name="action" value="refresh">&#8635;&nbsp; Refresh inbox</button></form>
<div class="nav">{nav}<hr><a href="/classic"><span>Classic view</span></a></div>
<p class="muted" style="padding:12px 16px">This app never sends email. Replies are saved as Gmail drafts only after you approve.</p></div>
<div class="main">{flash}{body}</div></div><script>{JS}</script></body></html>"""


# ------------------------------------------------------------------ list views
def _rows(dash, ws, infos, back_view) -> str:
    out = []
    for w in ws:
        info = infos.get(w.thread_id)
        sender = info.sender if info else w.thread_id
        subject = info.subject if info else ""
        unread = bool(info and "UNREAD" in info.labels)
        snippet = (info.snippet if info else "") or ""
        state = dash.decision_state(w.thread_id)
        saved = dash.saved_draft_id(w.thread_id)
        found = dash.analysis(w.thread_id)
        flags = ""
        if found and any(sg != "deadline mention" for sg in found.signals):
            flags += '<span class="chip c-warn">Urgent</span>'
        if found and found.uncertain:
            flags += '<span class="chip c-info">Check urgency</span>'
        tag = flags + ('<span class="chip c-ok">Draft saved</span>' if saved else
               f'<span class="chip c-info">{esc(state)}</span>' if state not in ("none",) else "")
        out.append(
            f'<a class="row {"unread" if unread else ""}" href="{_href(back_view, w.thread_id)}" '
            f'data-text="{esc((sender + " " + subject + " " + snippet).lower())}">'
            f'<span class="dot" style="background:{COLORS.get(w.classification or "routine", "#9aa0a6")}"></span>'
            f'<span class="who">{esc(_name(sender))}</span>'
            f'<span class="subj">{_chip(w.classification)}{tag}{esc(subject)}<span class="snip"> &ndash; {esc(snippet[:90])}</span></span>'
            f'<span class="when">{esc(_when(info.timestamp if info else ""))}</span></a>')
    return "".join(out) or '<div class="empty">Nothing here.</div>'


def _inbox(dash, workers, infos, category, sort) -> str:
    cat = category if category in CATEGORIES else "all"
    ws = [w for w in workers if cat == "all" or w.classification == cat]
    ws.sort(key=(lambda w: infos[w.thread_id].timestamp if infos.get(w.thread_id) else ""), reverse=True)
    if sort != "date":
        ws.sort(key=lambda w: -(w.priority or 0))
    tabs = "".join(
        f'<a class="{"on" if cat == c else ""}" href="{_href("inbox", category=c if c != "all" else "", sort=sort if sort != "priority" else "")}">'
        f'{esc(c.title())}{" (" + str(sum(1 for w in workers if w.classification == c)) + ")" if c != "all" else ""}</a>'
        for c in CATEGORIES)
    other = "date" if sort != "date" else "priority"
    toolbar = (f'<div class="toolbar"><span>Ranked by {"newest first" if sort == "date" else "urgency (priority rules)"}</span>'
               f'<a href="{_href("inbox", category=cat if cat != "all" else "", sort=other)}" style="color:#0b57d0">Sort by {other}</a></div>')
    return f'{_briefing(dash)}<div class="tabs">{tabs}</div>{toolbar}{_rows(dash, ws, infos, "inbox")}'


def _briefing(dash) -> str:
    b = dash.briefing()
    pills = []
    def add(n, text, cls=""):
        if n:
            pills.append(f'<span class="pill {cls}">{n} {esc(text)}</span>')
    add(b["urgent"], "urgent", "hot")
    add(b["needs_reply"], "waiting for your reply")
    add(b["ready"], "replies ready to approve", "go")
    add(b["meetings_waiting"], "meeting requests to confirm")
    add(b["due"], "reminders due today", "hot")
    add(b["overdue"], "overdue 48h+", "hot")
    nm = b["next_meeting"]
    if nm:
        pills.append(f'<span class="pill go">Next meeting: {esc(nm["start"].strftime("%a %d %b, %I:%M %p").replace(" 0", " "))}</span>')
    inner = "".join(pills) or '<span class="pill">You are all caught up.</span>'
    return f'<div class="brief"><b>&#10024; Today\'s briefing</b>{inner}</div>'


def _filtered(dash, workers, infos, view, csrf='') -> str:
    pick = {"needs": lambda w: w.followup_required, "actions": lambda w: bool(w.action_item),
            "meetings": lambda w: w.classification == "meeting", "drafts": lambda w: bool(w.draft_reply)}[view]
    ws = sorted((w for w in workers if pick(w)), key=lambda w: -(w.priority or 0))
    titles = {"needs": "Waiting for your reply", "actions": "Action items extracted from mail",
              "meetings": "Meeting requests (nothing is confirmed automatically)", "drafts": "Draft-reply queue"}
    extra = ""
    if view == "actions":
        extra = "".join(f'<div class="sec">{esc(_name(infos[w.thread_id].sender if infos.get(w.thread_id) else w.thread_id))}</div>'
                        f'<div style="padding:0 20px 8px">{esc(w.action_item or "")}</div>' for w in ws)
        return f'<div class="toolbar"><b>{titles[view]}</b></div>{extra or _rows(dash, ws, infos, view)}'
    bulk = ""
    if view == "drafts" and dash.can_save_drafts and dash.ready_reply_count():
        bulk = _form(csrf, view, "", f'<button class="primary" name="action" value="approve_all">Approve &amp; save all {dash.ready_reply_count()} ready replies to Gmail drafts</button>')
    return f'<div class="toolbar"><b>{titles[view]}</b><span>{bulk}</span></div>{_rows(dash, ws, infos, view)}'


def _followups(dash, infos) -> str:
    d = dash.followup_digest()
    def block(title, items, fmt):
        if not items:
            return ""
        lis = "".join(
            f'<a class="row" href="{_href("later", t)}"><span class="who">{esc(_name(infos[t].sender if infos.get(t) else t))}</span>'
            f'<span class="subj">{esc(infos[t].subject if infos.get(t) else "")}</span><span class="when" style="width:130px">{esc(fmt(t, extra))}</span></a>'
            for t, extra in items)
        return f'<div class="sec">{esc(title)}</div>{lis}'
    html = (block("Due now (reply-later date reached)", d["due"], lambda t, e: f"due {e}")
            + block("Overdue: waiting 48h or more", [(t, "") for t in d["overdue"]], lambda t, e: "overdue")
            + block("Upcoming reply-later", d["upcoming"], lambda t, e: f"on {e}")
            + block("Waiting for your reply", [(t, "") for t in d["awaiting"] if t not in d["overdue"]], lambda t, e: ""))
    return f'<div class="toolbar"><b>Follow-up digest</b></div>{html or "<div class=empty>No follow-ups. Use Reply later inside a thread to schedule one.</div>"}'


# ----------------------------------------------------------------- thread page
def _form(csrf, view, thread_id, inner) -> str:
    return (f'<form method="post"><input type="hidden" name="csrf" value="{esc(csrf)}">'
            f'<input type="hidden" name="view" value="{esc(view)}"><input type="hidden" name="thread_id" value="{esc(thread_id)}">{inner}</form>')


def _thread_page(dash, thread_id, csrf, back) -> str:
    info, w, g = dash.info(thread_id), dash.worker(thread_id), dash.grounding(thread_id)
    if info is None or w is None:
        return '<div class="empty">Unknown thread.</div>'
    msgs = dash.messages_for(thread_id)
    cards = "".join(
        f'<div class="gm"><div class="avatar av" style="background:{_color(m.sender)}">{esc(_name(m.sender)[:1].upper())}</div>'
        f'<div class="bd"><div class="top2"><span><b>{esc(_name(m.sender))}</b> <span class="muted">&lt;{esc(parseaddr(m.sender)[1])}&gt;</span></span>'
        f'<span class="muted">{esc(_full_when(m.timestamp))}</span></div><div class="muted">to {esc(", ".join(m.recipients) or "me")}</div>'
        f'<pre>{esc(m.body or "(no readable text in this message)")}</pre></div></div>' for m in msgs
    ) or f'<div class="gm"><div class="bd"><pre>{esc(info.subject)}\n\n(Sample thread: no message body.)</pre></div></div>'
    saved = dash.saved_draft_id(thread_id)
    found = dash.analysis(thread_id)
    grounded = bool(g and g.status == "grounded")

    # ---- reply box (Gmail-composer look)
    reply = ""
    if w.draft_reply:
        if saved:
            footer = ('<span class="chip c-ok">Saved in Gmail Drafts</span>'
                      '<a class="muted" style="color:#0b57d0" target="_blank" href="https://mail.google.com/mail/u/0/#drafts">Open it in Gmail to send</a>'
                      + _form(csrf, back, thread_id, '<button class="danger" name="action" value="undo">Undo</button>'))
            area = f'<textarea readonly>{esc(dash.draft_text(thread_id))}</textarea>'
        else:
            meeting_ok = (dash.meeting_plan(thread_id) or {}).get("status") == "free" and dash.has_calendar
            label = "Approve reply &amp; schedule meeting" if meeting_ok else "Approve &amp; save to Gmail drafts"
            if dash.can_save_drafts:
                footer = (f'<button class="primary" name="action" value="approve_save">{label}</button>'
                          '<button class="danger" name="action" value="discard">Discard</button>'
                          '<span class="muted">Nothing is sent; you send it from Gmail.</span>')
            else:
                footer = '<span class="muted">Draft saving is off. Start with <code>--drafts</code>.</span>'
            area = f'<textarea name="text">{esc(dash.draft_text(thread_id))}</textarea>'
        flag = "" if grounded else ' <span class="chip c-warn">not grounded</span>'
        reply = ('<div class="replybox">' + _form(csrf, back, thread_id,
                 f'<div class="rh"><span>&#10024; Reply drafted by your agent{flag}</span><span>To: {esc(_name(info.sender))}</span></div>{area}<div class="rf">{footer}</div>') + "</div>")

    # ---- side cards
    side = []
    if w.classification == "meeting":
        side.append(_calendar_card(dash, thread_id))
    chips = _chip(w.classification) + f'<span class="chip c-info">Priority {w.priority}</span>'
    if found and any(x != "deadline mention" for x in found.signals):
        chips += '<span class="chip c-warn">Urgent</span>'
    if found and found.uncertain:
        chips += '<span class="chip c-info">Check urgency</span>'
    side.append(f'<div class="mini"><h3>Agent triage</h3><p style="margin:6px 0">{chips}</p>'
                f'<div class="kv"><span>Sender</span><span>{esc((found.sender_category if found else "other").title())}</span></div>'
                f'<div class="kv"><span>Needs your reply</span><span>{"Yes" if w.followup_required else "No"}</span></div>'
                + ('<p class="muted">A date is mentioned but there is no urgency wording; please judge this one.</p>' if found and found.uncertain else "") + '</div>')
    if found and found.action_items:
        side.append('<div class="mini"><h3>Action items</h3>' + "".join(f"<p style='margin:5px 0'>&bull; {esc(t)}</p>" for t in found.action_items)
                    + f'<a class="muted" style="color:#0b57d0" href="{_href("actions")}">Mark done in Action items</a></div>')
    later = dash._store.reply_later().get(thread_id)
    side.append('<div class="mini"><h3>Remind me</h3>' + _form(csrf, back, thread_id,
        f'<input type="date" name="due" value="{esc(later["due"] if later else date.today().isoformat())}" required> '
        '<button name="action" value="later_set">Set</button>'
        + ('<button name="action" value="later_clear" class="danger">Clear</button>' if later else "")) + "</div>")
    sources = "".join(f'<li><b>{esc(src.source_type.replace("_", " "))}</b>: {esc(src.excerpt[:100])}</li>'
                      for src in (g.evidence.sources if g and g.evidence else ()))
    claims = "".join(f"<li>{esc(c)}</li>" for c in (g.evidence.supported_claims if g and g.evidence else ()))
    why = ", ".join(found.signals) if found and found.signals else "none"
    side.append(f'<details class="mini"><summary>Why the agent decided this</summary><p class="muted">Urgency signals: {esc(why)}</p>'
                f'<p>{'<span class="chip c-ok">Grounded in this thread</span>' if grounded else '<span class="chip c-warn">Not grounded</span>'}</p>'
                f'<p class="muted">Facts quoted from the email:</p><ul style="margin:2px 0 8px 18px">{claims}</ul>'
                f'<p class="muted">Evidence (this thread only):</p><ul style="margin:2px 0 0 18px">{sources}</ul></details>')
    if dash.can_archive:
        side.append('<div class="mini"><h3>Resolved?</h3>' + _form(csrf, back, thread_id,
            '<label class="muted"><input type="checkbox" name="confirm" value="yes"> confirm archive (reversible)</label> '
            '<button name="action" value="archive">Archive</button>') + "</div>")

    return (f'<div class="toolbar"><a href="{_href(back)}" style="color:#0b57d0">&larr; Back to {esc(back)}</a><span></span></div>'
            f'<div class="tview"><div class="tmain"><h1 class="sub">{esc(info.subject or "(no subject)")}</h1>'
            f'<div class="summary">&#10024; {esc(dash.summary(thread_id))}</div>{cards}{reply}</div>'
            f'<div class="tside">{"".join(side)}</div></div>')


def _calendar_card(dash, thread_id) -> str:
    plan = dash.meeting_plan(thread_id) or {}
    event = dash._store.event_for(thread_id)
    prop, status = plan.get("proposal"), plan.get("status")
    when = esc(dash._fmt_slot(prop.start, prop.end)) if prop else ""
    reminder = "Approving the reply adds it to your calendar with reminders: email 1 day before, popup 30 minutes before. Nobody else is invited."
    if event:
        link = (f' <a style="color:#0b57d0" target="_blank" href="{esc(event["link"])}">open</a>') if event.get("link") else ""
        body = f'<div class="big ok">&#10003; On your calendar</div><div>{when}{link}</div><p class="muted">Reminders set: 1 day before (email), 30 min before (popup).</p>'
    elif status == "free":
        body = f'<div class="big ok">{when}</div><div class="ok">&#10003; You are free then</div><p class="muted">{reminder}</p>'
    elif status == "past":
        body = f'<div class="big">{when}</div><div class="bad">That time has already passed</div><p class="muted">No event is created. Reply to agree a new time.</p>'
    elif status == "busy":
        body = (f'<div class="big">{when}</div><div class="bad">&#10007; You have a conflict</div>'
                '<p class="muted">The agent wrote other times into the reply from your calendar. No event is created.</p>')
    elif status == "no_time":
        body = '<div class="big">No exact time stated</div>' + ('<p class="muted">The agent added times you are free into the reply.</p>' if plan.get("slots") else
                '<p class="muted">Reply to agree a time.</p>')
    elif status == "calendar_off":
        body = f'<div class="big">{when}</div><p class="muted">Calendar is off. Start with <code>--calendar</code> and the agent will check it and schedule it for you.</p>'
    else:
        body = f'<p class="bad">Could not read your calendar: {esc(plan.get("detail", ""))}</p>'
    return f'<div class="mini"><h3>Meeting</h3>{body}</div>'


# ------------------------------------------------------------ insights / audit
def _insights(dash, workers) -> str:
    m = dash.metrics()
    def stat(label, value):
        return f'<div class="stat"><span class="muted">{esc(label)}</span><b>{esc(str(value))}</b></div>'
    hours = lambda v: "n/a" if v is None else f"{v} h"
    dist = {c: sum(1 for w in workers if w.classification == c) for c in CATEGORIES[1:]}
    top = max(dist.values() or [1]) or 1
    bars = "".join(f'<div style="margin:8px 0"><div class="kv"><span>{_chip(c)}</span><span>{n}</span></div>'
                   f'<div class="bar" style="width:{int(100 * n / top)}%;background:{COLORS[c]}"></div></div>' for c, n in dist.items())
    return (f'<div class="toolbar"><b>Insights</b><span class="muted">Computed from real message timestamps</span></div>'
            f'<div class="grid">{stat("Threads", len(workers))}{stat("Awaiting your reply", m.awaiting_reply)}{stat("Overdue (48h+)", m.overdue)}'
            f'{stat("Average response time", hours(m.avg_response_hours))}{stat("Median response time", hours(m.median_response_hours))}'
            f'{stat("Replies measured", m.replied_pairs)}{stat("Drafts saved", dash._store.saved_draft_count())}</div>'
            f'<div class="sec">By category</div><div style="padding:0 20px 20px;max-width:520px">{bars}</div>')


def _audit(dash) -> str:
    def box(inner):
        return f'<div class="row" style="cursor:default;height:auto;padding:10px 16px;background:#fff;font-weight:400">{inner}</div>'
    approvals = "".join(box(f'<b>{esc(a["decision"].title())}</b> &nbsp;{esc(a["subject"] or a["thread_id"])} '
                            f'<span class="muted">by {esc(a["by"])}{" &middot; published" if a["published"] else ""}{" &middot; " + esc(a["note"]) if a["note"] else ""}</span>')
                        for a in reversed(dash.approval_history()))
    replies = "".join(box(f'{esc((r.get("saved_at") or "")[:16].replace("T", " "))} UTC &nbsp;<b>{esc(r.get("subject") or r["thread_id"])}</b> '
                          f'<span class="muted">to {esc(r.get("to", ""))} &middot; Gmail draft {esc(r["draft_id"])} (saved, not sent)</span>')
                      for r in dash.reply_log())
    trail = "".join(box(esc(l)) for l in reversed(dash.audit_lines()))
    empty = "<div class=empty>Nothing yet.</div>"
    return ('<div class="toolbar"><b>Audit trail</b><span class="muted">Approval history, reply log and every governed action</span></div>'
            f'<div class="sec">Approval history</div>{approvals or empty}<div class="sec">Reply log</div>{replies or empty}'
            f'<div class="sec">Full audit trail</div>{trail or empty}')


def _actions(dash, csrf) -> str:
    items = dash.action_items()
    rows = "".join(
        f'<div class="row" style="cursor:default;height:auto;padding:10px 16px;background:#fff;font-weight:400;gap:16px">'
        f'<div style="flex:1;{"text-decoration:line-through;color:#80868b" if i["done"] else ""}">{esc(i["text"])}<br>'
        f'<a class="muted" style="color:#0b57d0" href="{_href("actions", i["thread_id"])}">open thread</a></div>'
        + _form(csrf, "actions", "", f'<input type="hidden" name="key" value="{esc(i["key"])}">'
                + ('<button name="action" value="reopen">Reopen</button>' if i["done"] else
                   '<label class="muted"><input type="checkbox" name="confirm" value="yes"> confirm done</label> '
                   '<button name="action" value="done">Mark done</button>')) + "</div>"
        for i in items)
    return ('<div class="toolbar"><b>Action items</b><span class="muted">Requests found in mail and your own commitments. '
            'Nothing is marked complete without your confirmation.</span></div>' + (rows or '<div class="empty">No action items.</div>'))


def _archived(dash, csrf) -> str:
    rows = "".join(
        f'<div class="row" style="cursor:default;background:#fff;font-weight:400"><span class="who">{esc(_name(v["sender"]))}</span>'
        f'<span class="subj">{esc(v["subject"])}</span>' + _form(csrf, "archived", t, '<button name="action" value="unarchive">Move back to inbox</button>') + "</div>"
        for t, v in dash.archived_threads().items())
    return '<div class="toolbar"><b>Archived by this app</b></div>' + (rows or '<div class="empty">Nothing archived.</div>')


def _calendar_view(dash, csrf) -> str:
    data = dash.calendar_overview()
    if not data.get("enabled"):
        return ('<div class="toolbar"><b>Calendar</b></div><div class="empty"><h2 style="font-weight:400">Calendar is not connected</h2>'
                '<p>1. Enable the Google Calendar API in your Cloud project.<br>2. Run <code>python src\\gmail_cli.py verify --drafts --calendar</code><br>'
                '3. Start the dashboard with <code>--calendar</code>.</p></div>')
    if data.get("error"):
        return f'<div class="toolbar"><b>Calendar</b></div><div class="flash err">Could not read your calendar: {esc(data["error"])}</div>'
    from datetime import timedelta
    today = datetime.now(data["busy"][0][0].tzinfo).date() if data["busy"] else date.today()
    cols = ""
    for offset in range(7):
        day = today + timedelta(days=offset)
        blocks = [b for b in data["busy"] if b[0].date() == day]
        inner = "".join(f'<div class="chip c-meeting" style="display:block;margin:4px 0">{b[0].strftime("%I:%M %p").lstrip("0")} &ndash; {b[1].strftime("%I:%M %p").lstrip("0")} busy</div>' for b in blocks) \
            or '<span class="muted">No busy time</span>'
        cols += f'<div class="card"><h3>{day.strftime("%a %d %b")}</h3>{inner}</div>'
    up = "".join(f'<a class="row" href="{_href("calendar", m["thread_id"])}"><span class="who">{esc(m["start"].strftime("%a %d %b, %I:%M %p").replace(" 0", " "))}</span>'
                 f'<span class="subj">{esc(m["summary"])}</span><span class="chip c-ok">Reminders on</span></a>' for m in dash.upcoming_meetings())
    reqs = ""
    for m in dash.meeting_threads():
        plan = dash.meeting_plan(m["thread_id"]) or {}
        label = {"free": ("You are free", "c-ok"), "busy": ("Conflict", "c-warn"), "no_time": ("No exact time", "c-info")}.get(plan.get("status"), ("", "c-info"))
        status = ('<span class="chip c-ok">On your calendar</span>' if m["event"] else f'<span class="chip {label[1]}">{esc(label[0])}</span>')
        reqs += (f'<a class="row" href="{_href("calendar", m["thread_id"])}"><span class="who">{esc(_name(m["sender"]))}</span>'
                 f'<span class="subj">{esc(m["subject"])}</span><span>{status}</span></a>')
    return (f'<div class="toolbar"><b>Calendar</b><span class="muted">Next 7 days, {esc(data["timezone"])}. Free/busy only: event titles are never read.</span></div>'
            f'<div class="grid">{cols}</div><div class="sec">Meetings the agent scheduled for you</div>'
            f'{up or "<div class=empty style=padding:20px>None yet. They appear when you approve a reply to a meeting request with a stated time.</div>"}'
            f'<div class="sec">Meeting requests in your inbox</div>{reqs or "<div class=empty>No meeting requests.</div>"}')


def _settings(dash) -> str:
    from response_library import load_library, USER_LIBRARY_PATH
    from rules import USER_RULES_PATH
    r = dash.rules
    lst = lambda v: esc(", ".join(v) or "none set")
    lib = "".join(f'<div class="card"><h3>{esc(e.title)}</h3><p class="muted">keywords: {esc(", ".join(e.keywords))}</p><p>{esc(e.body)}</p></div>' for e in load_library())
    return ('<div class="toolbar"><b>Rules &amp; standard replies</b></div>'
            f'<div class="thread"><div class="convo"><div class="card"><h3>Your rules</h3>'
            f'<div class="kv"><span>Priority phrases</span><span>{lst(r.priority_phrases)}</span></div>'
            f'<div class="kv"><span>Student domains</span><span>{lst(r.student_domains)}</span></div>'
            f'<div class="kv"><span>Admin domains</span><span>{lst(r.admin_domains)}</span></div>'
            f'<div class="kv"><span>Collaborator domains</span><span>{lst(r.collaborator_domains)}</span></div>'
            f'<div class="kv"><span>Vendor domains</span><span>{lst(r.vendor_domains)}</span></div>'
            f'<div class="kv"><span>Working hours (calendar slots)</span><span>{r.work_start_hour}:00 &ndash; {r.work_end_hour}:00, {r.slot_minutes} min</span></div>'
            f'<p class="muted">Edit <code>{esc(str(USER_RULES_PATH))}</code> and restart. Changes to replies: <code>{esc(str(USER_LIBRARY_PATH))}</code>.</p></div></div>'
            f'<div class="assist" style="width:520px">{lib}</div></div>')
