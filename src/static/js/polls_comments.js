/**
 * Purpose: Shared frontend UI module for Feature 2 (Threaded Comments & Suggestions Tab)
 *          and Feature 3 (On-the-Spot KISS Polls Tab), mounted inside both the
 *          Lecturer Dashboard and the Student Portal.
 * Inputs:  DOM container and `{ user, role }` context.
 * Outputs: Interactive Polls and Comments tabs with live animated progress bars and
 *          auto-refreshing polling loops (with cleanup on tab unmount).
 */

import { api } from './api.js';
import { icon } from './icons.js';
import {
    el, mount, toast, button, withLoading, formatTime,
} from './ui.js';

const SCOPE_OPTIONS = ['General', 'Lesson 1', 'Lesson 2', 'Lesson 3', 'Lesson 4', 'Lesson 5'];

/* ================================================================== */
/* Feature 3 — On-the-Spot KISS Polls Tab                              */
/* ================================================================== */

/**
 * Purpose: Render the Live Polls tab for either a Lecturer (poll creator + open/close controls
 *          + live animated results) or a Student (one-tap mobile voting + animated live bars).
 * Inputs:  container (HTMLElement); ctx { user, role }.
 * Outputs: cleanup function stopping the background refresh timer.
 */
export function renderPollsTab(container, ctx) {
    const isLecturer = ctx.role === 'lecturer';
    let disposed = false;
    const pollsListHost = el('div', { class: 'stack' });

    /**
     * Purpose: Render a single poll card with animated percentage progress bars.
     * Inputs:  poll (object) enriched poll from `/api/polls`.
     * Outputs: HTMLElement.
     */
    const renderPollCard = (poll) => {
        const hasVoted = poll.viewer_vote !== null && poll.viewer_vote !== undefined;
        const canVote = !isLecturer && poll.active && !hasVoted;

        const optionNodes = (poll.options || []).map((opt) => {
            const isSelected = poll.viewer_vote === opt.index;
            const fillBar = el('div', { class: 'poll-bar-fill' });
            // Trigger smooth CSS width transition after mount
            requestAnimationFrame(() => {
                fillBar.style.width = `${opt.percentage}%`;
            });

            const card = el('div', {
                class: `poll-option ${canVote ? 'clickable' : ''} ${isSelected ? 'selected' : ''}`.trim(),
                role: canVote ? 'button' : undefined,
                tabindex: canVote ? '0' : undefined,
            }, [
                el('div', { class: 'row' }, [
                    el('strong', { dir: 'auto' }, opt.text),
                    isSelected ? el('span', { class: 'chip ok' }, [el('span', { html: icon('check') }), 'Your vote']) : null,
                    el('span', { class: 'spacer' }),
                    el('span', { class: 'muted', style: 'font-variant-numeric:tabular-nums;font-weight:700' },
                        `${opt.percentage.toFixed(1)}% (${opt.count})`),
                ]),
                el('div', { class: 'poll-bar-track' }, fillBar),
            ]);

            if (canVote) {
                const submitVote = async () => {
                    try {
                        await api.post(`/api/polls/${encodeURIComponent(poll.poll_id)}/vote`, {
                            option_index: opt.index,
                        });
                        toast(`Vote recorded for "${opt.text}"!`, 'success', 2500);
                        await refreshPolls();
                    } catch (err) {
                        toast(err.message, 'error');
                    }
                };
                card.addEventListener('click', submitVote);
                card.addEventListener('keydown', (e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        submitVote();
                    }
                });
            }
            return card;
        });

        let lecturerControl = null;
        if (isLecturer) {
            const toggleBtn = button(poll.active ? 'Close Voting' : 'Reopen Voting', {
                cls: `sm ${poll.active ? 'danger' : 'primary'}`,
                iconName: poll.active ? 'stop' : 'play',
            });
            toggleBtn.addEventListener('click', () => withLoading(toggleBtn, async () => {
                await api.patch(`/api/polls/${encodeURIComponent(poll.poll_id)}/status`, {
                    active: !poll.active,
                });
                toast(poll.active ? 'Poll voting closed.' : 'Poll voting reopened.', 'info', 2300);
                await refreshPolls();
            }));
            lecturerControl = toggleBtn;
        }

        return el('div', { class: 'card stack' }, [
            el('div', { class: 'row' }, [
                el('span', { class: `chip ${poll.active ? 'ok live' : ''}` }, [
                    el('span', { class: 'dot' }),
                    poll.active ? 'LIVE POLL' : 'CLOSED',
                ]),
                el('span', { class: 'chip' }, poll.lesson_tag || 'General'),
                el('span', { class: 'faint' }, `${poll.total_votes} vote${poll.total_votes === 1 ? '' : 's'} · ${formatTime(poll.created_at)}`),
                el('span', { class: 'spacer' }),
                lecturerControl,
            ]),
            el('h2', { dir: 'auto' }, poll.question),
            canVote ? el('p', { class: 'faint' }, 'Tap an option below to cast your vote (one vote per poll):') : null,
            el('div', { class: 'stack' }, optionNodes),
        ]);
    };

    const refreshPolls = async () => {
        if (disposed || document.hidden) return;
        try {
            const { polls } = await api.get('/api/polls');
            if (disposed) return;
            if (!polls.length) {
                mount(pollsListHost, el('div', { class: 'card empty' },
                    isLecturer
                        ? 'No polls created yet. Launch an on-the-spot multiple-choice poll above!'
                        : 'No active polls yet. Stay tuned — polls launched by your lecturer appear here live.'));
                return;
            }
            mount(pollsListHost, polls.map(renderPollCard));
        } catch (err) {
            toast(err.message, 'error');
        }
    };

    /* ----- Lecturer Quick Poll Creator Card ----- */
    let creatorCard = null;
    if (isLecturer) {
        const qInput = el('input', {
            type: 'text',
            placeholder: 'e.g. How clear was the Strategy vs. State pattern comparison?',
            required: true,
        });
        const tagSelect = el('select', {}, SCOPE_OPTIONS.map((s) => el('option', { value: s }, s)));
        const optInputs = [
            el('input', { type: 'text', placeholder: 'Option 1 (e.g. Very clear — ready to code)', required: true }),
            el('input', { type: 'text', placeholder: 'Option 2 (e.g. Mostly clear — need one more example)', required: true }),
            el('input', { type: 'text', placeholder: 'Option 3 (Optional)' }),
            el('input', { type: 'text', placeholder: 'Option 4 (Optional)' }),
        ];
        const launchBtn = button('Launch Live Poll', { cls: 'primary', type: 'submit', iconName: 'poll' });

        const form = el('form', { class: 'stack' }, [
            el('div', { class: 'grid', style: 'grid-template-columns:2fr 1fr' }, [
                el('label', { class: 'field' }, ['Poll Question', qInput]),
                el('label', { class: 'field' }, ['Lesson / Scope Tag', tagSelect]),
            ]),
            el('div', { class: 'grid grid-2' }, optInputs.map((inp, i) => el('label', { class: 'field' }, [`Choice ${i + 1}`, inp]))),
            el('div', { class: 'row' }, [launchBtn]),
        ]);

        form.addEventListener('submit', (e) => {
            e.preventDefault();
            const options = optInputs.map((i) => i.value.trim()).filter(Boolean);
            withLoading(launchBtn, async () => {
                await api.post('/api/polls', {
                    question: qInput.value.trim(),
                    options,
                    lesson_tag: tagSelect.value,
                });
                qInput.value = '';
                for (const inp of optInputs) inp.value = '';
                toast('Live poll launched!', 'success');
                await refreshPolls();
            });
        });

        creatorCard = el('div', { class: 'card stack' }, [
            el('div', { class: 'card-title' }, [el('span', { html: icon('poll') }), el('h2', {}, 'Create On-the-Spot Classroom Poll')]),
            form,
        ]);
    }

    mount(container, el('div', { class: 'stack' }, [creatorCard, pollsListHost]));
    refreshPolls();
    const timerId = setInterval(refreshPolls, 4000);
    return () => {
        disposed = true;
        clearInterval(timerId);
    };
}

/* ================================================================== */
/* Feature 2 — Threaded Comments & Suggestions Tab                     */
/* ================================================================== */

/**
 * Purpose: Render the Threaded Comments & Suggestions tab supporting:
 *          - Visibility: Public (Everyone) vs. Private (Lecturer Only)
 *          - Identity: With Name vs. Anonymous
 *          - Scope Tag: Lesson 1..N vs. General (+ scope filtering)
 *          - Threaded Replies on every comment.
 * Inputs:  container (HTMLElement); ctx { user, role }.
 * Outputs: cleanup function stopping the background refresh timer.
 */
export function renderCommentsTab(container, ctx) {
    const isLecturer = ctx.role === 'lecturer';
    let activeScopeFilter = 'all';
    const commentsFeedHost = el('div', { class: 'stack' });

    const scopeFilterSelect = el('select', { 'aria-label': 'Filter comments by scope' }, [
        el('option', { value: 'all' }, 'All Lessons & General'),
        ...SCOPE_OPTIONS.map((s) => el('option', { value: s }, s)),
    ]);

    const renderCommentCard = (cmt) => {
        const isPrivate = cmt.visibility === 'private';
        const replyNodes = (cmt.replies || []).map((rep) => el('div', { class: 'reply-item stack', style: 'gap:0.25rem' }, [
            el('div', { class: 'row' }, [
                el('strong', { dir: 'auto' }, rep.display_name),
                rep.author_role === 'lecturer'
                    ? el('span', { class: 'chip ok' }, 'Lecturer')
                    : (rep.is_anonymous ? el('span', { class: 'chip' }, 'Anonymous') : null),
                el('span', { class: 'faint' }, formatTime(rep.created_at)),
            ]),
            el('p', { dir: 'auto' }, rep.text),
        ]));

        const replyInput = el('input', {
            type: 'text',
            placeholder: isPrivate ? 'Reply privately to this thread…' : 'Write a reply…',
            required: true,
        });
        const replyAnonCheck = el('input', { type: 'checkbox' });
        const replyBtn = button('Reply', { cls: 'sm primary', type: 'submit', iconName: 'send' });

        const replyForm = el('form', { class: 'row', style: 'margin-top:0.35rem' }, [
            el('div', { style: 'flex:1;min-width:180px' }, replyInput),
            !isLecturer
                ? el('label', { class: 'row faint', style: 'cursor:pointer;gap:0.35rem' }, [replyAnonCheck, 'Anonymous'])
                : null,
            replyBtn,
        ]);

        replyForm.addEventListener('submit', (e) => {
            e.preventDefault();
            const text = replyInput.value.trim();
            if (!text) return;
            withLoading(replyBtn, async () => {
                await api.post(`/api/comments/${encodeURIComponent(cmt.comment_id)}/replies`, {
                    text,
                    is_anonymous: !isLecturer && replyAnonCheck.checked,
                });
                replyInput.value = '';
                toast('Reply posted.', 'success', 2000);
                await loadComments();
            });
        });

        return el('div', { class: `card comment-card ${isPrivate ? 'private-card' : ''}`.trim() }, [
            el('div', { class: 'row' }, [
                el('strong', { dir: 'auto' }, cmt.display_name),
                cmt.author_role === 'lecturer'
                    ? el('span', { class: 'chip ok' }, 'Lecturer')
                    : (cmt.is_anonymous ? el('span', { class: 'chip' }, 'Anonymous') : null),
                el('span', { class: 'chip' }, cmt.scope_tag || 'General'),
                isPrivate
                    ? el('span', { class: 'chip warn' }, [el('span', { html: icon('lock') }), 'Private · Lecturer Only'])
                    : el('span', { class: 'chip ok' }, 'Public · Everyone'),
                el('span', { class: 'spacer' }),
                el('span', { class: 'faint' }, formatTime(cmt.created_at)),
            ]),
            el('p', { dir: 'auto', style: 'white-space:pre-wrap' }, cmt.text),
            replyNodes.length ? el('div', { class: 'reply-thread' }, replyNodes) : null,
            replyForm,
        ]);
    };

    const loadComments = async () => {
        try {
            const query = activeScopeFilter && activeScopeFilter !== 'all'
                ? `?scope_tag=${encodeURIComponent(activeScopeFilter)}`
                : '';
            const { comments } = await api.get(`/api/comments${query}`);
            if (!comments.length) {
                mount(commentsFeedHost, el('div', { class: 'card empty' },
                    'No comments or suggestions for this filter yet. Start the conversation above!'));
                return;
            }
            mount(commentsFeedHost, comments.map(renderCommentCard));
        } catch (err) {
            toast(err.message, 'error');
        }
    };

    scopeFilterSelect.addEventListener('change', () => {
        activeScopeFilter = scopeFilterSelect.value;
        loadComments();
    });

    /* ----- New Comment / Suggestion Composer Card ----- */
    const textInput = el('textarea', {
        placeholder: isLecturer
            ? 'Post a class announcement, discussion prompt, or teaching note…'
            : 'Share a question, suggestion, or feedback about the lecture…',
        required: true,
    });
    const visSelect = el('select', { name: 'visibility' }, [
        el('option', { value: 'public' }, 'Public (Everyone)'),
        el('option', { value: 'private' }, 'Private (Lecturer Only)'),
    ]);
    const identitySelect = el('select', { name: 'identity' }, [
        el('option', { value: 'named' }, `With Name (${ctx.user?.full_name || 'Student'})`),
        el('option', { value: 'anonymous' }, 'Anonymous'),
    ]);
    const scopeSelect = el('select', { name: 'scope_tag' }, SCOPE_OPTIONS.map((s) => el('option', { value: s }, s)));
    const postBtn = button('Post Comment', { cls: 'primary', type: 'submit', iconName: 'send' });

    const composerForm = el('form', { class: 'stack' }, [
        el('label', { class: 'field' }, ['Message / Suggestion', textInput]),
        el('div', { class: 'composer-grid' }, [
            el('label', { class: 'field' }, ['Visibility', visSelect]),
            !isLecturer
                ? el('label', { class: 'field' }, ['Identity', identitySelect])
                : el('label', { class: 'field' }, ['Posting as', el('input', { type: 'text', value: `${ctx.user?.full_name || 'Lecturer'} (Lecturer)`, disabled: true })]),
            el('label', { class: 'field' }, ['Scope Tag', scopeSelect]),
        ]),
        el('div', { class: 'row' }, [postBtn]),
    ]);

    composerForm.addEventListener('submit', (e) => {
        e.preventDefault();
        withLoading(postBtn, async () => {
            await api.post('/api/comments', {
                text: textInput.value.trim(),
                visibility: visSelect.value,
                is_anonymous: !isLecturer && identitySelect.value === 'anonymous',
                scope_tag: scopeSelect.value,
            });
            textInput.value = '';
            toast('Comment posted!', 'success', 2200);
            await loadComments();
        });
    });

    mount(container, el('div', { class: 'stack' }, [
        el('div', { class: 'card stack' }, [
            el('div', { class: 'card-title' }, [
                el('span', { html: icon('message') }),
                el('h2', {}, 'Post a Comment or Suggestion'),
            ]),
            composerForm,
        ]),
        el('div', { class: 'row' }, [
            el('h2', {}, 'Discussion & Feedback Feed'),
            el('span', { class: 'spacer' }),
            el('label', { class: 'row faint' }, ['Filter by scope:', scopeFilterSelect]),
            button('Refresh', { cls: 'sm ghost', onclick: loadComments }),
        ]),
        commentsFeedHost,
    ]));

    loadComments();
}
