/**
 * Purpose: Shared UI helpers — safe DOM builder, toast notifications, animated tab bar,
 *          button loading state, clipboard copy and small formatters.
 * Inputs:  see each function.
 * Outputs: DOM nodes / side effects. No user data is ever assigned through innerHTML.
 */

import { icon } from './icons.js';

/**
 * Purpose: Create a DOM element safely (text goes through textContent, never innerHTML).
 * Inputs:  tag (string); attrs (object: class, dataset, on* handlers, boolean/str attrs);
 *          children (Node | string | array of them).
 * Outputs: HTMLElement.
 */
export function el(tag, attrs = {}, children = []) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
        if (value === undefined || value === null || value === false) continue;
        if (key === 'class') node.className = value;
        else if (key === 'html') node.innerHTML = value; // only used with static icon markup
        else if (key.startsWith('on') && typeof value === 'function') node.addEventListener(key.slice(2), value);
        else if (key === 'dataset') Object.assign(node.dataset, value);
        else node.setAttribute(key, value === true ? '' : String(value));
    }
    for (const child of [].concat(children)) {
        if (child === null || child === undefined || child === false) continue;
        node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
}

/**
 * Purpose: Replace all children of a container.
 * Inputs:  container (HTMLElement); nodes (Node | array of Node).
 * Outputs: void.
 */
export function mount(container, nodes) {
    container.replaceChildren(...[].concat(nodes));
}

let toastRoot = null;

/**
 * Purpose: Show a toast notification that auto-dismisses with a shrinking progress bar.
 * Inputs:  message (string); type ('success' | 'error' | 'warning' | 'info'); duration (ms).
 * Outputs: void.
 */
export function toast(message, type = 'info', duration = 4200) {
    if (!toastRoot) {
        toastRoot = el('div', { class: 'toast-root', role: 'status', 'aria-live': 'polite' });
        document.body.append(toastRoot);
    }
    const iconName = { success: 'check', error: 'alert', warning: 'alert', info: 'info' }[type] || 'info';
    const node = el('div', { class: `toast ${type}` }, [
        el('span', { html: icon(iconName) }),
        el('div', { class: 'toast-body' }, message),
        el('div', { class: 'toast-bar', style: `animation-duration:${duration}ms` }),
    ]);

    let closed = false;
    const close = () => {
        if (closed) return;
        closed = true;
        node.classList.add('leaving');
        node.addEventListener('animationend', () => node.remove(), { once: true });
        setTimeout(() => node.remove(), 450); // safety net if animations are disabled
    };
    node.addEventListener('click', close);
    toastRoot.append(node);
    while (toastRoot.children.length > 4) toastRoot.firstElementChild.remove();
    setTimeout(close, duration);
}

/**
 * Purpose: Toggle a button's loading state (spinner + disabled) to prevent double submits.
 * Inputs:  button (HTMLButtonElement); loading (boolean).
 * Outputs: void.
 */
export function setLoading(button, loading) {
    button.classList.toggle('loading', loading);
    button.disabled = loading;
}

/**
 * Purpose: Build a button with an icon, label and built-in (hidden) spinner.
 * Inputs:  label (string); options {cls, iconName, onclick, type, title}.
 * Outputs: HTMLButtonElement.
 */
export function button(label, { cls = '', iconName = '', onclick, type = 'button', title } = {}) {
    return el('button', { class: `btn ${cls}`.trim(), type, onclick, title }, [
        el('span', { class: 'spinner' }),
        iconName ? el('span', { html: icon(iconName) }) : null,
        el('span', { class: 'btn-label' }, label),
    ]);
}

/**
 * Purpose: Run an async action on a button with loading state and toast-on-error handling.
 * Inputs:  btn (HTMLButtonElement); action (async function).
 * Outputs: Promise<void> (errors are toasted, never thrown).
 */
export async function withLoading(btn, action) {
    setLoading(btn, true);
    try {
        await action();
    } catch (err) {
        toast(err.message || 'Something went wrong.', 'error');
    } finally {
        setLoading(btn, false);
    }
}

/**
 * Purpose: Animated pill tab bar with a sliding gradient indicator and fading panels.
 * Inputs:  tabs (array of {id,label,icon,render(container)}); onChange(id) optional callback;
 *          initialId (string) optional starting tab.
 * Outputs: {bar, panel, select(id), current(), destroy()} — mount `bar` and `panel` where needed.
 *          `render(container)` may return a cleanup function, which runs when the tab is left.
 */
export function createTabs(tabs, { onChange, initialId } = {}) {
    const bar = el('div', { class: 'tabs', role: 'tablist' });
    const indicator = el('span', { class: 'tab-indicator' });
    const panel = el('div', { class: 'tab-panel', role: 'tabpanel' });
    bar.append(indicator);

    const buttons = new Map();
    let currentId = null;
    let cleanup = null;

    const moveIndicator = (btn) => {
        indicator.style.width = `${btn.offsetWidth}px`;
        // offsetLeft is already measured from the bar's padding edge, matching `left: 0`
        indicator.style.transform = `translateX(${btn.offsetLeft}px)`;
    };

    const select = (id) => {
        if (id === currentId) return;
        const tab = tabs.find((t) => t.id === id) || tabs[0];
        if (typeof cleanup === 'function') cleanup();
        cleanup = null;
        currentId = tab.id;
        for (const [tabId, btn] of buttons) btn.setAttribute('aria-selected', String(tabId === currentId));
        moveIndicator(buttons.get(currentId));
        buttons.get(currentId).scrollIntoView({ block: 'nearest', inline: 'center', behavior: 'smooth' });

        // Restart the CSS fade/slide animation on the single persistent panel
        panel.classList.remove('tab-panel');
        void panel.offsetWidth; // force reflow so the animation re-triggers
        panel.classList.add('tab-panel');
        panel.replaceChildren();
        cleanup = tab.render(panel) || null;
        if (onChange) onChange(currentId);
    };

    for (const tab of tabs) {
        const btn = el('button', {
            class: 'tab', role: 'tab', type: 'button', 'aria-selected': 'false',
            onclick: () => select(tab.id),
        }, [el('span', { html: icon(tab.icon || 'grid') }), tab.label]);
        buttons.set(tab.id, btn);
        bar.append(btn);
    }

    // Keep the indicator aligned when the window resizes
    window.addEventListener('resize', () => currentId && moveIndicator(buttons.get(currentId)));
    requestAnimationFrame(() => select(initialId || tabs[0].id));

    return {
        bar,
        panel,
        select,
        current: () => currentId,
        destroy: () => { if (typeof cleanup === 'function') cleanup(); },
    };
}

/**
 * Purpose: Copy text to the clipboard with a fallback for insecure (http) contexts.
 * Inputs:  text (string).
 * Outputs: Promise<boolean> true on success.
 */
export async function copyText(text) {
    try {
        await navigator.clipboard.writeText(text);
        return true;
    } catch (_) {
        const area = el('textarea', { style: 'position:fixed;opacity:0' });
        area.value = text;
        document.body.append(area);
        area.select();
        let ok = false;
        try { ok = document.execCommand('copy'); } catch (_) { ok = false; }
        area.remove();
        return ok;
    }
}

/**
 * Purpose: Format a number of seconds as m:ss.
 * Inputs:  totalSeconds (number).
 * Outputs: string like "12:05".
 */
export function formatClock(totalSeconds) {
    const s = Math.max(0, Math.round(totalSeconds));
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

/**
 * Purpose: Format a UNIX timestamp (seconds) as a local HH:MM:SS time.
 * Inputs:  unixSeconds (number | null).
 * Outputs: string ("-" when missing).
 */
export function formatTime(unixSeconds) {
    if (!unixSeconds) return '-';
    return new Date(unixSeconds * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

/**
 * Purpose: Page skeleton shared by lecturer & student views (animated background + top bar).
 * Inputs:  options {rightSlot: Node[]} extra nodes shown at the right of the top bar.
 * Outputs: {root, topbar, content} where `content` is the main container to fill.
 */
export function pageShell({ rightSlot = [], showBrandText = true } = {}) {
    const topbar = el('header', { class: 'topbar' }, el('div', { class: 'container' }, [
        el('div', { class: 'brand' }, [
            el('span', { class: 'brand-mark', html: icon('cap') }),
            showBrandText ? el('span', { class: 'brand-name' }, 'AI Teaching Assistant') : null,
        ]),
        el('div', { class: 'spacer' }),
        ...rightSlot,
    ]));
    const content = el('main', { class: 'container page' });
    return { root: el('div', {}, [topbar, content]), topbar, content };
}
