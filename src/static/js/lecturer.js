/**
 * Purpose: Lecturer experience — login/register view and the dashboard (Overview + Live Attendance
 *          tabs with the animated rotating QR countdown ring, live check-in feed, same-network toggle).
 * Inputs:  a root element and callbacks supplied by app.js.
 * Outputs: DOM rendered into the root. Later checkpoints append tabs in `buildLecturerTabs`.
 */

import { api } from './api.js';
import { icon } from './icons.js';
import {
    el, mount, toast, button, withLoading, createTabs, copyText, formatTime, pageShell,
} from './ui.js';

/* ------------------------------------------------------------------ */
/* Auth view                                                           */
/* ------------------------------------------------------------------ */

/**
 * Purpose: Render the lecturer sign-in / registration screen (registration needs the access code).
 * Inputs:  root (HTMLElement); handlers {onAuthenticated(user), onBack()}.
 * Outputs: void.
 */
export function renderLecturerAuth(root, { onAuthenticated, onBack }) {
    let mode = 'login';
    const shell = pageShell({
        rightSlot: [button('Back', { cls: 'ghost sm', onclick: onBack })],
    });
    const formHost = el('div', { class: 'stack' });

    const field = (label, name, type, placeholder, autocomplete) => el('label', { class: 'field' }, [
        label,
        el('input', { name, type, placeholder, autocomplete, required: true }),
    ]);

    const seg = el('div', { class: 'segmented', role: 'group', 'aria-label': 'Sign in or register' });
    const loginBtn = el('button', { type: 'button', 'aria-pressed': 'true', onclick: () => setMode('login') }, 'Sign in');
    const registerBtn = el('button', { type: 'button', 'aria-pressed': 'false', onclick: () => setMode('register') }, 'Register');
    seg.append(loginBtn, registerBtn);

    const renderForm = () => {
        const isRegister = mode === 'register';
        const submit = button(isRegister ? 'Create lecturer account' : 'Sign in', { cls: 'primary lg block', type: 'submit' });
        const form = el('form', { class: 'stack', autocomplete: 'on' }, [
            isRegister ? field('Full name', 'full_name', 'text', 'Dr. Jane Cohen', 'name') : null,
            field('Email', 'email', 'email', 'you@university.ac.il', 'email'),
            field('Password', 'password', 'password', isRegister ? 'At least 6 characters' : 'Your password',
                isRegister ? 'new-password' : 'current-password'),
            isRegister ? field('Lecturer access code', 'access_code', 'password', 'Provided by the course admin', 'off') : null,
            submit,
        ]);
        form.addEventListener('submit', (event) => {
            event.preventDefault();
            const data = Object.fromEntries(new FormData(form).entries());
            withLoading(submit, async () => {
                const result = await api.post(
                    isRegister ? '/api/auth/lecturer/register' : '/api/auth/lecturer/login',
                    data,
                );
                toast(`Welcome, ${result.user.full_name}!`, 'success');
                onAuthenticated(result.user);
            });
        });
        mount(formHost, form);
        form.querySelector('input')?.focus();
    };

    function setMode(next) {
        mode = next;
        loginBtn.setAttribute('aria-pressed', String(mode === 'login'));
        registerBtn.setAttribute('aria-pressed', String(mode === 'register'));
        renderForm();
    }

    shell.content.append(el('div', { class: 'auth-wrap stack' }, [
        el('div', { class: 'center' }, [
            el('h1', {}, ['Lecturer ', el('span', { class: 'gradient-text' }, 'sign in')]),
            el('p', { class: 'muted' }, 'Run attendance, polls and AI tools for your class.'),
        ]),
        el('div', { class: 'card stack' }, [seg, formHost]),
    ]));
    mount(root, shell.root);
    renderForm();
}

/* ------------------------------------------------------------------ */
/* Dashboard                                                           */
/* ------------------------------------------------------------------ */

/**
 * Purpose: Render the lecturer dashboard shell with the animated tab bar.
 * Inputs:  root (HTMLElement); ctx {user, onLogout()}.
 * Outputs: void.
 */
export function renderLecturerDashboard(root, ctx) {
    const logout = button('Sign out', { cls: 'ghost sm', iconName: 'logout', onclick: ctx.onLogout });
    const shell = pageShell({
        rightSlot: [
            el('span', { class: 'chip' }, [el('span', { html: icon('user') }), ctx.user.full_name || ctx.user.email]),
            logout,
        ],
    });

    const tabs = createTabs(buildLecturerTabs(ctx), { initialId: 'overview' });
    shell.content.append(tabs.bar, tabs.panel);
    mount(root, shell.root);
    return () => tabs.destroy();
}

/**
 * Purpose: Tab registry for the lecturer dashboard. Later checkpoints (roster/attendance
 *          management, polls, comments, video summary, homework) append entries here.
 * Inputs:  ctx (object) shared dashboard context.
 * Outputs: array of {id, label, icon, render(container) -> cleanup?}.
 */
function buildLecturerTabs(ctx) {
    return [
        { id: 'overview', label: 'Overview', icon: 'grid', render: (c) => renderOverviewTab(c, ctx) },
        { id: 'attendance', label: 'Live attendance', icon: 'qr', render: (c) => renderLiveAttendanceTab(c, ctx) },
    ];
}

/* ------------------------------------------------------------------ */
/* Overview tab                                                        */
/* ------------------------------------------------------------------ */

/**
 * Purpose: Build one statistic card.
 * Inputs:  label (string), value (string | number), sub (string), small (boolean).
 * Outputs: HTMLElement plus helpers to update the value later.
 */
function statCard(label, { small = false } = {}) {
    const value = el('div', { class: `stat-value ${small ? 'small' : ''} skeleton` }, '0000');
    const sub = el('div', { class: 'stat-sub' }, '');
    const node = el('div', { class: 'card stat hoverable' }, [el('div', { class: 'stat-label' }, label), value, sub]);
    return {
        node,
        set(text, subText = '') {
            value.classList.remove('skeleton');
            value.textContent = text;
            sub.textContent = subText;
        },
    };
}

/**
 * Purpose: Overview tab — system stats, roster import and quick-start guide.
 * Inputs:  container (HTMLElement); ctx (dashboard context).
 * Outputs: void.
 */
function renderOverviewTab(container, ctx) {
    const rosterStat = statCard('Roster students');
    const sessionStat = statCard('Attendance sessions');
    const ipStat = statCard('Your network IP', { small: true });
    const dbStat = statCard('Database', { small: true });
    const importHost = el('div', { class: 'stack' });

    const refresh = async () => {
        const [health, net, roster, sessions] = await Promise.all([
            api.get('/api/health'),
            api.get('/api/network-info'),
            api.get('/api/roster/students'),
            api.get('/api/attendance/sessions'),
        ]);
        const unassigned = roster.students.filter((s) => !s.group).length;
        rosterStat.set(String(roster.count), roster.count ? `${unassigned} without a group` : 'Import your class below');
        sessionStat.set(String(sessions.count), sessions.sessions.some((s) => s.active) ? 'One is live now' : 'None live');
        ipStat.set(net.ip, `detected via ${net.source}`);
        dbStat.set(health.firebase_mode === 'live' ? 'Firebase RTDB' : 'In-memory (mock)', health.firebase_mode === 'live' ? 'Live data' : 'Test data only');
        renderImport(roster.count);
    };

    const renderImport = (count) => {
        const fileInput = el('input', { type: 'file', accept: '.xlsx', class: 'hidden' });
        const uploadBtn = button('Upload roster (.xlsx)', { cls: 'primary', iconName: 'upload' });
        const bundledBtn = button('Use bundled course roster', { cls: '' });

        const runImport = (btn, request) => withLoading(btn, async () => {
            const result = await request();
            toast(`Imported ${result.total_imported} students (${result.unassigned_group_count} without a group).`, 'success');
            await refresh();
        });

        uploadBtn.addEventListener('click', () => fileInput.click());
        fileInput.addEventListener('change', () => {
            const file = fileInput.files[0];
            if (!file) return;
            const form = new FormData();
            form.append('file', file);
            runImport(uploadBtn, () => api.upload('/api/roster/import', form));
            fileInput.value = '';
        });
        bundledBtn.addEventListener('click', () => runImport(bundledBtn, () => api.post('/api/roster/import?use_default_asset=true')));

        mount(importHost, [
            count === 0
                ? el('div', { class: 'banner warn' }, [el('span', { html: icon('alert') }), el('span', {}, 'No students yet. Import the course roster so students can check in by name.')])
                : el('p', { class: 'muted' }, `${count} students are loaded. Re-importing replaces the roster.`),
            el('div', { class: 'row' }, [uploadBtn, bundledBtn, fileInput]),
            el('p', { class: 'faint' }, 'Expected columns: שם פרטי, שם משפחה, דוא"ל, קבוצות (empty group cells are fine).'),
        ]);
    };

    renderImport(-1);
    refresh().catch((err) => toast(err.message, 'error'));

    const steps = ['Import the class roster', 'Open Live attendance and start a lesson', 'Show the QR (use Projector mode) and let students scan']
        .map((text, i) => el('li', { class: 'row' }, [el('span', { class: 'chip ok' }, String(i + 1)), el('span', {}, text)]));

    mount(container, [
        el('div', { class: 'grid grid-stats' }, [rosterStat.node, sessionStat.node, ipStat.node, dbStat.node]),
        el('div', { class: 'grid grid-2', style: 'margin-top:1rem' }, [
            el('div', { class: 'card stack' }, [
                el('div', { class: 'card-title' }, [el('span', { html: icon('users') }), el('h2', {}, 'Class roster')]),
                importHost,
            ]),
            el('div', { class: 'card stack' }, [
                el('div', { class: 'card-title' }, [el('span', { html: icon('shield') }), el('h2', {}, 'Quick start')]),
                el('ol', { class: 'stack', style: 'list-style:none' }, steps),
            ]),
        ]),
    ]);
}

/* ------------------------------------------------------------------ */
/* Live attendance tab                                                 */
/* ------------------------------------------------------------------ */

const RING_RADIUS = 46;
const RING_LENGTH = 2 * Math.PI * RING_RADIUS;

/**
 * Purpose: Build one QR display (animated countdown ring + QR image + seconds chip).
 *          Used for both the dashboard card and the full-screen projector overlay.
 * Inputs:  none.
 * Outputs: {wrap, img, ring, countdown} DOM references.
 */
function buildQrDisplay() {
    const ring = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    ring.setAttribute('class', 'ring-fg');
    ring.setAttribute('cx', '50');
    ring.setAttribute('cy', '50');
    ring.setAttribute('r', String(RING_RADIUS));
    ring.style.strokeDasharray = String(RING_LENGTH);
    ring.style.strokeDashoffset = String(RING_LENGTH);

    const bg = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    bg.setAttribute('class', 'ring-bg');
    bg.setAttribute('cx', '50');
    bg.setAttribute('cy', '50');
    bg.setAttribute('r', String(RING_RADIUS));

    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('class', 'qr-ring');
    svg.setAttribute('viewBox', '0 0 100 100');
    svg.append(bg, ring);

    const img = el('img', { alt: 'Rotating attendance QR code', width: 400, height: 400 });
    const loading = el('div', { class: 'qr-loading' }, el('span', { class: 'spinner', style: 'width:28px;height:28px;border-color:#cbd5e1;border-top-color:#334155' }));
    const frame = el('div', { class: 'qr-frame' }, [img, loading]);
    const countdown = el('span', { class: 'qr-countdown', 'aria-live': 'off' }, '…');
    const wrap = el('div', { class: 'qr-ring-wrap' }, [svg, frame, countdown]);
    return { wrap, img, ring, countdown, loading };
}

/**
 * Purpose: Live attendance tab. Shows a setup form when no session is live, otherwise the rotating
 *          QR, same-network toggle, live counter and check-in feed.
 * Inputs:  container (HTMLElement); ctx (dashboard context).
 * Outputs: cleanup function stopping timers, animation frames and the projector overlay.
 */
function renderLiveAttendanceTab(container, ctx) {
    let disposed = false;
    let stopActive = null; // cleanup for the currently displayed active session

    const cleanup = () => {
        disposed = true;
        if (stopActive) stopActive();
    };

    const showSetup = async () => {
        if (stopActive) { stopActive(); stopActive = null; }
        mount(container, el('div', { class: 'card' }, el('span', { class: 'spinner' })));
        let sessions = [];
        let net = { ip: '', source: '' };
        try {
            [sessions, net] = await Promise.all([
                api.get('/api/attendance/sessions').then((r) => r.sessions),
                api.get('/api/network-info'),
            ]);
        } catch (err) {
            toast(err.message, 'error');
        }
        if (disposed) return;

        const live = sessions.filter((s) => s.active).sort((a, b) => b.started_at - a.started_at)[0];
        if (live) { showActive(live, null); return; }

        const highest = sessions.reduce((max, s) => {
            const m = /^lesson_(\d+)$/.exec(s.lesson_id || '');
            return m ? Math.max(max, Number(m[1])) : max;
        }, 0);

        const lessonNo = el('input', { type: 'number', min: 1, max: 99, value: highest + 1, required: true, name: 'lesson_no' });
        const title = el('input', { type: 'text', maxlength: 80, placeholder: 'Optional, e.g. Design Patterns', name: 'title' });
        const rotation = el('select', { name: 'rotation' }, [10, 12, 15].map((n) => el('option', { value: n, selected: n === 12 }, `${n} seconds`)));
        const enforce = el('input', { type: 'checkbox', checked: true, name: 'enforce' });
        const start = button('Start attendance session', { cls: 'primary lg block', type: 'submit', iconName: 'play' });

        const form = el('form', { class: 'stack' }, [
            el('div', { class: 'grid', style: 'grid-template-columns:1fr 1fr' }, [
                el('label', { class: 'field' }, ['Lesson number', lessonNo]),
                el('label', { class: 'field' }, ['QR rotates every', rotation]),
            ]),
            el('label', { class: 'field' }, ['Title', title]),
            el('label', { class: 'switch' }, [
                enforce, el('span', { class: 'track' }),
                el('span', { class: 'switch-text' }, [el('strong', {}, 'Enforce same-network IP check'), el('span', { class: 'faint' }, 'Students must be on the classroom network. Turn off if they use 4G/5G.')]),
            ]),
            start,
        ]);

        form.addEventListener('submit', (event) => {
            event.preventDefault();
            const n = Number(lessonNo.value);
            if (!Number.isInteger(n) || n < 1) { toast('Enter a valid lesson number.', 'warning'); return; }
            withLoading(start, async () => {
                const session = await api.post('/api/attendance/sessions', {
                    lesson_id: `lesson_${n}`,
                    lesson_title: title.value.trim() || `Lesson ${n}`,
                    enforce_same_network: enforce.checked,
                    rotation_seconds: Number(rotation.value),
                });
                toast(`${session.lesson_title} is live.`, 'success');
                showActive(session, session.ip_warning);
            });
        });

        const warnLocal = net.ip && /^(127\.|10\.|192\.168\.|::1|172\.(1[6-9]|2\d|3[01])\.)/.test(net.ip);
        mount(container, el('div', { class: 'grid grid-2' }, [
            el('div', { class: 'card stack' }, [
                el('div', { class: 'card-title' }, [el('span', { html: icon('qr') }), el('h2', {}, 'Start a lesson')]),
                form,
            ]),
            el('div', { class: 'card stack' }, [
                el('div', { class: 'card-title' }, [el('span', { html: icon('wifi') }), el('h2', {}, 'Network check')]),
                el('p', { class: 'muted' }, 'The server records the public IP you start the session from and compares it with each student\'s IP.'),
                el('div', { class: 'row' }, [el('span', { class: `chip ${warnLocal ? 'warn' : 'ok'}` }, [el('span', { class: 'dot' }), `Your IP: ${net.ip || 'unknown'}`]), el('span', { class: 'faint' }, net.source ? `via ${net.source}` : '')]),
                warnLocal
                    ? el('div', { class: 'banner warn' }, [el('span', { html: icon('alert') }), el('span', {}, 'This is a local address. Open the dashboard through your public tunnel / Render URL, otherwise phones on the classroom Wi-Fi will not match. Or switch the check off.')])
                    : el('div', { class: 'banner info' }, [el('span', { html: icon('info') }), el('span', {}, 'Looks like a public IP, so students on the same Wi-Fi should match.')]),
            ]),
        ]));
    };

    /**
     * Purpose: Render the live session view and start the QR rotation + polling loops.
     * Inputs:  session (object) active attendance session; ipWarning (string | null).
     * Outputs: void (registers cleanup in `stopActive`).
     */
    const showActive = (session, ipWarning) => {
        if (stopActive) stopActive();
        const lessonId = session.lesson_id;
        const mainQr = buildQrDisplay();
        const projectorQr = buildQrDisplay();
        const displays = [mainQr, projectorQr];

        const urlCode = el('code', {}, 'Generating link…');
        let currentUrl = '';
        const copyBtn = button('Copy', { cls: 'sm', iconName: 'copy', onclick: async () => {
            if (!currentUrl) return;
            toast((await copyText(currentUrl)) ? 'Check-in link copied.' : 'Could not copy the link.', 'info', 2200);
        } });

        const overlay = el('div', { class: 'projector-overlay', role: 'dialog', 'aria-label': 'Projector QR' }, [
            button('Close', { cls: 'projector-close', iconName: 'x', onclick: () => setProjector(false) }),
            el('h2', {}, session.lesson_title || lessonId),
            projectorQr.wrap,
            el('p', { class: 'muted' }, 'Scan with your phone camera to check in'),
        ]);
        document.body.append(overlay);
        const setProjector = (open) => overlay.classList.toggle('open', open);
        const onKey = (e) => { if (e.key === 'Escape') setProjector(false); };
        document.addEventListener('keydown', onKey);

        /* ----- QR rotation loop (requestAnimationFrame) ----- */
        let cycleStart = -Infinity;
        let cycleMs = (session.rotation_seconds || 12) * 1000;
        let fetching = false;
        let rafId = 0;

        const fetchQr = async () => {
            fetching = true;
            try {
                const data = await api.get(`/api/attendance/sessions/${encodeURIComponent(lessonId)}/qr`);
                if (disposed) return;
                cycleStart = performance.now();
                cycleMs = data.ttl_seconds * 1000;
                currentUrl = data.checkin_url;
                urlCode.textContent = data.checkin_url;
                for (const d of displays) {
                    d.img.src = data.qr_image_data_url;
                    d.loading.classList.add('hidden');
                    d.img.classList.remove('swap');
                    void d.img.offsetWidth; // restart the swap animation
                    d.img.classList.add('swap');
                }
            } catch (err) {
                if (err.status === 401) { toast('Your session expired. Please sign in again.', 'error'); stop(); return; }
                if (err.status === 400) { toast(err.message, 'warning'); stop(); showSetup(); return; }
                toast(err.message, 'error');
                cycleStart = performance.now() - cycleMs + 3000; // retry in ~3 seconds
            } finally {
                fetching = false;
            }
        };

        const tick = (now) => {
            const remain = Math.max(0, cycleMs - (now - cycleStart));
            const fraction = Number.isFinite(cycleStart) ? remain / cycleMs : 0;
            const secs = Math.ceil(remain / 1000);
            for (const d of displays) {
                d.ring.style.strokeDashoffset = String(RING_LENGTH * (1 - fraction));
                d.ring.classList.toggle('warn', remain <= 5000 && remain > 2500);
                d.ring.classList.toggle('danger', remain <= 2500);
                d.countdown.textContent = fetching || !Number.isFinite(cycleStart) ? '…' : `${secs}s`;
            }
            if (remain <= 0 && !fetching && !disposed) fetchQr();
            rafId = requestAnimationFrame(tick);
        };
        rafId = requestAnimationFrame(tick);

        /* ----- Live counter + feed ----- */
        const countNode = el('span', { class: 'counter-big' }, '0');
        const totalNode = el('span', { class: 'muted' }, '/ 0 students');
        const bar = el('i');
        const feed = el('div', { class: 'feed' });
        const emptyFeed = el('div', { class: 'empty' }, 'Waiting for the first check-in…');
        feed.append(emptyFeed);
        const feedNodes = new Map();
        let shownCount = 0;

        const animateCount = (to) => {
            const from = shownCount;
            shownCount = to;
            if (from === to) return;
            const t0 = performance.now();
            const step = (t) => {
                const p = Math.min(1, (t - t0) / 500);
                countNode.textContent = String(Math.round(from + (to - from) * p));
                if (p < 1) requestAnimationFrame(step);
            };
            requestAnimationFrame(step);
        };

        const pollReport = async () => {
            if (document.hidden || disposed) return;
            try {
                const report = await api.get(`/api/attendance/sessions/${encodeURIComponent(lessonId)}`);
                if (disposed) return;
                animateCount(report.present_count);
                totalNode.textContent = `/ ${report.total_students} students`;
                bar.style.width = report.total_students ? `${(report.present_count / report.total_students) * 100}%` : '0%';

                const present = report.roster_status
                    .filter((r) => r.status === 'present')
                    .sort((a, b) => (b.checked_in_at || 0) - (a.checked_in_at || 0))
                    .slice(0, 12);
                const wanted = new Set(present.map((r) => r.student_id));
                for (const [id, node] of feedNodes) {
                    if (!wanted.has(id)) { node.remove(); feedNodes.delete(id); }
                }
                for (const row of [...present].reverse()) {
                    if (feedNodes.has(row.student_id)) continue;
                    const node = el('div', { class: 'feed-item' }, [
                        el('span', { class: 'avatar' }, (row.full_name || '?').trim().charAt(0)),
                        el('div', { class: 'meta' }, [
                            el('strong', { dir: 'auto' }, row.full_name),
                            el('span', { class: 'faint' }, `${formatTime(row.checked_in_at)} · ${row.method}`),
                        ]),
                    ]);
                    feedNodes.set(row.student_id, node);
                    emptyFeed.remove();
                    feed.prepend(node);
                }
                if (feedNodes.size === 0 && !feed.contains(emptyFeed)) feed.append(emptyFeed);
            } catch (err) {
                if (err.status === 401) { toast('Your session expired. Please sign in again.', 'error'); stop(); }
            }
        };
        pollReport();
        const pollId = setInterval(pollReport, 3500);

        function stop() {
            cancelAnimationFrame(rafId);
            clearInterval(pollId);
            document.removeEventListener('keydown', onKey);
            overlay.remove();
            stopActive = null;
        }
        stopActive = stop;

        /* ----- Controls ----- */
        const enforceInput = el('input', { type: 'checkbox', checked: session.enforce_same_network !== false });
        enforceInput.addEventListener('change', async () => {
            const wanted = enforceInput.checked;
            try {
                await api.patch(`/api/attendance/sessions/${encodeURIComponent(lessonId)}/network-toggle`, { enforce_same_network: wanted });
                toast(wanted ? 'Same-network check is ON.' : 'Same-network check is OFF — students on 4G/5G can check in.', wanted ? 'success' : 'warning');
            } catch (err) {
                enforceInput.checked = !wanted; // revert the switch on failure
                toast(err.message, 'error');
            }
        });

        let confirmTimer = 0;
        const closeBtn = button('Close session', { cls: 'danger block', iconName: 'stop' });
        closeBtn.addEventListener('click', () => {
            const label = closeBtn.querySelector('.btn-label');
            if (!closeBtn.dataset.armed) {
                closeBtn.dataset.armed = '1';
                label.textContent = 'Click again to confirm';
                confirmTimer = setTimeout(() => { delete closeBtn.dataset.armed; label.textContent = 'Close session'; }, 3000);
                return;
            }
            clearTimeout(confirmTimer);
            withLoading(closeBtn, async () => {
                await api.post(`/api/attendance/sessions/${encodeURIComponent(lessonId)}/close`);
                toast('Session closed. The QR no longer accepts check-ins.', 'info');
                stop();
                showSetup();
            });
        });

        mount(container, el('div', { class: 'grid grid-2' }, [
            el('div', { class: 'card qr-stage' }, [
                el('div', { class: 'row', style: 'width:100%' }, [
                    el('h2', {}, session.lesson_title || lessonId),
                    el('span', { class: 'chip ok live' }, [el('span', { class: 'dot' }), 'LIVE']),
                    el('span', { class: 'spacer' }),
                    button('Projector mode', { cls: 'sm', iconName: 'maximize', onclick: () => setProjector(true) }),
                ]),
                mainQr.wrap,
                el('div', { class: 'qr-url' }, [urlCode, copyBtn]),
                el('p', { class: 'faint' }, 'The code changes automatically; older scans stay valid for a short grace period.'),
            ]),
            el('div', { class: 'stack' }, [
                ipWarning ? el('div', { class: 'banner warn' }, [el('span', { html: icon('alert') }), el('span', {}, ipWarning)]) : null,
                el('div', { class: 'card stack' }, [
                    el('div', { class: 'card-title' }, [el('span', { html: icon('users') }), el('h2', {}, 'Checked in')]),
                    el('div', { class: 'row', style: 'align-items:baseline' }, [countNode, totalNode]),
                    el('div', { class: 'progress' }, bar),
                    feed,
                ]),
                el('div', { class: 'card stack' }, [
                    el('div', { class: 'card-title' }, [el('span', { html: icon('wifi') }), el('h2', {}, 'Controls')]),
                    el('label', { class: 'switch' }, [
                        enforceInput, el('span', { class: 'track' }),
                        el('span', { class: 'switch-text' }, [el('strong', {}, 'Enforce same-network IP check'), el('span', { class: 'faint' }, `Classroom IP: ${session.lecturer_ip || 'unknown'}`)]),
                    ]),
                    closeBtn,
                ]),
            ]),
        ]));
    };

    showSetup();
    return cleanup;
}
