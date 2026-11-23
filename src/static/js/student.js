/**
 * Purpose: Student experience — the mobile QR check-in flow (searchable name picker, result screens
 *          for success / expired QR / wrong network / device cooldown) plus the student sign-in
 *          and portal shell used by later checkpoints (comments, polls).
 * Inputs:  a root element plus callbacks / QR parameters supplied by app.js.
 * Outputs: DOM rendered into the root.
 */

import { api, ApiError } from './api.js';
import { icon } from './icons.js';
import {
    el, mount, toast, button, withLoading, createTabs, formatClock, formatTime, pageShell,
} from './ui.js';

/* ------------------------------------------------------------------ */
/* QR check-in flow (opened by scanning the lecturer's QR)             */
/* ------------------------------------------------------------------ */

/**
 * Purpose: Build a result screen (big animated badge, title, message and actions).
 * Inputs:  kind ('ok' | 'err' | 'wait'); title (string); message (string); extra (Node[]).
 * Outputs: HTMLElement.
 */
function resultView(kind, title, message, extra = []) {
    const iconName = { ok: 'check', err: 'x', wait: 'clock' }[kind];
    return el('div', { class: 'card result' }, [
        el('div', { class: `result-badge ${kind}`, html: icon(iconName) }),
        el('h2', {}, title),
        el('p', { class: 'muted' }, message),
        ...extra,
    ]);
}

/**
 * Purpose: Highlight the matched part of a name for the search list (safe: text nodes only).
 * Inputs:  name (string); query (string, already trimmed).
 * Outputs: array of Nodes.
 */
function highlight(name, query) {
    if (!query) return [name];
    const index = name.toLowerCase().indexOf(query.toLowerCase());
    if (index < 0) return [name];
    return [name.slice(0, index), el('mark', {}, name.slice(index, index + query.length)), name.slice(index + query.length)];
}

/**
 * Purpose: Render the student mobile check-in page for a scanned QR link.
 * Inputs:  root (HTMLElement); params {lessonId, qrToken} read from the URL.
 * Outputs: void.
 */
export async function renderStudentCheckin(root, { lessonId, qrToken }) {
    const shell = pageShell();
    shell.content.classList.add('checkin-shell');
    shell.content.style.paddingTop = '1rem';
    mount(root, shell.root);
    const host = shell.content;

    mount(host, el('div', { class: 'card center stack' }, [el('span', { class: 'spinner', style: 'margin:0 auto' }), el('p', { class: 'muted' }, 'Opening check-in…')]));

    let context;
    try {
        context = await api.get(`/api/attendance/checkin-context?lesson_id=${encodeURIComponent(lessonId)}&qr_token=${encodeURIComponent(qrToken)}`);
    } catch (err) {
        const expired = /expired/i.test(err.message);
        mount(host, resultView('err', expired ? 'This QR code has expired' : 'Cannot open check-in', expired
            ? 'QR codes rotate every few seconds. Scan the code currently shown on the lecturer\'s screen again.'
            : err.message, [button('Back to start', { cls: 'block', onclick: () => { window.location.href = '/'; } })]));
        return;
    }

    let selected = null;
    const students = context.students;

    const search = el('input', { type: 'search', placeholder: 'Type your name…', autocomplete: 'off', autocapitalize: 'off', spellcheck: 'false', 'aria-label': 'Search your name', dir: 'auto' });
    const list = el('div', { class: 'combo-list', role: 'listbox' });
    const pickerHost = el('div', { class: 'stack' });
    const submit = button('Check me in', { cls: 'primary lg block', iconName: 'check' });
    submit.disabled = true;
    const inlineError = el('div', { class: 'hidden' });

    const renderList = () => {
        const q = search.value.trim();
        const matches = (q ? students.filter((s) => s.full_name.toLowerCase().includes(q.toLowerCase())) : students).slice(0, 60);
        const nodes = matches.map((s) => el('button', {
            class: 'combo-option', type: 'button', role: 'option', onclick: () => choose(s),
        }, [el('span', { class: 'avatar feed-avatar', html: icon('user') }), el('span', { dir: 'auto' }, highlight(s.full_name, q))]));
        if (!nodes.length) nodes.push(el('div', { class: 'empty', style: 'border:0' }, 'No matching name. Check the spelling or ask your lecturer.'));
        else if (!q && students.length > 60) nodes.push(el('div', { class: 'faint center', style: 'padding:0.6rem' }, `Showing 60 of ${students.length} — type to narrow down.`));
        mount(list, nodes);
    };

    const showPicker = () => {
        selected = null;
        submit.disabled = true;
        mount(pickerHost, [
            el('label', { class: 'field' }, [el('span', { class: 'row' }, [el('span', { html: icon('search') }), 'Find your name']), search]),
            list,
        ]);
        renderList();
    };

    const choose = (student) => {
        selected = student;
        submit.disabled = false;
        inlineError.classList.add('hidden');
        mount(pickerHost, el('div', { class: 'selected-chip' }, [
            el('span', { html: icon('check', 'lg') }),
            el('strong', { dir: 'auto' }, student.full_name),
            button('Change', { cls: 'sm ghost', onclick: () => { search.value = ''; showPicker(); search.focus(); } }),
        ]));
    };

    search.addEventListener('input', renderList);

    const showError = (message) => {
        mount(inlineError, el('div', { class: 'banner danger' }, [el('span', { html: icon('alert') }), el('span', {}, message)]));
        inlineError.classList.remove('hidden');
    };

    submit.addEventListener('click', () => {
        if (!selected) return;
        withLoading(submit, async () => {
            try {
                const result = await api.post('/api/attendance/checkin', {
                    lesson_id: context.lesson_id,
                    student_id: selected.student_id,
                    qr_token: qrToken,
                });
                showSuccess(result);
            } catch (err) {
                handleCheckinError(err);
            }
        });
    });

    /**
     * Purpose: Show the animated success screen after a recorded check-in.
     * Inputs:  result (object) API response with `record` and `cooldown_expires_at`.
     * Outputs: void.
     */
    function showSuccess(result) {
        history.replaceState(null, '', '/'); // drop the QR token from the address bar
        mount(host, resultView('ok', 'You are checked in!', `${result.record.full_name} · ${formatTime(result.record.checked_in_at)}`, [
            el('div', { class: 'banner info', style: 'text-align:left' }, [
                el('span', { html: icon('clock') }),
                el('span', {}, 'This device is now locked for 15 minutes so it cannot check in someone else.'),
            ]),
        ]));
    }

    /**
     * Purpose: Map backend refusals to clear screens (cooldown with live timer, wrong network, expired QR).
     * Inputs:  err (ApiError | Error).
     * Outputs: void.
     */
    function handleCheckinError(err) {
        const message = err.message || 'Check-in failed.';
        if (err instanceof ApiError && err.status === 403 && /cooldown/i.test(message)) {
            const match = /\((\d+) seconds\)/.exec(message);
            showCooldown(match ? Number(match[1]) : 900);
        } else if (/Same-Network/i.test(message)) {
            mount(host, resultView('err', 'Not on the classroom network',
                'Your phone is not on the same network as the lecturer. Connect to the classroom Wi-Fi and scan again — if you are on mobile data, tell your lecturer.',
                [button('Try again', { cls: 'primary block', onclick: () => window.location.reload() })]));
        } else if (/expired/i.test(message)) {
            mount(host, resultView('err', 'This QR code has expired',
                'Scan the code currently shown on the lecturer\'s screen again.'));
        } else {
            showError(message);
            toast(message, 'error');
        }
    }

    /**
     * Purpose: Show the device cooldown screen with a live mm:ss countdown.
     * Inputs:  seconds (number) remaining cooldown.
     * Outputs: void.
     */
    function showCooldown(seconds) {
        const clock = el('div', { class: 'cooldown-clock' }, formatClock(seconds));
        mount(host, resultView('wait', 'This device already checked in',
            'To stop one phone checking in several people, each device must wait 15 minutes between check-ins.', [clock]));
        const endsAt = Date.now() + seconds * 1000;
        const timer = setInterval(() => {
            const left = Math.max(0, Math.round((endsAt - Date.now()) / 1000));
            clock.textContent = formatClock(left);
            if (left <= 0) {
                clearInterval(timer);
                toast('Cooldown finished. Scan the QR again to check in.', 'success');
            }
        }, 500);
    }

    mount(host, [
        el('div', { class: 'card stack' }, [
            el('div', { class: 'center' }, [
                el('span', { class: 'chip ok live' }, [el('span', { class: 'dot' }), 'Check-in open']),
                el('h1', { style: 'font-size:1.6rem;margin-top:0.7rem', dir: 'auto' }, context.lesson_title),
                el('p', { class: 'muted' }, 'Pick your name from the class list.'),
            ]),
            pickerHost,
            inlineError,
            submit,
        ]),
        context.enforce_same_network
            ? el('p', { class: 'faint center' }, 'Tip: use the classroom Wi-Fi — your network is verified.')
            : null,
    ]);
    showPicker();
}

/* ------------------------------------------------------------------ */
/* Student sign-in + portal                                            */
/* ------------------------------------------------------------------ */

/**
 * Purpose: Render the student sign-in screen (verify by roster email — no password for the demo).
 * Inputs:  root (HTMLElement); handlers {onAuthenticated(user), onBack()}.
 * Outputs: void.
 */
export function renderStudentLogin(root, { onAuthenticated, onBack }) {
    const shell = pageShell({ rightSlot: [button('Back', { cls: 'ghost sm', onclick: onBack })] });
    const emailInput = el('input', { type: 'email', name: 'email', placeholder: 'the email on your class roster', autocomplete: 'email', required: true });
    const submit = button('Continue', { cls: 'primary lg block', type: 'submit' });
    const form = el('form', { class: 'stack' }, [el('label', { class: 'field' }, ['Roster email', emailInput]), submit]);

    form.addEventListener('submit', (event) => {
        event.preventDefault();
        withLoading(submit, async () => {
            const result = await api.post('/api/auth/student/login', { email: emailInput.value.trim() });
            toast(`Hi ${result.user.full_name}!`, 'success');
            onAuthenticated(result.user);
        });
    });

    shell.content.append(el('div', { class: 'auth-wrap stack' }, [
        el('div', { class: 'center' }, [
            el('h1', {}, ['Student ', el('span', { class: 'gradient-text' }, 'portal')]),
            el('p', { class: 'muted' }, 'Verify yourself with the email from the class roster.'),
        ]),
        el('div', { class: 'card stack' }, [form]),
        el('p', { class: 'faint center' }, 'To check in to a lesson, just scan the QR code shown by your lecturer.'),
    ]));
    mount(root, shell.root);
    emailInput.focus();
}

/**
 * Purpose: Render the student portal shell. Later checkpoints add Comments and Polls tabs
 *          through the registry in `buildStudentTabs`.
 * Inputs:  root (HTMLElement); ctx {user, onLogout()}.
 * Outputs: cleanup function.
 */
export function renderStudentPortal(root, ctx) {
    const shell = pageShell({
        rightSlot: [
            el('span', { class: 'chip' }, [el('span', { html: icon('user') }), el('span', { dir: 'auto' }, ctx.user.full_name || ctx.user.email)]),
            button('Sign out', { cls: 'ghost sm', iconName: 'logout', onclick: ctx.onLogout }),
        ],
    });
    const tabDefs = buildStudentTabs(ctx);
    const tabs = createTabs(tabDefs, { initialId: tabDefs[0].id });
    if (tabDefs.length > 1) shell.content.append(tabs.bar);
    shell.content.append(tabs.panel);
    mount(root, shell.root);
    return () => tabs.destroy();
}

/**
 * Purpose: Tab registry for the student portal.
 * Inputs:  ctx (object) portal context.
 * Outputs: array of {id, label, icon, render(container)}.
 */
function buildStudentTabs(ctx) {
    return [
        { id: 'checkin', label: 'Check-in', icon: 'qr', render: (c) => renderStudentHomeTab(c, ctx) },
    ];
}

/**
 * Purpose: Home tab explaining how to check in, with the student's roster details.
 * Inputs:  container (HTMLElement); ctx (portal context).
 * Outputs: void.
 */
function renderStudentHomeTab(container, ctx) {
    const steps = [
        'Look at the QR code on the lecturer\'s screen.',
        'Open your phone camera and scan it.',
        'Pick your name and tap “Check me in”.',
    ].map((text, i) => el('li', { class: 'row' }, [el('span', { class: 'chip ok' }, String(i + 1)), el('span', {}, text)]));

    mount(container, el('div', { class: 'grid grid-2' }, [
        el('div', { class: 'card stack' }, [
            el('div', { class: 'card-title' }, [el('span', { html: icon('qr') }), el('h2', {}, 'Check in to class')]),
            el('ol', { class: 'stack', style: 'list-style:none' }, steps),
            el('div', { class: 'banner info' }, [el('span', { html: icon('shield') }), el('span', {}, 'One check-in per device every 15 minutes, and you must be on the classroom network.')]),
        ]),
        el('div', { class: 'card stack' }, [
            el('div', { class: 'card-title' }, [el('span', { html: icon('user') }), el('h2', {}, 'Your details')]),
            el('div', { class: 'stat' }, [el('div', { class: 'stat-label' }, 'Name'), el('div', { class: 'stat-value small', dir: 'auto' }, ctx.user.full_name)]),
            el('div', { class: 'stat' }, [el('div', { class: 'stat-label' }, 'Email'), el('div', { class: 'stat-value small' }, ctx.user.email)]),
            el('div', { class: 'stat' }, [el('div', { class: 'stat-label' }, 'Group'), el('div', { class: 'stat-value small' }, ctx.user.group || 'Not assigned')]),
        ]),
    ]));
}
