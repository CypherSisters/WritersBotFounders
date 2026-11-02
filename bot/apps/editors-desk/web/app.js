/* Editor's Desk, the page. Every call goes to the page's own origin with no token; the PuffPAI Desktop's proxy adds the key.
   The server sends data, never markup: everything here is built with text nodes. Her text changes only from Save, Bring this
   version back, and Use this. */
(function () {
  'use strict';
  const $ = id => document.getElementById(id);
  const SIZE = {big: 'Big', medium: 'Worth a look', small: 'Small'};
  const STATE = [['open', 'Open'], ['later', 'Later'], ['fixed', 'Fixed'], ['not_a_problem', 'Not a problem']];
  const S = {books: [], book: null, v: null, filter: 'open', only: '', sec: null, sections: [], editing: false, dirty: false, opened: {}, ask: null, typed: {}, old: null};

  // ---------------------------------------------------------------- small helpers

  function el(tag, attrs, kids) {
    const n = document.createElement(tag);
    for (const k in attrs || {}) {
      if (k === 'on') for (const e in attrs.on) n.addEventListener(e, attrs.on[e]);
      else if (k === 'text') n.textContent = attrs.text;
      else if (k === 'cls') n.className = attrs.cls;
      else if (attrs[k] !== undefined && attrs[k] !== null && attrs[k] !== false) n.setAttribute(k, attrs[k] === true ? '' : attrs[k]);
    }
    for (const c of kids || []) if (c) n.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
    return n;
  }
  const clear = n => { while (n.firstChild) n.removeChild(n.firstChild); return n; };
  const q = o => Object.keys(o).map(k => encodeURIComponent(k) + '=' + encodeURIComponent(o[k])).join('&');

  async function call(path, body) {
    const r = await fetch(path, body === undefined ? {} : {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    let got = {};
    try { got = await r.json(); } catch (e) { got = {}; }
    if (!r.ok) throw new Error(got.error || 'That did not work. Try again.');
    return got;
  }
  function when(iso) {
    const d = new Date(iso);
    if (isNaN(d)) return '';
    return d.toLocaleString(undefined, {month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit'});
  }
  const plural = (n, one, many) => n + ' ' + (n === 1 ? one : (many || one + 's'));

  // ---------------------------------------------------------------- loading

  async function loadBooks() {
    const b = $('book');
    try {
      S.books = (await call('api/list')).books;
    } catch (e) {
      clear($('letter')).appendChild(el('p', {text: e.message}));
      return;
    }
    clear(b);
    if (!S.books.length) {
      clear($('letter')).append(el('h1', {text: "Your editor's letter"}), el('p', {text: 'There are no books yet. Add one on the Manuscript page, let your bot read it, and come back.'}));
      return;
    }
    for (const x of S.books) b.appendChild(el('option', {value: x.title, text: x.title}));
    b.disabled = false;
    const want = new URLSearchParams(location.search).get('title');
    const first = S.books.find(x => x.read) || S.books[0];        // a read book first, not one still waiting
    await pick(want && S.books.some(x => x.title === want) ? want : first.title);
  }

  async function pick(title) {
    if (!leaveOk()) { $('book').value = S.book; return; }
    S.book = title;
    $('book').value = title;
    closePane();
    try { await call('api/open', {title}); } catch (e) { /* the view below says what is wrong */ }
    await refresh();
  }

  async function refresh() {
    if (!S.book) return;
    try {
      const got = await call('api/desk?' + q({title: S.book}));
      const same = S.seen === JSON.stringify({...got, status: null});
      S.seen = JSON.stringify({...got, status: null});
      S.v = got;
      if (same) { drawWork(); return; }                // nothing but the progress line moved: leave what she has open
    } catch (e) {
      clear($('work')).appendChild(el('span', {text: e.message}));
      $('work').hidden = false;
      return;
    }
    draw();
  }

  function draw() {
    const v = S.v;
    $('count').textContent = plural(v.sections.length, 'section') + ', ' + v.words.toLocaleString() + ' words';
    const st = $('stale');
    st.hidden = !(v.unread && v.unread.length);
    if (!st.hidden) st.textContent = (v.unread.length === 1 ? 'Section ' + v.unread[0] + ' has' : 'Sections ' + v.unread.join(', ') + ' have') + ' changed since your book was last read. Your editor goes by your new words, but the story bible still has the old ones. Read the book again in Manuscript when you are ready.';
    $('private').hidden = !v.private;
    drawWork(); drawLetter(); drawNext(); drawStages(); drawAnytime(); drawFilters(); drawList(); drawDocs();
  }

  // ---------------------------------------------------------------- what your editor is doing

  function drawWork() {
    const box = clear($('work'));
    const st = S.v.status;
    const live = st && ['running', 'waiting', 'queued'].includes(st.state);
    box.hidden = !st;
    if (!st) return;
    box.className = 'work' + (st.state === 'failed' ? ' bad' : '');
    if (live) {
      box.append(el('b', {text: 'Your editor: ' + (st.check_name || '')}), el('span', {cls: 'grow', text: st.step || ''}));
      if (st.total) box.appendChild(el('progress', {max: st.total, value: Math.min(st.done || 0, st.total)}));
      if (st.total) box.appendChild(el('span', {text: 'step ' + (st.done || 0) + ' of ' + st.total}));
      box.appendChild(el('span', {text: st.left || (st.total ? 'timing your bot...' : '')}));
      if (st.in_line && st.in_line.length) box.appendChild(el('span', {text: 'then ' + st.in_line.join(', ')}));
      box.appendChild(el('button', {type: 'button', text: 'Stop', on: {click: stop}}));
    } else {
      box.appendChild(el('span', {cls: 'grow', text: st.step || ''}));
      if (st.problems && st.problems.length) {
        const list = el('ul', {}, st.problems.map(p => el('li', {text: p})));
        box.appendChild(el('details', {}, [el('summary', {text: plural(st.problems.length, 'thing') + ' to know'}), list]));
      }
    }
  }
  async function stop() {
    try { await call('api/stop', {title: S.book}); } catch (e) { /* the status says */ }
    refresh();
  }

  // ---------------------------------------------------------------- the letter

  function drawLetter() {
    const box = clear($('letter'));
    const v = S.v, lt = v.letter;
    box.appendChild(el('h1', {text: "Your editor's letter"}));
    if (!lt) {
      box.appendChild(el('p', {text: v.has_bible ? 'It comes with the first check. Start with the one below, and your editor will write to you when it is done.' :
        'Your bot has not read this book yet. Open Manuscript and let it read the book; your editor works from what it finds. A few checks need no reading and can run now.'}));
      if (!v.has_bible) box.appendChild(el('button', {type: 'button', text: 'Open Manuscript', on: {click: openManuscript}}));
      return;
    }
    box.appendChild(el('p', {text: lt.book}));
    if (lt.working && lt.working.length) {
      box.appendChild(el('h3', {text: "What's working"}));
      box.appendChild(el('ul', {}, lt.working.map(x => el('li', {text: x}))));
    }
    const byId = {};
    for (const f of v.findings) byId[f.id] = f;
    const still = (lt.problems || []).filter(p => byId[p.id] && byId[p.id].kind !== 'question');   // never one since dropped
    if (still.length) {
      box.appendChild(el('h3', {text: 'The biggest problems'}));
      box.appendChild(el('ol', {}, still.map(p => {
        const f = byId[p.id];
        const done = f && f.state !== 'open' && f.state !== 'later';
        return el('li', {cls: done ? 'done' : ''}, [f ? el('button', {cls: 'link', type: 'button', text: p.line, on: {click: () => showFinding(f)}}) : p.line]);
      })));
    }
    if (lt.first) { box.appendChild(el('h3', {text: 'What to do first'})); box.appendChild(el('p', {text: lt.first})); }
    if (v.plan && v.plan.length) {
      const list = el('ol', {}, v.plan.map(p => el('li', {}, [el('button', {cls: 'link', type: 'button', text: p.what, on: {click: () => showFinding(byId[p.id])}}), p.stage ? ' (' + p.stage + ')' : ''])));
      box.appendChild(el('details', {}, [el('summary', {text: 'The revision plan: ' + plural(v.open_now, 'thing') + ' open, in the order to do them'}), list]));
    }
    const moved = lt.open_then !== undefined && lt.open_then !== v.open_now;
    box.appendChild(el('p', {cls: 'fine', text: 'Written ' + when(lt.written_at) + (lt.from_checks && lt.from_checks.length ? ', from ' + plural(lt.from_checks.length, 'check') : '') + '.' + (moved ? ' You have marked things since; the revision plan keeps up, and the letter is as it was then.' : '')}));
    box.appendChild(el('button', {type: 'button', text: 'Write it again', disabled: isBusy('letter'), on: {click: () => run('letter')}}));
  }

  // ---------------------------------------------------------------- next

  function drawNext() {
    const box = clear($('next'));
    const n = S.v.next;
    box.hidden = !n;
    if (!n) return;
    if (!n.check) {
      box.append(el('strong', {text: 'Next: settle ' + n.stage}), el('p', {text: n.why}));
      return;
    }
    box.append(el('strong', {text: 'Next: ' + n.name}), el('p', {text: n.why}));
    if (!n.can_run) { box.appendChild(el('p', {text: 'It needs your book read in Manuscript first.'})); return; }
    let input = null;
    if (n.box) { input = boxFor(n.check, n.box); box.appendChild(input); }
    box.appendChild(el('button', {cls: 'main', type: 'button', text: n.button === 'Run' ? 'Run ' + n.name : n.button, disabled: isBusy(n.check), on: {click: () => run(n.check, input)}}));
  }

  // ---------------------------------------------------------------- the stages

  const isBusy = id => {
    const st = S.v.status;
    return !!(st && ['running', 'waiting', 'queued'].includes(st.state) && st.check === id);
  };

  function boxFor(id, label) {
    const box = el('input', {type: 'text', maxlength: '1000', placeholder: label, 'aria-label': label, value: S.typed[id] || '',
      on: {input: e => { S.typed[id] = e.target.value; }}});
    return box;
  }

  function checkRow(c) {
    const running = c.running;
    const states = [];
    if (running) states.push(running === 'queued' ? 'In line' : 'Running now');
    else if (c.last_run) states.push('Last run ' + when(c.last_run));
    else states.push('Not run yet');
    if (c.open) states.push(plural(c.open, 'open finding'));
    if (c.fixed) states.push(c.fixed + ' fixed');
    if (c.skipped && c.skipped.length) states.push('Not finished: ran out of time on ' + c.skipped.join('; '));
    if (c.last_run && !c.open && !c.fixed) states.push(c.found ? '' : 'nothing found');
    const input = c.box ? boxFor(c.id, c.box) : null;
    const go = el('button', {cls: 'act', type: 'button', text: running ? '...' : c.last_run && c.button === 'Run' ? 'Run again' : c.button, disabled: !!running || !c.can_run,
      title: c.can_run ? '' : 'It needs your book read in Manuscript first.', on: {click: () => run(c.id, input)}});
    return el('div', {cls: 'check'}, [
      el('div', {cls: 'title'}, [c.name, c.thin ? el('span', {cls: 'pill', text: 'thin'}) : null]),
      go,
      el('div', {cls: 'says', text: c.says}),
      el('div', {cls: 'state', text: states.filter(Boolean).join('. ')}),
      input,
      el('details', {}, [el('summary', {text: 'How far to trust it'}), el('div', {text: c.trust})]),
    ]);
  }

  function drawStages() {
    const box = clear($('stages'));
    const nextStage = S.v.next && S.v.next.stage;
    S.v.stages.forEach((s, i) => {
      const open = S.opened[s.id] !== undefined ? S.opened[s.id] : s.name === nextStage;
      const tally = s.big_open ? plural(s.big_open, 'big thing') + ' open' : s.ran ? s.ran + ' of ' + s.total + ' run' : '';
      const d = el('details', {cls: 'stage', open: open}, [
        el('summary', {}, [el('span', {cls: 'num', text: (i + 1) + '.'}), el('span', {cls: 'name', text: s.name}), el('span', {cls: 'plain', text: s.plain}), el('span', {cls: 'tally' + (s.big_open ? ' big' : ''), text: tally})]),
        el('p', {cls: 'why', text: s.why}),
      ].concat(s.checks.map(checkRow)));
      d.addEventListener('toggle', () => { S.opened[s.id] = d.open; });
      box.appendChild(d);
    });
  }

  function drawAnytime() {
    const box = clear($('anytime'));
    box.appendChild(el('h2', {text: 'Any time'}));
    for (const c of S.v.anytime) box.appendChild(checkRow(c));
  }

  async function run(id, input) {
    const plan = input ? input.value.trim() : '';
    try {
      await call('api/run', {title: S.book, check: id, plan});
      if (input) S.typed[id] = '';
    } catch (e) {
      clear($('work')).appendChild(el('span', {text: e.message}));
      $('work').hidden = false;
      $('work').className = 'work bad';
      return;
    }
    refresh();
  }

  // ---------------------------------------------------------------- findings

  function drawFilters() {
    const box = clear($('filters'));
    for (const [id, label] of STATE) {
      const n = S.v.findings.filter(f => f.state === id && f.kind !== 'question' && (!S.only || f.check === S.only)).length;
      box.appendChild(el('button', {type: 'button', role: 'tab', 'aria-selected': String(S.filter === id), text: label + ' ' + n,
        on: {click: () => { S.filter = id; S.all = false; drawFilters(); drawList(); }}}));
    }
    const names = {};
    for (const f of S.v.findings) names[f.check] = f.check_name;
    if (Object.keys(names).length > 1) {
      const sel = el('select', {'aria-label': 'Show one check', on: {change: e => { S.only = e.target.value; drawFilters(); drawList(); }}},
        [el('option', {value: '', text: 'Every check'})].concat(Object.keys(names).map(k => el('option', {value: k, text: names[k]}))));
      sel.value = S.only;
      box.appendChild(sel);
    }
  }

  function findingCard(f) {
    const heads = [el('span', {cls: 'size-' + f.size, text: SIZE[f.size] || ''}), el('span', {text: f.check_name}), f.plan ? el('span', {text: 'If: ' + f.plan}) : null,
      f.carried ? el('span', {text: 'from the old desk'}) : null];
    const card = el('article', {cls: 'f' + (f.size === 'big' && f.state === 'open' ? ' big' : ''), id: 'f-' + f.id}, [
      el('div', {cls: 'head'}, heads),
      el('div', {cls: 'what', text: f.what}),
    ]);
    for (const w of f.where || []) {
      if (w.quote) card.appendChild(el('button', {cls: 'q', type: 'button', on: {click: () => openAt(w.section, w.quote)}}, [w.quote, el('small', {text: 'Go to section ' + w.section})]));
      else card.appendChild(el('button', {cls: 'link', type: 'button', text: 'Go to section ' + w.section, on: {click: () => openAt(w.section, '')}}));
    }
    if (f.why) card.appendChild(el('p', {}, [el('span', {cls: 'lab', text: 'Why it matters'}), f.why]));
    if (f.fix) card.appendChild(el('p', {}, [el('span', {cls: 'lab', text: 'One way to fix it'}), f.fix]));
    if (f.still_broken) card.appendChild(el('div', {cls: 'flag', text: 'You marked this Fixed, and your editor still sees it. Have another look.'}));
    if (f.gone) card.appendChild(el('div', {cls: 'flag calm', text: 'Your editor did not find this last time. It stays because you wrote on it.'}));
    if (f.changed) card.appendChild(el('div', {cls: 'flag calm', text: 'This section changed since your editor found this.'}));
    if (f.doubt) card.appendChild(el('div', {cls: 'flag calm', text: 'Your bot thinks the text may explain this: ' + f.doubt + ' You be the judge.'}));
    if (f.kind === 'question' && f.reason) card.appendChild(el('div', {cls: 'flag calm', text: f.reason}));
    if (f.unweighed) card.appendChild(el('div', {cls: 'flag calm', text: 'Found by matching words. Your bot could not weigh it, so check it yourself.'}));
    const marks = el('div', {cls: 'marks'});
    for (const [id, label] of STATE) {
      marks.appendChild(el('button', {type: 'button', cls: (f.state === id ? 'on ' : '') + (id === 'fixed' ? 'fix' : ''), text: label, 'aria-pressed': String(f.state === id), on: {click: () => mark(f, id)}}));
    }
    if (f.can_use && f.state === 'open') marks.appendChild(el('button', {type: 'button', cls: 'main', text: 'Use this', on: {click: () => useThis(f, card)}}));
    card.appendChild(marks);
    const note = el('textarea', {cls: 'mynote', placeholder: 'Your note', 'aria-label': 'Your note', on: {change: e => saveNote(f, e.target.value)}});
    note.value = f.note || '';
    card.appendChild(note);
    return card;
  }

  function drawList() {
    const box = clear($('list'));
    const shown = S.v.findings.filter(f => f.state === S.filter && f.kind !== 'question' && (!S.only || f.check === S.only));
    const asks = S.v.findings.filter(f => f.kind === 'question' && f.state === 'open' && (!S.only || f.check === S.only));
    if (S.filter === 'fixed') {
      const all = S.v.findings.filter(f => f.state !== 'not_a_problem').length;
      if (all) box.appendChild(el('p', {cls: 'prog', text: S.v.findings.filter(f => f.state === 'fixed').length + ' fixed of ' + all + ' so far.'}));
    }
    const questions = () => {
      if (asks.length && S.filter === 'open') box.appendChild(el('details', {cls: 'asks'}, [el('summary', {text: 'Questions for you (' + asks.length + '): things that may be fine, not counted as problems'})].concat(asks.map(findingCard))));
    };
    if (!shown.length) {
      questions();
      box.appendChild(el('p', {cls: 'empty', text: {open: S.v.findings.length ? 'Nothing open here.' : 'Nothing yet. Findings appear here as the checks run.', later: 'Nothing put off for later.', fixed: 'Nothing marked fixed yet.', not_a_problem: 'Nothing here.'}[S.filter]}));
      return;
    }
    const most = S.all ? shown.length : 12;              // biggest first; the rest a press away, not a wall
    for (const f of shown.slice(0, most)) box.appendChild(findingCard(f));
    if (shown.length > most) box.appendChild(el('button', {type: 'button', text: 'Show the other ' + (shown.length - most), on: {click: () => { S.all = true; drawList(); }}}));
    questions();
    if (S.v.old_notes) box.appendChild(el('details', {cls: 'fine'}, [el('summary', {text: 'Your notes from the old desk'}), el('div', {cls: 'oldtext', text: S.v.old_notes})]));
  }

  async function mark(f, state) {
    try { await call('api/mark', {title: S.book, id: f.id, state}); } catch (e) { return; }
    refresh();
  }
  async function saveNote(f, note) {
    try { await call('api/mark', {title: S.book, id: f.id, note}); } catch (e) { /* the box keeps what she typed */ }
    f.note = note;
  }
  function showFinding(f) {
    if (!f) return;
    S.filter = f.state;
    S.only = '';
    S.all = true;
    drawFilters(); drawList();
    const n = $('f-' + f.id);
    if (n) n.scrollIntoView({block: 'center'});
  }

  // PROPOSED: Use this, for grammar and proofreading only, after she has seen the before and the after.
  function useThis(f, card) {
    const old = card.querySelector('.ba');
    if (old) { old.remove(); return; }
    const w = f.where[0];
    const box = el('div', {cls: 'ba'}, [
      el('div', {}, [el('span', {cls: 'lab', text: 'Now'}), el('span', {cls: 'was', text: w.quote})]),
      el('div', {}, [el('span', {cls: 'lab', text: 'Would be'}), el('span', {cls: 'now', text: f.correct})]),
    ]);
    const say = el('p', {cls: 'fine', text: 'You can always bring back the old version.'});
    const row = el('div', {cls: 'row'}, [
      el('button', {type: 'button', cls: 'main', text: 'Yes, put it in', on: {click: async () => {
        try {
          const got = await call('api/use', {title: S.book, id: f.id});
          await refresh();
          openAt(got.n, f.correct);
          sayText(got.message);
        } catch (e) { say.textContent = e.message; }
      }}}),
      el('button', {type: 'button', text: 'Not now', on: {click: () => box.remove()}}),
    ]);
    box.append(say, row);
    card.appendChild(box);
  }

  // ---------------------------------------------------------------- documents

  function drawDocs() {
    const box = clear($('docs'));
    const docs = S.v.documents || [];
    if (!docs.length) return;
    box.appendChild(el('h2', {text: 'Documents'}));
    box.appendChild(el('p', {cls: 'fine', text: 'About your book, for you to keep and rewrite. Never part of your manuscript.'}));
    const seen = {};
    const older = [];
    const rowOf = d => {
      const row = el('div', {cls: 'doc'}, [el('button', {cls: 'link', type: 'button', text: d.name, on: {click: () => openDoc(d, row)}}), el('span', {cls: 'when', text: when(d.made_at)})]);
      return row;
    };
    for (const d of docs) {
      if (seen[d.kind]) older.push(d);
      else box.appendChild(rowOf(d));
      seen[d.kind] = true;
    }
    if (older.length) box.appendChild(el('details', {}, [el('summary', {cls: 'fine', text: 'Earlier drafts (' + older.length + ')'})].concat(older.map(rowOf))));
  }
  async function openDoc(d, row) {
    const was = row.nextElementSibling;
    if (was && was.dataset.doc) { was.remove(); return; }
    let got;
    try { got = await call('api/document?' + q({title: S.book, id: d.id})); } catch (e) { return; }
    const wrap = el('div', {'data-doc': d.id});
    if (got.ext === 'docx') {
      wrap.appendChild(el('p', {text: 'A Word file, ready to open and send. It is in your files at: ' + got.where}));
    } else {
      const area = el('textarea', {cls: 'docbox', 'aria-label': d.name});
      area.value = got.text || '';
      const say = el('span', {cls: 'fine'});
      wrap.append(area, el('div', {cls: 'row'}, [el('button', {type: 'button', text: 'Save', on: {click: async () => {
        try { say.textContent = (await call('api/document', {title: S.book, id: d.id, text: area.value})).message; } catch (e) { say.textContent = e.message; }
      }}}), say]), el('p', {cls: 'fine', text: 'In your files at: ' + got.where}));
    }
    row.after(wrap);
  }

  // ---------------------------------------------------------------- the text pane

  function sayText(line, bad) {
    const n = $('saved');
    n.textContent = line || '';
    n.className = 'saved' + (bad ? ' bad' : '');
  }
  function leaveOk() {
    if (!S.dirty) return true;
    return window.confirm('Your changes to this section are not saved. Leave them?');
  }
  function closePane() {
    S.sec = null; S.editing = false; S.dirty = false;
    $('pane').hidden = true;
    $('wrap').classList.remove('open');
  }
  async function openAt(n, quote) {
    if (S.sec && S.sec.n === n && S.editing) { placeInEditor(quote); return; }
    if (!leaveOk()) return;
    S.dirty = false; S.editing = false;
    await loadSection(n, quote);
  }
  async function loadSection(n, quote) {
    let got;
    try { got = await call('api/section?' + q({title: S.book, n})); } catch (e) { sayText(e.message, true); return; }
    S.sec = got;
    $('pane').hidden = false;
    $('wrap').classList.add('open');
    const sel = clear($('sec'));
    for (const s of S.v.sections) sel.appendChild(el('option', {value: s.n, text: s.n + '. ' + s.heading}));
    sel.value = got.n;
    $('unread').hidden = !got.unread;
    sayText('');
    drawReader(quote);
    drawVersions();
    closeOld();
    $('pane').scrollTop = 0;
    const m = $('reader').querySelector('mark');
    if (m) m.scrollIntoView({block: 'center'});
  }
  // the first place the words are, by any spacing and either kind of quote mark
  function findIn(text, quote) {
    if (!quote) return null;
    const folded = s => s.replace(/[‘’]/g, "'").replace(/[“”]/g, '"').replace(/[\u2013\u2014]/g, '-').toLowerCase();
    const pat = folded(quote).trim().split(/\s+/).map(w => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('\\s+');
    const m = new RegExp(pat).exec(folded(text));
    return m ? [m.index, m.index + m[0].length] : null;
  }
  function drawReader(quote) {
    const box = clear($('reader'));
    const text = S.sec.text;
    const at = findIn(text, quote);
    if (at) box.append(text.slice(0, at[0]), el('mark', {text: text.slice(at[0], at[1])}), text.slice(at[1]));
    else box.textContent = text;
    box.hidden = false;
    $('editor').hidden = true;
    $('edit').hidden = false; $('save').hidden = true; $('cancel').hidden = true;
  }
  function startEdit(quote) {
    S.editing = true;
    const ed = $('editor');
    ed.value = S.sec.text;
    ed.hidden = false;
    $('reader').hidden = true;
    $('edit').hidden = true; $('save').hidden = false; $('cancel').hidden = false;
    placeInEditor(quote);
  }
  function placeInEditor(quote) {
    const ed = $('editor');
    ed.focus();
    const at = findIn(ed.value, quote);
    if (at) {
      ed.setSelectionRange(at[0], at[1]);
      ed.scrollTop = Math.max(0, ed.scrollHeight * at[0] / Math.max(1, ed.value.length) - ed.clientHeight / 2);
    }
  }
  async function save() {
    try {
      const got = await call('api/save', {title: S.book, n: S.sec.n, text: $('editor').value, sha: S.sec.sha});
      S.dirty = false; S.editing = false;
      const n = S.sec.n;
      await refresh();
      await loadSection(n, '');
      const t = new Date().toLocaleTimeString(undefined, {hour: 'numeric', minute: '2-digit'});
      sayText(got.changed ? 'Saved at ' + t + '. Your earlier version is kept.' : got.message);
    } catch (e) { sayText(e.message, true); }
  }
  function drawVersions() {
    const vs = S.sec.versions || [];
    const box = $('versions');
    box.hidden = !vs.length;
    $('vcount').textContent = 'Earlier versions (' + vs.length + ')';
    const list = clear($('vlist'));
    for (const x of vs) {
      list.appendChild(el('li', {}, [el('button', {cls: 'link', type: 'button', text: when(x.saved_at), on: {click: () => seeOld(x)}}), el('span', {cls: 'fine', text: plural(x.words, 'word') + (x.by && x.by !== 'you' ? ', before ' + x.by : '')})]));
    }
  }
  async function seeOld(x) {
    let got;
    try { got = await call('api/version?' + q({title: S.book, n: S.sec.n, id: x.id})); } catch (e) { sayText(e.message, true); return; }
    S.old = x;
    $('olderline').textContent = 'As it was before ' + when(x.saved_at) + ':';
    $('oldtext').textContent = got.text;
    $('older').hidden = false;
    $('older').scrollIntoView({block: 'nearest'});
  }
  function closeOld() { $('older').hidden = true; S.old = null; }
  async function restore() {
    if (!S.old || !leaveOk()) return;
    try {
      const got = await call('api/restore', {title: S.book, n: S.sec.n, id: S.old.id, sha: S.sec.sha});
      S.dirty = false;
      const n = S.sec.n;
      await refresh();
      await loadSection(n, '');
      sayText(got.message);
    } catch (e) { sayText(e.message, true); }
  }
  function openManuscript() {
    if (window.parent !== window) window.parent.postMessage({puffpai: 'open', pkg: 'manuscript', query: q({title: S.book})}, '*');
    else location.href = location.origin + '/pkg/manuscript/?' + q({title: S.book});
  }

  // ---------------------------------------------------------------- wiring

  $('book').addEventListener('change', e => pick(e.target.value));
  $('sec').addEventListener('change', e => {
    if (!leaveOk()) { $('sec').value = S.sec.n; return; }
    S.dirty = false; S.editing = false;
    loadSection(Number(e.target.value), '');
  });
  $('edit').addEventListener('click', () => startEdit(($('reader').querySelector('mark') || {}).textContent || ''));
  $('save').addEventListener('click', save);
  $('cancel').addEventListener('click', () => { if (leaveOk()) { S.dirty = false; S.editing = false; drawReader(''); } });
  $('editor').addEventListener('input', () => { S.dirty = true; sayText(''); });
  $('editor').addEventListener('keydown', e => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') { e.preventDefault(); save(); } });
  $('closepane').addEventListener('click', () => { if (leaveOk()) closePane(); });
  $('restore').addEventListener('click', restore);
  $('closeold').addEventListener('click', closeOld);
  $('vaultfree').addEventListener('change', e => { call('api/settings', {vault_free: e.target.checked}).catch(() => {}); });
  call('api/settings').then(g => { $('vaultfree').checked = g.settings.vault_free !== false; }).catch(() => {});

  // What the server says moves on its own while a check runs; she may be typing in a note, so never redraw under her.
  setInterval(() => {
    const st = S.v && S.v.status;
    const live = st && ['running', 'waiting', 'queued'].includes(st.state);
    const typing = document.activeElement && ['TEXTAREA', 'INPUT'].includes(document.activeElement.tagName) && document.activeElement.id !== 'editor';
    if (!typing && (live || document.visibilityState === 'visible')) refresh();
  }, 5000);
  loadBooks();
})();
