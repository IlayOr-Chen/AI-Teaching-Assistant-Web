/**
 * Purpose: Lecturer experience — login/register view and the dashboard (Overview + Live Attendance
 *          tabs with the animated rotating QR countdown ring, live check-in feed, same-network toggle).
 * Inputs:  a root element and callbacks supplied by app.js.
 * Outputs: DOM rendered into the root. Later checkpoints append tabs in `buildLecturerTabs`.
 */

import { api } from './api.js';
import { renderVideoSummaryTab, renderHomeworkTab } from './ai_tools.js';
import { icon } from './icons.js';
import { renderPollsTab, renderCommentsTab } from './polls_comments.js';
import {
    el, mount, toast, button, withLoading, createTabs, copyText, formatTime, pageShell,
} from './ui.js';

/* ------------------------------------------------------------------ */
/* Auth view                                                           */
/* ------------------------------------------------------------------ */

/**
 * Purpose: Render the restricted lecturer sign-in screen (public self-registration is disabled;
 *          lecturer accounts are configured strictly via backend environment configuration).
 * Inputs:  root (HTMLElement); handlers {onAuthenticated(user), onBack()}.
 * Outputs: void.
 */
export function renderLecturerAuth(root, { onAuthenticated, onBack }) {
    const shell = pageShell({
        rightSlot: [button('Back', { cls: 'ghost sm', onclick: onBack })],
    });

    const field = (label, name, type, placeholder, autocomplete) => el('label', { class: 'field' }, [
        label,
        el('input', { name, type, placeholder, autocomplete, required: true }),
    ]);

    const submit = button('Sign in', { cls: 'primary lg block', type: 'submit' });
    const form = el('form', { class: 'stack', autocomplete: 'on' }, [
        field('Email', 'email', 'email', 'adminL@gmail.com', 'email'),
        field('Password', 'password', 'password', 'Your password', 'current-password'),
        submit,
    ]);

    form.addEventListener('submit', (event) => {
        event.preventDefault();
        const data = Object.fromEntries(new FormData(form).entries());
        withLoading(submit, async () => {
            const result = await api.post('/api/auth/lecturer/login', data);
            toast(`Welcome, ${result.user.full_name}!`, 'success');
            onAuthenticated(result.user);
        });
    });

    shell.content.append(el('div', { class: 'auth-wrap stack' }, [
        el('div', { class: 'center' }, [
            el('h1', {}, ['Lecturer ', el('span', { class: 'gradient-text' }, 'sign in')]),
            el('p', { class: 'muted' }, 'Run attendance, polls and AI tools for your class.'),
        ]),
        el('div', { class: 'card stack' }, [form]),
    ]));
    mount(root, shell.root);
    form.querySelector('input')?.focus();
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
        { id: 'roster', label: 'Roster & YOLO Headcount', icon: 'table', render: (c) => renderRosterAndHeadcountTab(c, ctx) },
        { id: 'polls', label: 'Live Polls', icon: 'poll', render: (c) => renderPollsTab(c, { ...ctx, role: 'lecturer' }) },
        { id: 'comments', label: 'Comments & Suggestions', icon: 'message', render: (c) => renderCommentsTab(c, { ...ctx, role: 'lecturer' }) },
        { id: 'video', label: 'MP4 Lecture Summary', icon: 'video', render: (c) => renderVideoSummaryTab(c, { ...ctx, role: 'lecturer' }) },
        { id: 'homework', label: 'PDF Homework Review', icon: 'file', render: (c) => renderHomeworkTab(c, { ...ctx, role: 'lecturer' }) },
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
        const bundledBtn = button('Sync from Course Excel', { cls: '', iconName: 'refresh' });

        const runImport = (btn, request) => withLoading(btn, async () => {
            const result = await request();
            toast(`Synced ${result.total_imported} students from Excel (${result.unassigned_group_count} without a group).`, 'success');
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
                : el('p', { class: 'muted' }, `${count} students are loaded from assets/courseid_130158_participants.xlsx. Re-importing updates the roster.`),
            el('div', { class: 'row' }, [bundledBtn, uploadBtn, fileInput]),
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

/**
 * Purpose: Build one centered static session QR display (elevated white frame + QR image + loader).
 *          Used for both the dashboard card and the full-screen projector overlay.
 * Inputs:  none.
 * Outputs: {wrap, img, loading} DOM references.
 */
function buildQrDisplay() {
    const img = el('img', { alt: 'Session attendance QR code', width: 360, height: 360 });
    const loading = el('div', { class: 'qr-loading' }, el('span', { class: 'spinner', style: 'width:28px;height:28px;border-color:#cbd5e1;border-top-color:#334155' }));
    const frame = el('div', { class: 'qr-frame' }, [img, loading]);
    const wrap = el('div', { class: 'qr-ring-wrap' }, [frame]);
    return { wrap, img, loading };
}

/**
 * Purpose: Live attendance tab. Shows a setup form with the interactive Leaflet geofence map
 *          when no session is live, otherwise the centered static session QR, GPS geofencing
 *          toggle, cooldown reset button, live counter and check-in feed.
 * Inputs:  container (HTMLElement); ctx (dashboard context).
 * Outputs: cleanup function stopping polling timers, Leaflet map, and the projector overlay.
 */
function renderLiveAttendanceTab(container, ctx) {
    let disposed = false;
    let stopActive = null; // cleanup for the currently displayed active session
    let stopSetupMap = null; // cleanup for the Leaflet setup map instance

    const cleanup = () => {
        disposed = true;
        if (stopSetupMap) { stopSetupMap(); stopSetupMap = null; }
        if (stopActive) stopActive();
    };

    /**
     * Purpose: Build a reusable "Reset 15-Min Device Cooldowns" button calling the protected endpoint.
     * Inputs:  extraCls (string) optional button CSS modifiers.
     * Outputs: HTMLButtonElement.
     */
    const buildResetCooldownsButton = (extraCls = 'block') => {
        const btn = button('Reset 15-Min Device Cooldowns', { cls: `ghost ${extraCls}`.trim(), iconName: 'refresh' });
        btn.addEventListener('click', () => withLoading(btn, async () => {
            const res = await api.post('/api/attendance/reset-cooldowns');
            toast(res.message || 'All 15-minute device cooldowns have been reset. Students can re-scan immediately.', 'success', 3800);
        }));
        return btn;
    };

    const showSetup = async () => {
        if (stopActive) { stopActive(); stopActive = null; }
        if (stopSetupMap) { stopSetupMap(); stopSetupMap = null; }
        mount(container, el('div', { class: 'card' }, el('span', { class: 'spinner' })));
        let sessions = [];
        try {
            sessions = await api.get('/api/attendance/sessions').then((r) => r.sessions);
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
        const radiusInput = el('input', { type: 'number', min: 30, max: 500, value: 100, required: true, name: 'geofence_radius_meters' });
        const latInput = el('input', { type: 'text', value: '32.104400', placeholder: 'e.g. 32.104400', name: 'classroom_lat' });
        const lngInput = el('input', { type: 'text', value: '35.207800', placeholder: 'e.g. 35.207800', name: 'classroom_lng' });
        const enforce = el('input', { type: 'checkbox', checked: true, name: 'enforce_geofence' });
        const detectGpsBtn = button('Use My Current Location', { cls: 'sm primary', iconName: 'wifi' });
        const presetGpsBtn = button('Ariel Campus preset', { cls: 'sm ghost' });
        const resetCooldownSetupBtn = buildResetCooldownsButton('sm');
        const start = button('Start attendance session', { cls: 'primary lg block', type: 'submit', iconName: 'play' });

        /* ----- Radius selector pills (30m / 50m / 100m) ----- */
        const RADIUS_OPTIONS = [30, 50, 100];
        let mapInstance = null;
        let mapMarker = null;
        let mapCircle = null;

        const pillButtons = RADIUS_OPTIONS.map((r) => {
            const pill = el('button', {
                type: 'button',
                class: `radius-pill ${r === 100 ? 'active' : ''}`.trim(),
                'data-radius': String(r),
            }, `${r}m`);
            pill.addEventListener('click', () => setRadiusMeters(r));
            return pill;
        });

        const setRadiusMeters = (r) => {
            const radiusVal = Math.max(30, Math.min(500, Number(r) || 100));
            radiusInput.value = String(radiusVal);
            for (const pill of pillButtons) {
                pill.classList.toggle('active', Number(pill.dataset.radius) === radiusVal);
            }
            if (mapCircle) {
                mapCircle.setRadius(radiusVal);
            }
        };

        const setPinCoordinates = (lat, lng, recenter = true) => {
            const safeLat = Number(lat);
            const safeLng = Number(lng);
            if (!Number.isFinite(safeLat) || !Number.isFinite(safeLng)) return;
            latInput.value = safeLat.toFixed(6);
            lngInput.value = safeLng.toFixed(6);
            if (mapMarker) mapMarker.setLatLng([safeLat, safeLng]);
            if (mapCircle) mapCircle.setLatLng([safeLat, safeLng]);
            if (mapInstance && recenter) {
                mapInstance.setView([safeLat, safeLng], Math.max(mapInstance.getZoom(), 17));
            }
        };

        radiusInput.addEventListener('input', () => setRadiusMeters(Number(radiusInput.value)));
        const onManualCoordInput = () => {
            const parsedLat = Number(latInput.value.trim());
            const parsedLng = Number(lngInput.value.trim());
            if (Number.isFinite(parsedLat) && Number.isFinite(parsedLng)) {
                setPinCoordinates(parsedLat, parsedLng, true);
            }
        };
        latInput.addEventListener('change', onManualCoordInput);
        lngInput.addEventListener('change', onManualCoordInput);

        detectGpsBtn.addEventListener('click', () => {
            if (!navigator.geolocation) {
                toast('Browser Geolocation is not available; click on the map or enter coordinates manually.', 'warning');
                return;
            }
            navigator.geolocation.getCurrentPosition(
                (pos) => {
                    setPinCoordinates(pos.coords.latitude, pos.coords.longitude, true);
                    toast(`Captured classroom GPS (${latInput.value}, ${lngInput.value}).`, 'success');
                },
                (err) => {
                    toast(`Could not read browser GPS (${err.message}). Click on the map to place the pin manually.`, 'warning');
                },
                { enableHighAccuracy: true, timeout: 8000 },
            );
        });
        presetGpsBtn.addEventListener('click', () => {
            setPinCoordinates(32.104400, 35.207800, true);
            toast('Applied Ariel University lecture hall coordinates.', 'info', 2200);
        });

        const mapHost = el('div', {
            class: 'geofence-map-container',
            id: 'lecturer-geofence-map',
            'aria-label': 'Interactive classroom geofence map',
        });

        const form = el('form', { class: 'stack' }, [
            el('div', { class: 'grid', style: 'grid-template-columns:1fr 2fr' }, [
                el('label', { class: 'field' }, ['Lesson number', lessonNo]),
                el('label', { class: 'field' }, ['Title', title]),
            ]),
            el('div', { class: 'grid', style: 'grid-template-columns:1fr 1fr 1fr' }, [
                el('label', { class: 'field' }, ['Classroom Lat', latInput]),
                el('label', { class: 'field' }, ['Classroom Lng', lngInput]),
                el('label', { class: 'field' }, ['Geofence Radius (m)', radiusInput]),
            ]),
            el('label', { class: 'switch' }, [
                enforce, el('span', { class: 'track' }),
                el('span', { class: 'switch-text' }, [
                    el('strong', {}, 'Enforce GPS Geofencing'),
                    el('span', { class: 'faint' }, 'Verify student Browser Geolocation via Haversine distance (works across Campus NAT & 4G/5G).'),
                ]),
            ]),
            start,
        ]);

        form.addEventListener('submit', (event) => {
            event.preventDefault();
            const n = Number(lessonNo.value);
            if (!Number.isInteger(n) || n < 1) { toast('Enter a valid lesson number.', 'warning'); return; }
            const parsedLat = latInput.value.trim() ? Number(latInput.value.trim()) : null;
            const parsedLng = lngInput.value.trim() ? Number(lngInput.value.trim()) : null;
            withLoading(start, async () => {
                const session = await api.post('/api/attendance/sessions', {
                    lesson_id: `lesson_${n}`,
                    lesson_title: title.value.trim() || `Lesson ${n}`,
                    classroom_lat: Number.isFinite(parsedLat) ? parsedLat : null,
                    classroom_lng: Number.isFinite(parsedLng) ? parsedLng : null,
                    geofence_radius_meters: Number(radiusInput.value) || 100,
                    enforce_geofence: enforce.checked,
                });
                toast(`${session.lesson_title} is live.`, 'success');
                showActive(session, session.geofence_warning);
            });
        });

        mount(container, el('div', { class: 'grid grid-2' }, [
            el('div', { class: 'card stack' }, [
                el('div', { class: 'card-title' }, [el('span', { html: icon('qr') }), el('h2', {}, 'Start a lesson')]),
                form,
                el('div', { class: 'row', style: 'padding-top:0.4rem;border-top:1px solid var(--border)' }, [
                    resetCooldownSetupBtn,
                ]),
            ]),
            el('div', { class: 'card stack' }, [
                el('div', { class: 'card-title' }, [el('span', { html: icon('shield') }), el('h2', {}, 'Interactive Classroom Geofence Map')]),
                el('div', { class: 'row' }, [
                    detectGpsBtn,
                    presetGpsBtn,
                    el('span', { class: 'spacer' }),
                    el('div', { class: 'radius-pills', role: 'group', 'aria-label': 'Geofence radius selector' }, pillButtons),
                ]),
                mapHost,
                el('p', { class: 'faint' }, 'Click anywhere on the map or drag the pin to position the classroom. The translucent blue/cyan circle previews the exact 30m / 50m / 100m geofence boundary enforced on student phones.'),
                el('div', { class: 'banner info' }, [el('span', { html: icon('info') }), el('span', {}, 'Browser Geolocation requires HTTPS (provided automatically by Cloudflare Tunnel and Render) or localhost.')]),
            ]),
        ]));

        /* ----- Initialize Leaflet.js + OpenStreetMap Map & Live Circle ----- */
        if (typeof window !== 'undefined' && window.L) {
            const L = window.L;
            const initialLat = Number(latInput.value) || 32.104400;
            const initialLng = Number(lngInput.value) || 35.207800;
            const initialRadius = Number(radiusInput.value) || 100;

            mapInstance = L.map(mapHost, {
                center: [initialLat, initialLng],
                zoom: 17,
                zoomControl: true,
            });
            L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
                maxZoom: 19,
                attribution: '&copy; OpenStreetMap contributors',
            }).addTo(mapInstance);

            mapMarker = L.marker([initialLat, initialLng], { draggable: true }).addTo(mapInstance);
            mapCircle = L.circle([initialLat, initialLng], {
                radius: initialRadius,
                color: '#22d3ee',
                fillColor: '#7c8cff',
                fillOpacity: 0.22,
                weight: 2,
            }).addTo(mapInstance);

            mapInstance.on('click', (e) => {
                setPinCoordinates(e.latlng.lat, e.latlng.lng, false);
            });
            mapMarker.on('drag', (e) => {
                const pos = e.target.getLatLng();
                if (mapCircle) mapCircle.setLatLng(pos);
                latInput.value = pos.lat.toFixed(6);
                lngInput.value = pos.lng.toFixed(6);
            });
            mapMarker.on('dragend', (e) => {
                const pos = e.target.getLatLng();
                setPinCoordinates(pos.lat, pos.lng, false);
            });

            // Important Leaflet fix: call map.invalidateSize() whenever the tab/panel becomes visible
            // so OpenStreetMap tiles never render half-gray.
            const refreshMapSize = () => {
                if (mapInstance && !disposed) {
                    mapInstance.invalidateSize();
                }
            };
            requestAnimationFrame(refreshMapSize);
            const t1 = setTimeout(refreshMapSize, 120);
            const t2 = setTimeout(refreshMapSize, 350);
            let resizeObs = null;
            if (typeof ResizeObserver !== 'undefined') {
                resizeObs = new ResizeObserver(() => refreshMapSize());
                resizeObs.observe(mapHost);
            }

            stopSetupMap = () => {
                clearTimeout(t1);
                clearTimeout(t2);
                if (resizeObs) resizeObs.disconnect();
                if (mapInstance) {
                    mapInstance.remove();
                    mapInstance = null;
                }
            };
        }
    };

    /**
     * Purpose: Render the live session view with the centered static session QR code and live feed polling.
     * Inputs:  session (object) active attendance session; ipWarning (string | null).
     * Outputs: void (registers cleanup in `stopActive`).
     */
    const showActive = (session, ipWarning) => {
        if (stopSetupMap) { stopSetupMap(); stopSetupMap = null; }
        if (stopActive) stopActive();
        const lessonId = session.lesson_id;
        const mainQr = buildQrDisplay();
        const projectorQr = buildQrDisplay();
        const displays = [mainQr, projectorQr];

        const urlCode = el('code', {}, 'Generating link…');
        let currentUrl = '';
        const copyBtn = button('Copy Link', { cls: 'sm', iconName: 'copy', onclick: async () => {
            if (!currentUrl) return;
            toast((await copyText(currentUrl)) ? 'Check-in link copied.' : 'Could not copy the link.', 'info', 2200);
        } });

        const overlay = el('div', { class: 'projector-overlay', role: 'dialog', 'aria-label': 'Projector QR' }, [
            button('Close', { cls: 'projector-close', iconName: 'x', onclick: () => setProjector(false) }),
            el('h2', {}, session.lesson_title || lessonId),
            projectorQr.wrap,
            el('p', { class: 'muted' }, 'Scan with your phone camera to check in — protected by GPS Geofencing & 15-min device cooldown.'),
        ]);
        document.body.append(overlay);
        const setProjector = (open) => overlay.classList.toggle('open', open);
        const onKey = (e) => { if (e.key === 'Escape') setProjector(false); };
        document.addEventListener('keydown', onKey);

        /* ----- Load static session QR code once ----- */
        const fetchQr = async () => {
            try {
                const data = await api.get(`/api/attendance/sessions/${encodeURIComponent(lessonId)}/qr`);
                if (disposed) return;
                currentUrl = data.checkin_url;
                urlCode.textContent = data.checkin_url;
                for (const d of displays) {
                    d.img.src = data.qr_image_data_url;
                    d.loading.classList.add('hidden');
                    d.img.classList.remove('swap');
                    void d.img.offsetWidth;
                    d.img.classList.add('swap');
                }
            } catch (err) {
                if (err.status === 401) { toast('Your session expired. Please sign in again.', 'error'); stop(); return; }
                if (err.status === 400) { toast(err.message, 'warning'); stop(); showSetup(); return; }
                toast(err.message, 'error');
            }
        };
        fetchQr();

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
            clearInterval(pollId);
            document.removeEventListener('keydown', onKey);
            overlay.remove();
            stopActive = null;
        }
        stopActive = stop;

        /* ----- Controls ----- */
        const enforceInput = el('input', { type: 'checkbox', checked: session.enforce_geofence !== false });
        enforceInput.addEventListener('change', async () => {
            const wanted = enforceInput.checked;
            try {
                await api.patch(`/api/attendance/sessions/${encodeURIComponent(lessonId)}/geofence-toggle`, { enforce_geofence: wanted });
                toast(wanted ? 'GPS Geofencing is ON.' : 'GPS Geofencing is OFF — students without location permission can check in.', wanted ? 'success' : 'warning');
            } catch (err) {
                enforceInput.checked = !wanted; // revert the switch on failure
                toast(err.message, 'error');
            }
        });

        const resetCooldownActiveBtn = buildResetCooldownsButton('block');

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

        const coordsLabel = (session.classroom_lat !== null && session.classroom_lat !== undefined)
            ? `Radius: ${session.geofence_radius_meters || 100}m around (${session.classroom_lat}, ${session.classroom_lng})`
            : `Radius: ${session.geofence_radius_meters || 100}m`;

        mount(container, el('div', { class: 'grid grid-2' }, [
            el('div', { class: 'card qr-stage' }, [
                el('div', { class: 'row', style: 'width:100%;justify-content:space-between' }, [
                    el('div', { class: 'row' }, [
                        el('h2', {}, session.lesson_title || lessonId),
                        el('span', { class: 'chip ok live' }, [el('span', { class: 'dot' }), 'LIVE']),
                    ]),
                    button('Projector mode', { cls: 'sm', iconName: 'maximize', onclick: () => setProjector(true) }),
                ]),
                mainQr.wrap,
                el('div', { class: 'qr-url' }, [urlCode, copyBtn]),
                el('p', { class: 'faint qr-helper-text' }, 'Scan to check in — protected by GPS Geofencing & 15-min device cooldown.'),
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
                    el('div', { class: 'card-title' }, [el('span', { html: icon('shield') }), el('h2', {}, 'Controls')]),
                    el('label', { class: 'switch' }, [
                        enforceInput, el('span', { class: 'track' }),
                        el('span', { class: 'switch-text' }, [el('strong', {}, 'Enforce GPS Geofencing'), el('span', { class: 'faint' }, coordsLabel)]),
                    ]),
                    resetCooldownActiveBtn,
                    closeBtn,
                ]),
            ]),
        ]));
    };

    showSetup();
    return cleanup;
}

/* ------------------------------------------------------------------ */
/* Roster, Attendance Management & YOLO Headcount tab (Checkpoint 6)  */
/* ------------------------------------------------------------------ */

/**
 * Purpose: Render the full Attendance Management & YOLO Classroom Headcount tab:
 *          1) Lesson selector, search/group/status filters, and CSV / Excel (.xlsx) export buttons,
 *          2) YOLO classroom photo upload + one-click `/assets/p3.jpeg` test + side-by-side
 *             Detected vs. Self-Registered counts + visual GREEN / ORANGE / RED confidence card
 *             + annotated bounding-box photo display,
 *          3) Complete student roster table (Full Name, Email, Group, Status, Method, Timestamp)
 *             with one-click manual Present/Absent toggle per student.
 * Inputs:  container (HTMLElement); ctx (dashboard context).
 * Outputs: void.
 */
function renderRosterAndHeadcountTab(container, ctx) {
    let activeLessonId = 'lesson_1';
    let reportData = null;
    let latestYoloReport = null;

    const lessonSelect = el('select', { name: 'lesson_select', 'aria-label': 'Select lesson' });
    const searchInput = el('input', { type: 'search', placeholder: 'Search name or email…', 'aria-label': 'Search student' });
    const groupSelect = el('select', { 'aria-label': 'Filter by group' }, [
        el('option', { value: '' }, 'All groups'),
    ]);
    const statusSelect = el('select', { 'aria-label': 'Filter by attendance status' }, [
        el('option', { value: '' }, 'All statuses'),
        el('option', { value: 'present' }, 'Present only'),
        el('option', { value: 'absent' }, 'Absent only'),
    ]);

    const presentStat = statCard('Self-registered present');
    const absentStat = statCard('Absent');
    const yoloStat = statCard('YOLO photo headcount');
    const statusStat = statCard('Confidence indicator', { small: true });

    const yoloResultHost = el('div', { class: 'stack' });
    const tableBody = el('tbody');
    const tableSummary = el('span', { class: 'faint' }, 'Loading roster…');

    /**
     * Purpose: Trigger a browser download of the CSV or Excel (.xlsx) attendance export.
     * Inputs:  format ('csv' | 'xlsx').
     * Outputs: void.
     */
    const downloadExport = (format) => {
        const url = `/api/attendance/sessions/${encodeURIComponent(activeLessonId)}/export?format=${encodeURIComponent(format)}`;
        const link = el('a', { href: url, download: `attendance_${activeLessonId}.${format}`, class: 'hidden' });
        document.body.append(link);
        link.click();
        link.remove();
        toast(`Downloading ${format.toUpperCase()} attendance spreadsheet for ${activeLessonId}…`, 'info', 2600);
    };

    /**
     * Purpose: Render the YOLO confidence indicator card (GREEN / ORANGE / RED) and
     *          side-by-side comparison between YOLO Detected Count and Self-Registered Count.
     * Inputs:  yolo (object | null) YOLO headcount report.
     * Outputs: void.
     */
    const renderYoloPanel = (yolo) => {
        const regCount = reportData ? reportData.present_count : 0;
        if (!yolo) {
            yoloStat.set('—', 'Upload photo or test p3.jpeg');
            statusStat.set('Not run yet', 'Run YOLO headcount below');
            mount(yoloResultHost, el('div', { class: 'empty' },
                'Upload a classroom photo or click "Test on Classroom Photo (p3.jpeg)" to compare physical headcount against self-registered attendance.'));
            return;
        }

        const color = yolo.status_color || 'GREEN';
        const diff = yolo.discrepancy !== null && yolo.discrepancy !== undefined
            ? (yolo.discrepancy > 0 ? `+${yolo.discrepancy}` : String(yolo.discrepancy))
            : String(yolo.detected_count - regCount);

        yoloStat.set(String(yolo.detected_count), `Avg conf ${(yolo.average_confidence * 100).toFixed(1)}%`);
        statusStat.set(color, yolo.status_label || '');

        mount(yoloResultHost, [
            el('div', { class: `yolo-status-card status-${color}` }, [
                el('div', { class: 'row' }, [
                    el('span', { class: `yolo-badge ${color}` }, `${color} · ${yolo.status_label || 'Confidence Assessment'}`),
                    el('span', { class: 'spacer' }),
                    el('span', { class: 'faint' }, `High (>=0.60): ${yolo.high_confidence_count ?? 0} · Low (<0.45): ${yolo.low_confidence_count ?? 0}`),
                ]),
                el('p', {}, yolo.status_reason || ''),
            ]),
            el('div', { class: 'yolo-compare-grid' }, [
                el('div', { class: 'card stat' }, [
                    el('div', { class: 'stat-label' }, 'YOLO Detected Count'),
                    el('div', { class: 'stat-value' }, String(yolo.detected_count)),
                    el('div', { class: 'stat-sub' }, 'Physical persons in photo'),
                ]),
                el('div', { class: 'card stat' }, [
                    el('div', { class: 'stat-label' }, 'Self-Registered Count'),
                    el('div', { class: 'stat-value' }, String(yolo.registered_count ?? regCount)),
                    el('div', { class: 'stat-sub' }, `Checked in (${activeLessonId})`),
                ]),
                el('div', { class: 'card stat' }, [
                    el('div', { class: 'stat-label' }, 'Count Discrepancy'),
                    el('div', { class: 'stat-value' }, diff),
                    el('div', { class: 'stat-sub' }, 'Detected minus registered'),
                ]),
            ]),
            yolo.annotated_image_data_url
                ? el('div', { class: 'yolo-photo-frame' }, [
                    el('img', { src: yolo.annotated_image_data_url, alt: 'Annotated YOLO classroom headcount with bounding boxes and confidence scores' }),
                ])
                : null,
        ]);
    };

    /**
     * Purpose: Render the filtered student roster table rows with manual attendance toggle buttons.
     * Inputs:  none (reads `reportData` and filter inputs).
     * Outputs: void.
     */
    const renderTableRows = () => {
        if (!reportData) return;
        const q = searchInput.value.trim().toLowerCase();
        const gFilter = groupSelect.value;
        const sFilter = statusSelect.value;

        const allRows = reportData.roster_status || [];
        const filtered = allRows.filter((row) => {
            if (q) {
                const matchName = (row.full_name || '').toLowerCase().includes(q);
                const matchEmail = (row.email || '').toLowerCase().includes(q);
                if (!matchName && !matchEmail) return false;
            }
            if (gFilter === '__unassigned__') {
                if (row.group) return false;
            } else if (gFilter && String(row.group) !== gFilter) {
                return false;
            }
            if (sFilter && row.status !== sFilter) return false;
            return true;
        });

        tableSummary.textContent = `Showing ${filtered.length} of ${allRows.length} students for ${activeLessonId}`;

        if (!filtered.length) {
            mount(tableBody, el('tr', {}, el('td', { colspan: '8', class: 'center muted', style: 'padding:1.5rem' },
                allRows.length === 0
                    ? 'No students imported yet. Import the class Excel roster from the Overview tab.'
                    : 'No students match the current search/filter criteria.')));
            return;
        }

        const trs = filtered.map((row, index) => {
            const isPresent = row.status === 'present';
            const toggleBtn = button(isPresent ? 'Mark Absent' : 'Mark Present', {
                cls: `sm ${isPresent ? 'ghost' : 'primary'}`,
                iconName: isPresent ? 'x' : 'check',
            });
            toggleBtn.addEventListener('click', () => withLoading(toggleBtn, async () => {
                const nextStatus = isPresent ? 'absent' : 'present';
                await api.post(`/api/attendance/sessions/${encodeURIComponent(activeLessonId)}/manual`, {
                    student_id: row.student_id,
                    status: nextStatus,
                });
                toast(`${row.full_name} marked ${nextStatus.toUpperCase()}.`, 'success', 2400);
                await loadLessonReport(activeLessonId);
            }));

            const geoText = row.distance_meters !== null && row.distance_meters !== undefined
                ? `${Math.round(row.distance_meters)}m (${row.geofence_status})`
                : (row.geofence_status || '-');

            return el('tr', {}, [
                el('td', { class: 'faint' }, String(index + 1)),
                el('td', { dir: 'auto' }, el('strong', {}, row.full_name)),
                el('td', { class: 'muted' }, row.email),
                el('td', {}, row.group ? el('span', { class: 'chip' }, `Group ${row.group}`) : el('span', { class: 'faint' }, 'Unassigned')),
                el('td', {}, el('span', { class: `chip ${isPresent ? 'ok' : 'danger'}` }, [
                    el('span', { class: 'dot' }),
                    isPresent ? 'Present' : 'Absent',
                ])),
                el('td', { class: 'faint' }, row.method || '-'),
                el('td', { class: 'faint' }, geoText),
                el('td', { class: 'faint' }, formatTime(row.checked_in_at)),
                el('td', {}, toggleBtn),
            ]);
        });
        mount(tableBody, trs);
    };

    /**
     * Purpose: Populate the group filter dropdown dynamically from the roster groups.
     * Inputs:  rows (array) roster status entries.
     * Outputs: void.
     */
    const updateGroupFilterOptions = (rows) => {
        const current = groupSelect.value;
        const groups = Array.from(new Set(rows.map((r) => r.group).filter(Boolean))).sort();
        const opts = [
            el('option', { value: '' }, 'All groups'),
            ...groups.map((g) => el('option', { value: g }, `Group ${g}`)),
            el('option', { value: '__unassigned__' }, 'Unassigned group'),
        ];
        mount(groupSelect, opts);
        groupSelect.value = current;
    };

    /**
     * Purpose: Load the attendance report for `lessonId` and refresh stats, YOLO comparison, and table.
     * Inputs:  lessonId (string).
     * Outputs: Promise<void>.
     */
    const loadLessonReport = async (lessonId) => {
        activeLessonId = lessonId;
        reportData = await api.get(`/api/attendance/sessions/${encodeURIComponent(lessonId)}`);
        presentStat.set(String(reportData.present_count), `of ${reportData.total_students} roster students`);
        absentStat.set(String(reportData.absent_count), reportData.session?.active ? 'Session is LIVE' : 'Session inactive/closed');

        updateGroupFilterOptions(reportData.roster_status || []);
        renderTableRows();

        const storedYolo = reportData.session?.yolo_headcount || null;
        if (latestYoloReport && latestYoloReport.lesson_id === lessonId) {
            renderYoloPanel(latestYoloReport);
        } else {
            renderYoloPanel(storedYolo);
        }
    };

    /**
     * Purpose: Load available lessons and initialize the lesson selector dropdown.
     * Inputs:  none.
     * Outputs: Promise<void>.
     */
    const initLessons = async () => {
        try {
            const { sessions } = await api.get('/api/attendance/sessions');
            const ids = Array.from(new Set([
                ...sessions.map((s) => s.lesson_id),
                'lesson_1', 'lesson_2', 'lesson_3',
            ]));
            const live = sessions.find((s) => s.active);
            if (live) activeLessonId = live.lesson_id;
            mount(lessonSelect, ids.map((id) => {
                const match = sessions.find((s) => s.lesson_id === id);
                const label = match ? `${match.lesson_title} (${id})${match.active ? ' · LIVE' : ''}` : id;
                return el('option', { value: id, selected: id === activeLessonId }, label);
            }));
            await loadLessonReport(activeLessonId);
        } catch (err) {
            toast(err.message, 'error');
        }
    };

    lessonSelect.addEventListener('change', () => loadLessonReport(lessonSelect.value));
    searchInput.addEventListener('input', renderTableRows);
    groupSelect.addEventListener('change', renderTableRows);
    statusSelect.addEventListener('change', renderTableRows);

    /* ----- YOLO Classroom Photo Upload & Sample Test Controls ----- */
    const photoInput = el('input', { type: 'file', accept: '.jpg,.jpeg,.png,.webp', class: 'hidden' });
    const uploadPhotoBtn = button('Upload classroom photo', { cls: 'primary', iconName: 'camera' });
    const samplePhotoBtn = button('Test on Classroom Photo (p3.jpeg)', { cls: '', iconName: 'users' });

    uploadPhotoBtn.addEventListener('click', () => photoInput.click());
    photoInput.addEventListener('change', () => {
        const file = photoInput.files[0];
        if (!file) return;
        const form = new FormData();
        form.append('file', file);
        withLoading(uploadPhotoBtn, async () => {
            const url = `/api/attendance/headcount?lesson_id=${encodeURIComponent(activeLessonId)}`;
            latestYoloReport = await api.upload(url, form);
            renderYoloPanel(latestYoloReport);
            toast(`YOLO detected ${latestYoloReport.detected_count} persons (${latestYoloReport.status_color}).`, 'success');
        });
        photoInput.value = '';
    });

    samplePhotoBtn.addEventListener('click', () => withLoading(samplePhotoBtn, async () => {
        const url = `/api/attendance/headcount?use_default_asset=true&lesson_id=${encodeURIComponent(activeLessonId)}`;
        latestYoloReport = await api.post(url);
        renderYoloPanel(latestYoloReport);
        toast(`YOLO detected ${latestYoloReport.detected_count} persons on p3.jpeg (${latestYoloReport.status_color}).`, 'success');
    }));

    const syncRosterBtn = button('Sync from Course Excel', { cls: 'sm', iconName: 'refresh' });
    syncRosterBtn.addEventListener('click', () => withLoading(syncRosterBtn, async () => {
        const result = await api.post('/api/roster/import?use_default_asset=true');
        toast(`Synced ${result.total_imported} students from courseid_130158_participants.xlsx.`, 'success');
        await loadLessonReport(activeLessonId);
    }));

    mount(container, el('div', { class: 'stack' }, [
        el('div', { class: 'grid grid-stats' }, [presentStat.node, absentStat.node, yoloStat.node, statusStat.node]),
        el('div', { class: 'card stack' }, [
            el('div', { class: 'row' }, [
                el('div', { class: 'card-title', style: 'margin-bottom:0' }, [
                    el('span', { html: icon('camera') }),
                    el('h2', {}, 'YOLO Classroom Headcount & Confidence Verification'),
                ]),
                el('span', { class: 'spacer' }),
                uploadPhotoBtn,
                samplePhotoBtn,
                photoInput,
            ]),
            el('p', { class: 'muted' }, 'Runs local Ultralytics YOLO person detection, annotates bounding boxes with confidence scores, and flags discrepancies (GREEN / ORANGE / RED) against self-registered attendance.'),
            yoloResultHost,
        ]),
        el('div', { class: 'card stack' }, [
            el('div', { class: 'row' }, [
                el('div', { class: 'card-title', style: 'margin-bottom:0' }, [
                    el('span', { html: icon('table') }),
                    el('h2', {}, 'Student Roster & Per-Lesson Attendance Management'),
                ]),
                el('span', { class: 'spacer' }),
                syncRosterBtn,
                button('Export CSV (.csv)', { cls: 'sm', iconName: 'download', onclick: () => downloadExport('csv') }),
                button('Export Excel (.xlsx)', { cls: 'sm primary', iconName: 'download', onclick: () => downloadExport('xlsx') }),
            ]),
            el('div', { class: 'toolbar-grid' }, [
                el('label', { class: 'field' }, ['Lesson session', lessonSelect]),
                el('label', { class: 'field' }, ['Search student', searchInput]),
                el('label', { class: 'field' }, ['Group filter', groupSelect]),
                el('label', { class: 'field' }, ['Attendance status', statusSelect]),
            ]),
            el('div', { class: 'row' }, [tableSummary]),
            el('div', { class: 'table-wrap' }, [
                el('table', { class: 'data' }, [
                    el('thead', {}, el('tr', {}, [
                        el('th', {}, '#'),
                        el('th', {}, 'Full Name'),
                        el('th', {}, 'Email'),
                        el('th', {}, 'Group'),
                        el('th', {}, 'Status'),
                        el('th', {}, 'Method'),
                        el('th', {}, 'GPS Geofence'),
                        el('th', {}, 'Checked In At'),
                        el('th', {}, 'Manual Action'),
                    ])),
                    tableBody,
                ]),
            ]),
        ]),
    ]));

    initLessons();
}
