/* WhatsMerge viewer. Everything runs from the JSON payload embedded above;
   the page makes no network requests. */
(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };
  var DATA = JSON.parse($("payload").textContent);

  // Short codes keep the embedded payload small on big archives.
  var KIND = { t: "text", y: "system", m: "media", d: "deleted", c: "call", l: "location", p: "poll", v: "contact" };

  // WhatsApp tints each participant's name in a group; these are its hues.
  var NAME_COLORS = ["#e542a3", "#02a698", "#c4532d", "#6e57c4", "#0a7ea4", "#b4681e",
                     "#1f8a4c", "#a3357f", "#2b6cb0", "#8d6a1f"];

  var VISUAL = { image: 1, video: 1, gif: 1, sticker: 1 };

  var state = {
    chatId: null,
    from: 0,
    filter: "all",
    lightbox: { items: [], index: 0 },
    audio: null,      // the <audio> currently playing
    rate: 1
  };

  /* ------------------------------------------------------------- helpers */

  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function hashCode(s) {
    var h = 0;
    for (var i = 0; i < s.length; i++) { h = (h * 31 + s.charCodeAt(i)) | 0; }
    return Math.abs(h);
  }

  // WhatsApp shows a plain silhouette for anyone without a profile photo, and
  // exports never carry photos — so every avatar is the default one.
  function avatar(isGroup, extraClass) {
    return '<span class="avatar ' + (extraClass || "") + '">' +
           '<svg viewBox="0 0 212 212"><use href="#' + (isGroup ? "i-group" : "i-person") + '"/></svg>' +
           "</span>";
  }

  function nameColor(name) { return NAME_COLORS[hashCode(String(name)) % NAME_COLORS.length]; }

  // Timestamps are emitted as UTC epochs so the rendered clock always matches
  // the exported transcript, whatever timezone the reader is in.
  function dateOf(sec) { return new Date(sec * 1000); }
  function pad(n) { return n < 10 ? "0" + n : String(n); }
  function hhmm(d) { return pad(d.getUTCHours()) + ":" + pad(d.getUTCMinutes()); }
  function ddmmyyyy(d) { return pad(d.getUTCDate()) + "/" + pad(d.getUTCMonth() + 1) + "/" + d.getUTCFullYear(); }
  function dayKey(d) { return d.getUTCFullYear() * 10000 + (d.getUTCMonth() + 1) * 100 + d.getUTCDate(); }
  function fullStamp(sec) {
    var d = dateOf(sec);
    return ddmmyyyy(d) + " " + hhmm(d) + ":" + pad(d.getUTCSeconds());
  }

  var WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

  function daySeparatorLabel(d) {
    var now = new Date();
    var todayKey = now.getFullYear() * 10000 + (now.getMonth() + 1) * 100 + now.getDate();
    var k = dayKey(d);
    if (k === todayKey) return "Today";
    var y = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1);
    if (k === y.getFullYear() * 10000 + (y.getMonth() + 1) * 100 + y.getDate()) return "Yesterday";
    var ageDays = (Date.now() - d.getTime()) / 86400000;
    if (ageDays < 7 && ageDays >= 0) return WEEKDAYS[d.getUTCDay()];
    return ddmmyyyy(d);
  }

  function listStamp(sec) {
    var d = dateOf(sec);
    var label = daySeparatorLabel(d);
    return label === "Today" ? hhmm(d) : label;
  }

  function humanBytes(n) {
    if (!n) return "";
    var units = ["B", "KB", "MB", "GB"], i = 0;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    return (i === 0 ? n.toFixed(0) : n.toFixed(1)) + " " + units[i];
  }

  function clock(sec) {
    if (!isFinite(sec) || sec < 0) return "--:--";
    var m = Math.floor(sec / 60), s = Math.floor(sec % 60);
    return m + ":" + pad(s);
  }

  function fold(s) {
    return String(s).toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
  }

  /* --------------------------------------------------------- body format */

  var URL_RE = /((?:https?:\/\/|www\.)[^\s<>"']+[^\s<>"'.,;:!?)\]}])/g;

  // Unicode property escapes are built at runtime: an engine without them
  // would otherwise throw a SyntaxError and take the whole page down.
  var EMOJI_ONLY = null;
  try { EMOJI_ONLY = new RegExp("^[\\p{Extended_Pictographic}\\p{Emoji_Component}\\s\\u200d\\ufe0f\\u20e3]+$", "u"); }
  catch (e) { EMOJI_ONLY = null; }

  function linkify(escaped) {
    return escaped.replace(URL_RE, function (m) {
      var href = m.indexOf("www.") === 0 ? "https://" + m : m;
      return '<a href="' + href + '" target="_blank" rel="noopener noreferrer nofollow">' + m + "</a>";
    });
  }

  var INLINE = [
    [/(^|[\s(\[>])\*(\S(?:[^*\n]*\S)?)\*(?=$|[\s.,!?;:)\]<])/g, "$1<b>$2</b>"],
    [/(^|[\s(\[>])_(\S(?:[^_\n]*\S)?)_(?=$|[\s.,!?;:)\]<])/g, "$1<i>$2</i>"],
    [/(^|[\s(\[>])~(\S(?:[^~\n]*\S)?)~(?=$|[\s.,!?;:)\]<])/g, "$1<s>$2</s>"]
  ];

  function inlineMarkup(html) {
    // Anchors are left untouched so a URL's underscores are not italicised.
    return html.split(/(<a\b[^>]*>[\s\S]*?<\/a>)/g).map(function (part, i) {
      if (i % 2) return part;
      INLINE.forEach(function (rule) { part = part.replace(rule[0], rule[1]); });
      return part;
    }).join("");
  }

  function formatBody(raw) {
    if (!raw) return "";
    var stash = [];
    var text = raw.replace(/```([\s\S]*?)```|`([^`\n]+)`/g, function (m, block, span) {
      stash.push(block !== undefined
        ? "<pre>" + esc(block.replace(/^\n/, "")) + "</pre>"
        : "<code>" + esc(span) + "</code>");
      return "\u0000" + (stash.length - 1) + "\u0000";
    });
    text = inlineMarkup(linkify(esc(text)));
    return text.replace(/\u0000(\d+)\u0000/g, function (m, i) { return stash[+i]; });
  }

  function isJumbo(text) {
    if (!text || text.length > 24 || !EMOJI_ONLY) return false;
    return EMOJI_ONLY.test(text.trim());
  }

  /* ---------------------------------------------------------- icon parts */

  function ticksSvg(read) {
    return '<svg class="ticks' + (read ? " is-read" : "") + '" viewBox="0 0 16 11" aria-hidden="true">' +
           '<path d="M1 6.2 3.7 9 10 1.8"/><path d="M5.6 6.2 8.3 9 14.6 1.8"/></svg>';
  }
  function tickSingle() {
    return '<svg class="ticks" viewBox="0 0 16 11" aria-hidden="true"><path d="M3.4 6.2 6.1 9 12.4 1.8"/></svg>';
  }
  function ticksFor(outgoing) {
    if (!outgoing) return "";
    return DATA.ticks === "read" ? ticksSvg(true)
         : DATA.ticks === "delivered" ? ticksSvg(false)
         : DATA.ticks === "sent" ? tickSingle() : "";
  }

  var GLYPH = {
    image: '<svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="8.5" cy="10" r="1.5"/><path d="M5 17l4.5-4.5 3 3L16 12l3 3"/></svg>',
    video: '<svg viewBox="0 0 24 24"><rect x="3" y="6" width="12" height="12" rx="2"/><path d="M15 10l6-3v10l-6-3z"/></svg>',
    audio: '<svg viewBox="0 0 24 24"><path d="M9 18V6l10-2v12"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="16.5" cy="16" r="2.5"/></svg>',
    voice: '<svg viewBox="0 0 24 24"><path d="M12 15a3 3 0 0 0 3-3V6a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3z"/><path d="M6 11v1a6 6 0 0 0 12 0v-1"/></svg>',
    sticker: '<svg viewBox="0 0 24 24"><path d="M14 3H6a3 3 0 0 0-3 3v12a3 3 0 0 0 3 3h6l9-9V6a3 3 0 0 0-3-3z"/><path d="M12 21v-6a3 3 0 0 1 3-3h6"/></svg>',
    gif: '<svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M11 10H9v4h2v-2"/><path d="M13 14v-4M16 14v-4h3"/></svg>',
    document: '<svg viewBox="0 0 24 24"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/></svg>',
    contact: '<svg viewBox="0 0 24 24"><circle cx="12" cy="9" r="3.2"/><path d="M5.5 20a6.5 6.5 0 0 1 13 0"/></svg>',
    missing: '<svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M4 18 20 6"/></svg>',
    deleted: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M5.6 5.6l12.8 12.8"/></svg>',
    call: '<svg viewBox="0 0 24 24"><path d="M6 3l3 5-2 2a12 12 0 0 0 5 5l2-2 5 3-2 4c-8 0-14-6-14-14z"/></svg>',
    location: '<svg viewBox="0 0 24 24"><path d="M12 21s7-6.4 7-11a7 7 0 1 0-14 0c0 4.6 7 11 7 11z"/><circle cx="12" cy="10" r="2.5"/></svg>',
    poll: '<svg viewBox="0 0 24 24"><path d="M5 20V9M12 20V4M19 20v-7"/></svg>',
    download: '<svg viewBox="0 0 24 24"><path d="M12 4v11M7 11l5 5 5-5M5 20h14"/></svg>',
    play: '<svg viewBox="0 0 24 24"><path d="M7 4.5v15l13-7.5z"/></svg>',
    pause: '<svg viewBox="0 0 24 24"><path d="M7 4h4v16H7zM13 4h4v16h-4z"/></svg>',
    mic: '<svg viewBox="0 0 24 24"><path d="M12 14a3 3 0 0 0 3-3V6a3 3 0 0 0-6 0v5a3 3 0 0 0 3 3z"/><path d="M6.5 11a5.5 5.5 0 0 0 11 0M12 17v3"/></svg>'
  };

  /* ------------------------------------------------------- chat indexing */

  DATA.chats.forEach(function (c, i) {
    c._i = i;
    c._media = [];
    c.msgs.forEach(function (m) {
      if (m.a) { m._mi = c._media.length; c._media.push(m); }
    });
    c._viewable = c._media.filter(function (m) { return m.a.s && VISUAL[m.a.k]; });
    c._viewable.forEach(function (m, vi) { m._vi = vi; });
  });

  var byId = {};
  DATA.chats.forEach(function (c) { byId[c.id] = c; });

  function senderOf(chat, m) { return m.w == null ? null : chat.people[m.w]; }

  function previewOf(chat) {
    var m = chat.msgs[chat.msgs.length - 1];
    if (!m) return { icon: "", prefix: "", body: "" };
    var who = senderOf(chat, m);
    var kind = KIND[m.k] || "text";
    var body = m.b || "";
    if (kind === "media" && m.a) {
      body = m.b || (m.a.k === "voice" ? "Voice message"
           : m.a.k === "image" ? "Photo"
           : m.a.k === "video" ? "Video"
           : m.a.k === "sticker" ? "Sticker"
           : m.a.k === "gif" ? "GIF"
           : m.a.k === "missing" ? "Media" : (m.a.n || "Document"));
    } else if (kind === "deleted") { body = "This message was deleted"; }
    var icon = kind === "media" && m.a ? (GLYPH[m.a.k] || GLYPH.document) : "";
    var prefix = m.o ? ticksFor(true) : (chat.group && who ? esc(who) + ": " : "");
    return { icon: icon, prefix: prefix, body: body.replace(/\s+/g, " ").slice(0, 120) };
  }

  /* ----------------------------------------------------------- chat list */

  function chatMatchesFilter(chat) {
    if (state.filter === "group") return !!chat.group;
    if (state.filter === "direct") return !chat.group;
    if (state.filter === "media") return chat._media.length > 0;
    return true;
  }

  function renderChatList() {
    var shown = DATA.chats.filter(chatMatchesFilter);
    var html = shown.map(function (chat) {
      var last = chat.msgs[chat.msgs.length - 1];
      var p = previewOf(chat);
      return '<li class="chatrow' + (chat.id === state.chatId ? " is-active" : "") +
        '" data-chat="' + esc(chat.id) + '" role="option" tabindex="0">' +
        avatar(chat.group) +
        '<div class="chatrow__main">' +
          '<div class="chatrow__top">' +
            '<span class="chatrow__name">' + esc(chat.title) + "</span>" +
            '<span class="chatrow__time">' + (last ? listStamp(last.t) : "") + "</span>" +
          "</div>" +
          '<div class="chatrow__bottom">' +
            '<span class="chatrow__preview">' + p.prefix + p.icon + esc(p.body) + "</span>" +
            '<span class="count" title="messages in this conversation">' +
              chat.msgs.length.toLocaleString() + "</span>" +
          "</div>" +
        "</div></li>";
    }).join("");
    $("chatList").innerHTML = html || '<li class="results__empty">No conversations match this filter.</li>';
    $("railChatCount").textContent = shown.length;
  }

  /* -------------------------------------------------------------- thread */

  var CHUNK = 250;
  var WAVE_BARS = 38;

  function voiceHtml(m) {
    var bars = "";
    for (var i = 0; i < WAVE_BARS; i++) bars += "<i style=\"height:30%\"></i>";
    return '<div class="voice" data-voice>' +
      avatar(false, "voice__avatar") +
      '<button class="voice__play" aria-label="Play voice message">' + GLYPH.play + "</button>" +
      '<div class="voice__body">' +
        '<div class="voice__wave" role="slider" tabindex="0" aria-label="Seek">' + bars + "</div>" +
        '<div class="voice__foot"><span class="voice__dur">--:--</span>' +
          '<button class="voice__rate">1&times;</button></div>' +
      "</div>" +
      '<span class="voice__mic">' + GLYPH.mic + "</span>" +
      '<audio preload="metadata" src="' + m.a.s + '"></audio>' +
      "</div>";
  }

  function attachmentHtml(m, mediaOnly) {
    var a = m.a;
    if (!a) return "";
    if (a.o || a.k === "missing" || !a.s) {
      var label = a.o ? "Media not included in this export"
                      : (a.n ? "File missing from export: " + a.n : "Media unavailable");
      return '<div class="missing">' + GLYPH.missing + esc(label) + "</div>";
    }

    if (a.k === "voice" || a.k === "audio") return voiceHtml(m);

    if (VISUAL[a.k]) {
      var cls = a.k === "sticker" ? "att att--sticker" : "att";
      var inner = a.k === "video"
        ? '<video controls preload="metadata" src="' + a.s + '" data-lb="' + m._vi + '"></video>'
        : '<img loading="lazy" src="' + a.s + '" alt="' + esc(a.n) + '" data-lb="' + m._vi + '">';
      // With no caption the timestamp has nowhere to sit, so it goes on the
      // picture over a scrim — exactly as WhatsApp does it.
      var stamp = (mediaOnly && a.k !== "sticker")
        ? '<span class="att__scrim"></span><span class="att__stamp">' +
          (m.e ? '<span class="bubble__edited">edited</span>' : "") +
          "<span>" + hhmm(dateOf(m.t)) + "</span>" + ticksFor(m.o) + "</span>"
        : "";
      return '<div class="' + cls + '">' + inner + stamp + "</div>";
    }

    return '<a class="file" href="' + a.s + '" download="' + esc(a.n) + '" title="Download ' + esc(a.n) + '">' +
           '<span class="file__icon">' + (GLYPH[a.k] || GLYPH.document) + "</span>" +
           '<span class="file__meta"><span class="file__name">' + esc(a.n) + "</span>" +
           '<span class="file__sub">' + esc(a.n.split(".").pop() || "file") +
           (a.z ? " &middot; " + humanBytes(a.z) : "") + "</span></span>" +
           '<span class="file__dl">' + GLYPH.download + "</span></a>";
  }

  function bodyHtml(m, mediaOnly) {
    var kind = KIND[m.k] || "text";
    if (kind === "deleted") {
      return '<div class="bubble__text">' + GLYPH.deleted +
             "<span>" + esc(m.b || "This message was deleted") + "</span></div>";
    }
    if (kind === "call") {
      return '<div class="bubble__text locmap">' + GLYPH.call +
             "<span>" + esc(m.b || "Call") + "</span></div>";
    }
    var out = "";
    if (m.a) out += attachmentHtml(m, mediaOnly);
    if (kind === "location" && m.b) {
      out += '<div class="bubble__text locmap">' + GLYPH.location + "<span>" + formatBody(m.b) + "</span></div>";
    } else if (m.b) {
      out += '<div class="bubble__text' + (m.a ? " att__caption" : "") + '">' + formatBody(m.b) + "</div>";
    }
    return out;
  }

  function metaHtml(m) {
    var bits = '<span class="bubble__meta" title="' + esc(fullStamp(m.t)) +
               (m.r && m.r.length ? "&#10;from: " + m.r.map(function (i) {
                 return esc(DATA.sources[i].label);
               }).join(", ") : "") + '">';
    if (m.e) bits += '<span class="bubble__edited">edited</span>';
    bits += "<span>" + hhmm(dateOf(m.t)) + "</span>" + ticksFor(m.o);
    if (m.r && m.r.length > 1) {
      bits += '<span class="srcdot" title="Present in ' + m.r.length + ' exports"></span>';
    }
    return bits + "</span>";
  }

  function messageHtml(m, chat, prev, index) {
    var kind = KIND[m.k] || "text";
    if (kind === "system") {
      return '<div class="sysmsg" data-i="' + index + '"><span>' + formatBody(m.b) + "</span></div>";
    }
    var who = senderOf(chat, m);
    var side = m.o ? "out" : "in";
    var runStart = !prev || prev.w !== m.w || (KIND[prev.k] || "text") === "system" ||
                   (m.t - prev.t) > 900;
    var sticker = m.a && m.a.k === "sticker" && m.a.s;
    var visual = m.a && m.a.s && VISUAL[m.a.k];
    var mediaOnly = visual && !m.b && !sticker;
    var jumbo = !m.a && isJumbo(m.b);

    var cls = "bubble" + (sticker ? " has-sticker" : "") + (jumbo ? " is-jumbo" : "") +
              (mediaOnly ? " has-media-only" : "") + (kind === "deleted" ? " is-deleted" : "");
    var head = (runStart && chat.group && who && !m.o)
      ? '<span class="bubble__who" style="color:' + nameColor(who) + '">' + esc(who) + "</span>"
      : "";
    var tail = mediaOnly ? "" : ('<span class="bubble__pad"></span>' + metaHtml(m));
    var more = sticker ? "" : '<span class="bubble__more">' +
      '<svg viewBox="0 0 20 20"><path d="M5 8l5 5 5-5"/></svg></span>';

    return '<div class="row ' + side + (runStart ? " is-runstart" : "") + '" data-i="' + index + '">' +
             '<div class="' + cls + '">' + more + head + bodyHtml(m, mediaOnly) + tail +
             "</div></div>";
  }

  function renderThread(focusIndex) {
    var chat = byId[state.chatId];
    if (!chat) return;
    var msgs = chat.msgs;
    var parts = [];
    var lastDay = null;
    var prev = null;

    if (state.from > 0) {
      // Keep run-grouping correct across the chunk boundary.
      prev = msgs[state.from - 1];
      lastDay = dayKey(dateOf(prev.t));
    }

    for (var i = state.from; i < msgs.length; i++) {
      var m = msgs[i];
      var d = dateOf(m.t);
      var k = dayKey(d);
      if (k !== lastDay) {
        parts.push('<div class="daypill"><span>' + esc(daySeparatorLabel(d)) + "</span></div>");
        lastDay = k;
        prev = null;
      }
      parts.push(messageHtml(m, chat, prev, i));
      prev = m;
    }

    $("msgs").innerHTML = parts.join("");
    observeWaves();
    var remaining = Math.min(CHUNK, state.from);
    $("loadMore").hidden = state.from === 0;
    $("loadMore").textContent = "Load " + remaining.toLocaleString() +
      " earlier message" + (remaining === 1 ? "" : "s") +
      " (" + state.from.toLocaleString() + " above)";

    var scroller = $("msgScroll");
    var target = focusIndex == null
      ? null
      : $("msgs").querySelector('[data-i="' + focusIndex + '"]');

    if (target) {
      var bubble = target.querySelector(".bubble") || target.querySelector("span");
      if (bubble && bubble.animate) {
        bubble.animate(
          [{ boxShadow: "0 0 0 3px var(--accent)" }, { boxShadow: "0 0 0 0 transparent" }],
          { duration: 1400, easing: "ease-out" }
        );
      }
      settleScroll(scroller, function () { target.scrollIntoView({ block: "center" }); });
    } else {
      settleScroll(scroller, function () { scroller.scrollTop = scroller.scrollHeight; });
    }
  }

  // Images and video have no intrinsic size until they load, so a scroll
  // issued right after render lands in the wrong place. Re-apply it as each
  // one arrives, and stop as soon as the reader scrolls for themselves.
  function settleScroll(scroller, apply) {
    var active = true;
    function reapply() { if (active) apply(); }
    reapply();
    requestAnimationFrame(reapply);
    Array.prototype.forEach.call($("msgs").querySelectorAll("img, video"), function (el) {
      el.addEventListener("load", reapply, { once: true });
      el.addEventListener("loadedmetadata", reapply, { once: true });
      el.addEventListener("error", reapply, { once: true });
    });
    function release() { active = false; }
    scroller.addEventListener("wheel", release, { once: true, passive: true });
    scroller.addEventListener("touchstart", release, { once: true, passive: true });
    setTimeout(release, 3000);
  }

  function openChat(id, focusIndex) {
    var chat = byId[id];
    if (!chat) return;
    stopAudio();
    state.chatId = id;
    var total = chat.msgs.length;
    state.from = focusIndex != null
      ? Math.max(0, focusIndex - Math.floor(CHUNK / 3))
      : Math.max(0, total - CHUNK);

    $("emptyState").hidden = true;
    $("thread").hidden = false;
    $("app").classList.add("is-open");
    $("chatAvatar").outerHTML = avatar(chat.group).replace("<span ", '<span id="chatAvatar" ');
    $("chatTitle").textContent = chat.title;

    var others = chat.people.filter(function (p) { return p !== DATA.owner; });
    var span = total
      ? ddmmyyyy(dateOf(chat.msgs[0].t)) + " – " + ddmmyyyy(dateOf(chat.msgs[total - 1].t))
      : "";
    $("chatSub").textContent = chat.group
      ? others.slice(0, 6).join(", ") + (others.length > 6 ? " +" + (others.length - 6) : "")
      : span + "  ·  " + total.toLocaleString() + " messages";

    renderChatList();
    renderThread(focusIndex);
    closeDrawer();
    history.replaceState(null, "", "#" + encodeURIComponent(id) + (focusIndex != null ? "/" + focusIndex : ""));
  }

  /* --------------------------------------------------------- voice notes */

  var audioCtx = null;

  function stopAudio() {
    if (state.audio) {
      state.audio.pause();
      var box = state.audio.closest(".voice");
      if (box) box.querySelector(".voice__play").innerHTML = GLYPH.play;
      state.audio = null;
    }
  }

  function paintWave(box, ratio) {
    var bars = box.querySelectorAll(".voice__wave i");
    var upto = Math.round(ratio * bars.length);
    for (var i = 0; i < bars.length; i++) bars[i].classList.toggle("is-played", i < upto);
  }

  // Draw the real amplitude envelope rather than a decorative one. If the
  // browser cannot decode the codec (Safari and Opus, typically) the neutral
  // placeholder bars stay as they are.
  function decodeWave(box, audio) {
    if (box.dataset.decoded) return;
    box.dataset.decoded = "1";
    var AC = window.AudioContext || window.webkitAudioContext;
    if (!AC || !window.fetch) return;
    try { audioCtx = audioCtx || new AC(); } catch (e) { return; }
    fetch(audio.src)
      .then(function (r) { return r.arrayBuffer(); })
      .then(function (buf) { return audioCtx.decodeAudioData(buf); })
      .then(function (decoded) {
        var raw = decoded.getChannelData(0);
        var bars = box.querySelectorAll(".voice__wave i");
        var step = Math.floor(raw.length / bars.length) || 1;
        var peaks = [], max = 0;
        for (var i = 0; i < bars.length; i++) {
          var peak = 0;
          for (var j = i * step; j < (i + 1) * step && j < raw.length; j++) {
            var v = Math.abs(raw[j]);
            if (v > peak) peak = v;
          }
          peaks.push(peak);
          if (peak > max) max = peak;
        }
        if (!max) return;
        peaks.forEach(function (p, i) {
          bars[i].style.height = Math.max(12, Math.round(p / max * 100)) + "%";
        });
      })
      .catch(function () { /* codec unsupported — keep the placeholder */ });
  }

  // Decode each voice note's waveform once it scrolls into view, so the bars
  // show the real envelope rather than waiting for the reader to press play.
  var waveObserver = window.IntersectionObserver
    ? new IntersectionObserver(function (entries) {
        entries.forEach(function (entry) {
          if (!entry.isIntersecting) return;
          waveObserver.unobserve(entry.target);
          decodeWave(entry.target, entry.target.querySelector("audio"));
        });
      }, { rootMargin: "200px" })
    : null;

  function observeWaves() {
    if (!waveObserver) return;
    Array.prototype.forEach.call($("msgs").querySelectorAll("[data-voice]"), function (box) {
      waveObserver.observe(box);
    });
  }

  function wireVoice(root) {
    root.addEventListener("click", function (e) {
      var box = e.target.closest("[data-voice]");
      if (!box) return;
      var audio = box.querySelector("audio");
      var btn = box.querySelector(".voice__play");

      if (e.target.closest(".voice__rate")) {
        var rates = [1, 1.5, 2];
        state.rate = rates[(rates.indexOf(state.rate) + 1) % rates.length];
        audio.playbackRate = state.rate;
        box.querySelector(".voice__rate").innerHTML = state.rate + "&times;";
        return;
      }

      var wave = e.target.closest(".voice__wave");
      if (wave) {
        var r = wave.getBoundingClientRect();
        var ratio = Math.min(Math.max((e.clientX - r.left) / r.width, 0), 1);
        if (isFinite(audio.duration)) audio.currentTime = ratio * audio.duration;
        paintWave(box, ratio);
        return;
      }

      if (!e.target.closest(".voice__play")) return;
      if (state.audio === audio && !audio.paused) {
        audio.pause();
        btn.innerHTML = GLYPH.play;
        state.audio = null;
        return;
      }
      stopAudio();
      decodeWave(box, audio);
      audio.playbackRate = state.rate;
      audio.play().then(function () {
        state.audio = audio;
        btn.innerHTML = GLYPH.pause;
      }).catch(function () { /* autoplay policy or bad codec */ });
    });

    root.addEventListener("loadedmetadata", function (e) {
      var box = e.target.closest && e.target.closest("[data-voice]");
      if (box && e.target.tagName === "AUDIO") {
        box.querySelector(".voice__dur").textContent = clock(e.target.duration);
      }
    }, true);

    root.addEventListener("timeupdate", function (e) {
      var box = e.target.closest && e.target.closest("[data-voice]");
      if (!box || e.target.tagName !== "AUDIO") return;
      var a = e.target;
      box.querySelector(".voice__dur").textContent = clock(a.currentTime);
      if (isFinite(a.duration) && a.duration > 0) paintWave(box, a.currentTime / a.duration);
    }, true);

    root.addEventListener("ended", function (e) {
      var box = e.target.closest && e.target.closest("[data-voice]");
      if (!box || e.target.tagName !== "AUDIO") return;
      box.querySelector(".voice__play").innerHTML = GLYPH.play;
      box.querySelector(".voice__dur").textContent = clock(e.target.duration);
      paintWave(box, 0);
      state.audio = null;
    }, true);
  }

  /* -------------------------------------------------------------- search */

  var MAX_HITS = 400;

  function runSearch(q) {
    var needle = fold(q).trim();
    var showList = needle.length < 2;
    $("chatList").hidden = !showList;
    $("results").hidden = showList;
    $("searchClear").hidden = !q;
    if (showList) return;

    var hits = [];
    for (var c = 0; c < DATA.chats.length && hits.length < MAX_HITS; c++) {
      var chat = DATA.chats[c];
      if (!chatMatchesFilter(chat)) continue;
      for (var i = 0; i < chat.msgs.length; i++) {
        var m = chat.msgs[i];
        var hay = m.b || (m.a && m.a.n) || "";
        if (!hay) continue;
        var pos = fold(hay).indexOf(needle);
        if (pos < 0) continue;
        hits.push({ chat: chat, msg: m, index: i, pos: pos, text: hay });
        if (hits.length >= MAX_HITS) break;
      }
    }

    if (!hits.length) {
      $("results").innerHTML = '<div class="results__empty">No messages match <b>' + esc(q) + "</b></div>";
      return;
    }

    var html = '<div class="results__head">' + hits.length.toLocaleString() +
      (hits.length === MAX_HITS ? "+" : "") + " message" + (hits.length === 1 ? "" : "s") + "</div>";

    html += hits.map(function (h) {
      var start = Math.max(0, h.pos - 34);
      var snippet = (start ? "…" : "") + h.text.slice(start, h.pos) +
        "\u0001" + h.text.substr(h.pos, needle.length) + "\u0002" +
        h.text.slice(h.pos + needle.length, h.pos + needle.length + 90);
      var who = senderOf(h.chat, h.msg);
      return '<div class="hit" data-chat="' + esc(h.chat.id) + '" data-i="' + h.index + '">' +
        '<div class="hit__top"><span class="hit__chat">' + esc(h.chat.title) + "</span>" +
        "<span>" + ddmmyyyy(dateOf(h.msg.t)) + "</span></div>" +
        '<div class="hit__body">' + (who ? "<b>" + esc(who) + ":</b> " : "") +
        esc(snippet).replace("\u0001", "<mark>").replace("\u0002", "</mark>") + "</div></div>";
    }).join("");

    $("results").innerHTML = html;
  }

  /* -------------------------------------------------------------- drawer */

  function openDrawer(title, html) {
    $("drawerTitle").textContent = title;
    $("drawerBody").innerHTML = html;
    $("drawer").hidden = false;
  }
  function closeDrawer() { $("drawer").hidden = true; }

  function barChart(rows, total) {
    return '<div class="barchart">' + rows.map(function (r) {
      var pct = total ? Math.round(r.n / total * 100) : 0;
      return '<div class="barchart__row"><span>' + esc(r.label) + "</span><span>" +
        r.n.toLocaleString() + " · " + pct + "%</span>" +
        '<span class="barchart__track"><span class="barchart__fill" style="width:' + pct +
        "%;background:" + (r.color || "var(--accent)") + '"></span></span></div>';
    }).join("") + "</div>";
  }

  function mediaGrid(items) {
    return '<div class="mediagrid">' + items.map(function (m) {
      return m.a.k === "video"
        ? '<video src="' + m.a.s + '" data-lb="' + m._vi + '" data-chat="' + esc(m._chat) + '" preload="metadata"></video>'
        : '<img loading="lazy" src="' + m.a.s + '" data-lb="' + m._vi + '" data-chat="' + esc(m._chat) +
          '" alt="' + esc(m.a.n) + '">';
    }).join("") + "</div>";
  }

  function chatInfo(chat) {
    if (!chat) return;
    var counts = {}, kinds = {}, srcCounts = {}, confirmed = 0;
    chat.msgs.forEach(function (m) {
      var who = senderOf(chat, m);
      if (who) counts[who] = (counts[who] || 0) + 1;
      if (m.a) kinds[m.a.k] = (kinds[m.a.k] || 0) + 1;
      (m.r || []).forEach(function (s) { srcCounts[s] = (srcCounts[s] || 0) + 1; });
      if (m.r && m.r.length > 1) confirmed++;
    });

    var people = Object.keys(counts).sort(function (a, b) { return counts[b] - counts[a]; });
    var total = chat.msgs.length;
    var first = total ? dateOf(chat.msgs[0].t) : null;
    var last = total ? dateOf(chat.msgs[total - 1].t) : null;
    var days = first && last ? Math.max(1, Math.round((last - first) / 86400000) + 1) : 0;

    var html = '<div class="drawer__hero">' + avatar(chat.group, "avatar--big") +
      "<h2>" + esc(chat.title) + "</h2><p>" +
      (chat.group ? people.length + " participants" : "Direct conversation") + "</p></div>";

    html += '<div class="drawer__section"><h3>Overview</h3>' +
      '<div class="kv"><span>Messages</span><span>' + total.toLocaleString() + "</span></div>" +
      '<div class="kv"><span>First message</span><span>' + (first ? ddmmyyyy(first) : "—") + "</span></div>" +
      '<div class="kv"><span>Last message</span><span>' + (last ? ddmmyyyy(last) : "—") + "</span></div>" +
      '<div class="kv"><span>Span</span><span>' + days.toLocaleString() + " days</span></div>" +
      '<div class="kv"><span>Attachments</span><span>' + chat._media.length.toLocaleString() + "</span></div>" +
      "</div>";

    html += '<div class="drawer__section"><h3>Who wrote what</h3>' +
      barChart(people.map(function (p) {
        return { label: p + (p === DATA.owner ? " (you)" : ""), n: counts[p], color: nameColor(p) };
      }), total) + "</div>";

    var mediaRows = Object.keys(kinds).sort(function (a, b) { return kinds[b] - kinds[a]; })
      .map(function (k) { return { label: k, n: kinds[k] }; });
    if (mediaRows.length) {
      html += '<div class="drawer__section"><h3>Attachments</h3>' +
        barChart(mediaRows, chat._media.length) + "</div>";
    }

    html += '<div class="drawer__section"><h3>Rebuilt from ' + chat.sources.length +
      " export" + (chat.sources.length === 1 ? "" : "s") + '</h3><div class="srclist">' +
      chat.sources.map(function (s) {
        return '<div class="srcrow"><span class="srcrow__name">' + esc(DATA.sources[s].label) +
          '</span><span class="srcrow__n">' + (srcCounts[s] || 0).toLocaleString() + "</span></div>";
      }).join("") +
      (chat.sources.length > 1
        ? '<div class="kv" style="margin-top:8px"><span>Present in more than one export</span><span>' +
          confirmed.toLocaleString() + "</span></div>"
        : "") +
      "</div></div>";

    var pics = chat._viewable.slice(-30).reverse();
    pics.forEach(function (m) { m._chat = chat.id; });
    if (pics.length) {
      html += '<div class="drawer__section"><h3>Media</h3>' + mediaGrid(pics) + "</div>";
    }

    openDrawer("Conversation details", html);
  }

  function archiveMedia() {
    var items = [];
    DATA.chats.forEach(function (c) {
      c._viewable.forEach(function (m) { m._chat = c.id; items.push(m); });
    });
    items.sort(function (a, b) { return b.t - a.t; });
    var html = items.length
      ? '<div class="drawer__section"><h3>' + items.length.toLocaleString() +
        " photo" + (items.length === 1 ? "" : "s") + ", video and sticker" +
        (items.length === 1 ? "" : "s") + " across the archive</h3>" +
        mediaGrid(items.slice(0, 300)) +
        (items.length > 300 ? '<p style="color:var(--text-2);font-size:13px;margin:12px 0 0">' +
          "Showing the 300 most recent.</p>" : "") + "</div>"
      : '<div class="results__empty">No viewable media in this archive.<br>' +
        "Exports made without media only record that an attachment was there.</div>";
    openDrawer("Media", html);
  }

  function archiveStats() {
    var totals = { msgs: 0, media: 0, confirmed: 0 };
    var first = null, last = null, srcCounts = {};
    DATA.chats.forEach(function (c) {
      totals.msgs += c.msgs.length;
      totals.media += c._media.length;
      c.msgs.forEach(function (m) {
        if (m.r && m.r.length > 1) totals.confirmed++;
        (m.r || []).forEach(function (s) { srcCounts[s] = (srcCounts[s] || 0) + 1; });
        if (first === null || m.t < first) first = m.t;
        if (last === null || m.t > last) last = m.t;
      });
    });

    var html = '<div class="drawer__section"><h3>' + esc(DATA.title) + "</h3>" +
      '<div class="kv"><span>Conversations</span><span>' + DATA.chats.length.toLocaleString() + "</span></div>" +
      '<div class="kv"><span>Messages</span><span>' + totals.msgs.toLocaleString() + "</span></div>" +
      '<div class="kv"><span>Attachments</span><span>' + totals.media.toLocaleString() + "</span></div>" +
      '<div class="kv"><span>Source exports</span><span>' + DATA.sources.length + "</span></div>" +
      '<div class="kv"><span>Present in 2+ exports</span><span>' + totals.confirmed.toLocaleString() + "</span></div>" +
      (first ? '<div class="kv"><span>Covers</span><span>' + ddmmyyyy(dateOf(first)) + " – " +
        ddmmyyyy(dateOf(last)) + "</span></div>" : "") +
      '<div class="kv"><span>Archive owner</span><span>' + esc(DATA.owner || "unknown") + "</span></div>" +
      '<div class="kv"><span>Built</span><span>' + esc(DATA.generated) + "</span></div></div>";

    html += '<div class="drawer__section"><h3>Busiest conversations</h3>' +
      barChart(DATA.chats.slice().sort(function (a, b) { return b.msgs.length - a.msgs.length; })
        .slice(0, 10).map(function (c) {
          return { label: c.title, n: c.msgs.length, color: nameColor(c.title) };
        }), totals.msgs) + "</div>";

    html += '<div class="drawer__section"><h3>Source exports</h3><div class="srclist">' +
      DATA.sources.map(function (s, i) {
        return '<div class="srcrow"><span class="srcrow__name">' + esc(s.label) +
          '</span><span class="srcrow__n">' + (srcCounts[i] || 0).toLocaleString() + " msgs</span></div>";
      }).join("") + "</div></div>";

    if (DATA.notes && DATA.notes.length) {
      html += '<div class="drawer__section"><h3>Notes from the merge</h3><div class="srclist">' +
        DATA.notes.map(function (n) {
          return '<div class="srcrow"><span class="srcrow__name">' + esc(n) + "</span></div>";
        }).join("") + "</div></div>";
    }

    openDrawer("Archive statistics", html);
  }

  /* ------------------------------------------------------------ lightbox */

  function openLightbox(chat, viewableIndex) {
    if (!chat || !chat._viewable.length) return;
    stopAudio();
    state.lightbox = {
      chat: chat,
      items: chat._viewable,
      index: Math.min(Math.max(viewableIndex || 0, 0), chat._viewable.length - 1)
    };
    $("lightbox").hidden = false;
    paintLightbox();
  }

  function paintLightbox() {
    var lb = state.lightbox;
    var m = lb.items[lb.index];
    if (!m) return;
    var who = senderOf(lb.chat, m);
    $("lbStage").innerHTML = m.a.k === "video"
      ? '<video src="' + m.a.s + '" controls autoplay></video>'
      : '<img src="' + m.a.s + '" alt="' + esc(m.a.n) + '">';
    $("lbWho").textContent = (who || "Unknown") + " · " + fullStamp(m.t);
    $("lbCaption").textContent = (m.b ? m.b.slice(0, 160) + "  ·  " : "") +
      m.a.n + "   (" + (lb.index + 1) + "/" + lb.items.length + ")";
  }

  function stepLightbox(delta) {
    var lb = state.lightbox;
    if (!lb.items || !lb.items.length) return;
    lb.index = (lb.index + delta + lb.items.length) % lb.items.length;
    paintLightbox();
  }

  function closeLightbox() {
    $("lightbox").hidden = true;
    $("lbStage").innerHTML = "";
  }

  /* --------------------------------------------------------------- theme */

  function applyTheme(mode) {
    document.documentElement.setAttribute("data-theme", mode);
    try { localStorage.setItem("whatsmerge-theme", mode); } catch (e) { /* private mode */ }
  }

  function currentTheme() {
    try { return localStorage.getItem("whatsmerge-theme") || DATA.theme || "auto"; }
    catch (e) { return DATA.theme || "auto"; }
  }

  /* --------------------------------------------------------------- wiring */

  function debounce(fn, ms) {
    var t;
    return function () {
      var args = arguments, self = this;
      clearTimeout(t);
      t = setTimeout(function () { fn.apply(self, args); }, ms);
    };
  }

  function railSelect(id) {
    ["railChats", "railMedia", "railStats"].forEach(function (k) {
      $(k).classList.toggle("is-on", k === id);
    });
  }

  function boot() {
    applyTheme(currentTheme());

    $("railMe").innerHTML = avatar(false);
    $("railMe").title = DATA.owner ? "Archive owner: " + DATA.owner : "Archive owner unknown";
    $("emptyTitle").textContent = DATA.title;
    $("emptyBody").textContent = DATA.owner
      ? "Merged from " + DATA.sources.length + " export file" +
        (DATA.sources.length === 1 ? "" : "s") + ". Messages from " + DATA.owner +
        " are on the right. Select a conversation to read it."
      : "Select a conversation to read it.";

    renderChatList();

    $("chatList").addEventListener("click", function (e) {
      var row = e.target.closest("[data-chat]");
      if (row) openChat(row.dataset.chat);
    });
    $("chatList").addEventListener("keydown", function (e) {
      var row = e.target.closest("[data-chat]");
      if (row && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); openChat(row.dataset.chat); }
    });

    $("results").addEventListener("click", function (e) {
      var hit = e.target.closest(".hit");
      if (hit) openChat(hit.dataset.chat, parseInt(hit.dataset.i, 10));
    });

    var onSearch = debounce(function () { runSearch($("search").value); }, 140);
    $("search").addEventListener("input", onSearch);
    $("searchClear").addEventListener("click", function () {
      $("search").value = ""; runSearch(""); $("search").focus();
    });

    $("filters").addEventListener("click", function (e) {
      var pill = e.target.closest("[data-filter]");
      if (!pill) return;
      state.filter = pill.dataset.filter;
      Array.prototype.forEach.call($("filters").children, function (b) {
        b.classList.toggle("is-on", b === pill);
      });
      renderChatList();
      if ($("search").value) runSearch($("search").value);
    });

    // Printing must show the whole conversation, not just the loaded chunk.
    window.addEventListener("beforeprint", function () {
      if (state.chatId && state.from !== 0) { state.from = 0; renderThread(); }
    });

    $("loadMore").addEventListener("click", function () {
      var scroller = $("msgScroll");
      var before = scroller.scrollHeight - scroller.scrollTop;
      state.from = Math.max(0, state.from - CHUNK);
      renderThread();
      scroller.scrollTop = scroller.scrollHeight - before;
    });

    $("msgs").addEventListener("click", function (e) {
      var target = e.target.closest("[data-lb]");
      if (target) openLightbox(byId[state.chatId], parseInt(target.dataset.lb, 10));
    });
    $("drawerBody").addEventListener("click", function (e) {
      var target = e.target.closest("[data-lb]");
      if (!target) return;
      var chat = byId[target.dataset.chat] || byId[state.chatId];
      openLightbox(chat, parseInt(target.dataset.lb, 10));
    });

    wireVoice($("msgs"));

    var scroller = $("msgScroll");
    scroller.addEventListener("scroll", function () {
      var atBottom = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 120;
      $("jumpDown").hidden = atBottom;
    }, { passive: true });
    $("jumpDown").addEventListener("click", function () {
      scroller.scrollTo({ top: scroller.scrollHeight, behavior: "smooth" });
    });

    $("btnInfo").addEventListener("click", function () { chatInfo(byId[state.chatId]); });
    $("chatTitleWrap").addEventListener("click", function () { chatInfo(byId[state.chatId]); });
    $("btnMenu").addEventListener("click", archiveStats);
    $("drawerClose").addEventListener("click", closeDrawer);
    $("btnFind").addEventListener("click", function () { $("search").focus(); $("search").select(); });
    $("btnBack").addEventListener("click", function () { $("app").classList.remove("is-open"); });

    $("railChats").addEventListener("click", function () { railSelect("railChats"); closeDrawer(); });
    $("railMedia").addEventListener("click", function () { railSelect("railMedia"); archiveMedia(); });
    $("railStats").addEventListener("click", function () { railSelect("railStats"); archiveStats(); });
    $("railPrint").addEventListener("click", function () { window.print(); });
    $("railTheme").addEventListener("click", function () {
      var order = ["auto", "light", "dark"];
      applyTheme(order[(order.indexOf(currentTheme()) + 1) % order.length]);
    });
    $("railMe").addEventListener("click", archiveStats);

    $("lbClose").addEventListener("click", closeLightbox);
    $("lbPrev").addEventListener("click", function () { stepLightbox(-1); });
    $("lbNext").addEventListener("click", function () { stepLightbox(1); });
    $("lightbox").addEventListener("click", function (e) {
      if (e.target === $("lightbox") || e.target === $("lbStage")) closeLightbox();
    });

    document.addEventListener("keydown", function (e) {
      if (!$("lightbox").hidden) {
        if (e.key === "Escape") closeLightbox();
        if (e.key === "ArrowLeft") stepLightbox(-1);
        if (e.key === "ArrowRight") stepLightbox(1);
        return;
      }
      if (e.key === "/" && document.activeElement !== $("search")) {
        e.preventDefault(); $("search").focus();
      } else if (e.key === "Escape") {
        if (!$("drawer").hidden) { closeDrawer(); railSelect("railChats"); }
        else if ($("search").value) { $("search").value = ""; runSearch(""); }
      }
    });

    // Deep link: #chatId or #chatId/messageIndex
    var hash = decodeURIComponent(location.hash.replace(/^#/, ""));
    var parts = hash.split("/");
    if (parts[0] && byId[parts[0]]) {
      openChat(parts[0], parts[1] ? parseInt(parts[1], 10) : null);
    } else if (DATA.chats.length === 1) {
      openChat(DATA.chats[0].id);
    }

    $("boot").remove();
    $("app").hidden = false;
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
