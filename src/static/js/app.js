/**
 * Purpose: Application entry point and tiny router. Decides which screen to show:
 *          QR check-in (student scanned the QR) -> existing session (lecturer dashboard /
 *          student portal) -> role chooser / sign-in views selected by the URL hash.
 * Inputs:  window.location (query string + hash) and the session cookie (via /api/auth/me).
 * Outputs: renders into #app; re-routes on hash changes and after login/logout.
 */

import { api } from './api.js';
import { icon } from './icons.js';
import { el, mount, pageShell, toast } from './ui.js';
import { renderLecturerAuth, renderLecturerDashboard } from './lecturer.js';
import {
    renderStudentCheckin, renderStudentLogin, renderStudentPortal,
} from './student.js';

const root = document.getElementById('app');
let cleanup = null;

/**
 * Purpose: Swap the current screen, running the previous screen's cleanup first.
 * Inputs:  renderFn (function) renders into root and may return a cleanup function.
 * Outputs: void.
 */
function show(renderFn) {
    if (typeof cleanup === 'function') cleanup();
    cleanup = renderFn() || null;
}

/**
 * Purpose: Render the landing page with the Lecturer / Student role cards.
 * Inputs:  none.
 * Outputs: void.
 */
function renderLanding() {
    const roleCard = (iconName, title, text, hash) => el('button', {
        class: 'card hoverable role-card', type: 'button', onclick: () => { window.location.hash = hash; },
    }, [
        el('span', { class: 'role-icon', html: icon(iconName, 'lg') }),
        el('h2', {}, title),
        el('p', { class: 'muted' }, text),
    ]);

    const shell = pageShell();
    shell.content.append(
        el('section', { class: 'hero' }, [
            el('span', { class: 'chip ok live' }, [el('span', { class: 'dot' }), 'Smart classroom platform']),
            el('h1', { style: 'margin-top:1rem' }, ['Your ', el('span', { class: 'gradient-text' }, 'AI teaching assistant')]),
            el('p', {}, 'Rotating-QR attendance, live polls, class feedback and AI summaries — built for a 10-minute classroom demo.'),
        ]),
        el('div', { class: 'role-grid' }, [
            roleCard('cap', "I'm a lecturer", 'Start attendance, show the rotating QR and manage your class.', '#/lecturer'),
            roleCard('user', "I'm a student", 'Sign in with your roster email. To check in, scan the lecturer\'s QR code.', '#/student'),
        ]),
    );
    mount(root, shell.root);
}

/**
 * Purpose: Log the current user out and return to the landing page.
 * Inputs:  none.
 * Outputs: Promise<void>.
 */
async function logout() {
    try {
        await api.post('/api/auth/logout');
    } catch (err) {
        toast(err.message, 'error');
    }
    window.location.hash = '';
    route();
}

/**
 * Purpose: Pick and render the right screen for the current URL and session.
 * Inputs:  none (reads location and session cookie).
 * Outputs: Promise<void>.
 */
async function route() {
    const params = new URLSearchParams(window.location.search);
    const lessonId = params.get('lesson_id');
    const qrToken = params.get('qr_token');

    // A scanned QR always wins: students must land straight on the check-in page
    if (lessonId && qrToken) {
        show(() => renderStudentCheckin(root, { lessonId, qrToken }));
        return;
    }

    let user = null;
    try {
        user = (await api.get('/api/auth/me')).user;
    } catch (_) {
        user = null; // 401 simply means "not signed in"
    }

    if (user && user.role === 'lecturer') {
        show(() => renderLecturerDashboard(root, { user, onLogout: logout }));
    } else if (user && user.role === 'student') {
        show(() => renderStudentPortal(root, { user, onLogout: logout }));
    } else if (window.location.hash === '#/lecturer') {
        show(() => renderLecturerAuth(root, { onAuthenticated: route, onBack: () => { window.location.hash = ''; } }));
    } else if (window.location.hash === '#/student') {
        show(() => renderStudentLogin(root, { onAuthenticated: route, onBack: () => { window.location.hash = ''; } }));
    } else {
        show(renderLanding);
    }
}

window.addEventListener('hashchange', route);
route();
