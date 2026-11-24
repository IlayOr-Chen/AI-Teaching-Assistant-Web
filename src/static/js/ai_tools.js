/**
 * Purpose: Frontend UI module for Feature 4 (ML Lecture Video Summary — MP4 Only + Gemini
 *          with Lecturer API key & model settings) and Feature 5 (Deep Homework Review &
 *          Peer Comparison — PDF Only + Gemini + Gated Release Rule + First-Submission notice).
 * Inputs:  DOM container and `{ user, role }` context.
 * Outputs: Interactive Video Summary and Homework Review & Peer Comparison tabs.
 */

import { api } from './api.js';
import { icon } from './icons.js';
import {
    el, mount, toast, button, withLoading, formatTime,
} from './ui.js';

const LESSON_TAGS = ['Lesson 1', 'Lesson 2', 'Lesson 3', 'Lesson 4', 'Lesson 5', 'General'];

/* ================================================================== */
/* Feature 4 — ML Lecture Video Summary (MP4 Only + Gemini Settings)  */
/* ================================================================== */

/**
 * Purpose: Render the Lecturer's Gemini Settings panel + MP4 Lecture Video Summarizer tab.
 * Inputs:  container (HTMLElement); ctx { user, role }.
 * Outputs: void.
 */
export function renderVideoSummaryTab(container, ctx) {
    const settingsStatusChip = el('span', { class: 'chip' }, 'Loading settings…');
    const apiKeyInput = el('input', {
        type: 'password',
        placeholder: 'Enter or update your Google Gemini API key…',
        autocomplete: 'off',
    });
    const modelInput = el('input', {
        type: 'text',
        value: 'gemini-2.5-flash-lite',
        placeholder: 'e.g. gemini-2.5-flash-lite or gemini-3.5-flash-lite',
        required: true,
    });
    const saveSettingsBtn = button('Save Gemini Settings', { cls: 'primary sm', type: 'submit', iconName: 'sparkles' });

    const loadSettings = async () => {
        try {
            const s = await api.get('/api/settings/gemini');
            modelInput.value = s.model_id || 'gemini-2.5-flash-lite';
            if (s.has_api_key) {
                settingsStatusChip.className = 'chip ok';
                settingsStatusChip.textContent = `Key active (${s.masked_api_key}) · Model: ${s.model_id}`;
            } else {
                settingsStatusChip.className = 'chip warn';
                settingsStatusChip.textContent = `No external key set (Structured Analyzer active) · Model: ${s.model_id}`;
            }
        } catch (err) {
            toast(err.message, 'error');
        }
    };

    const settingsForm = el('form', { class: 'stack' }, [
        el('div', { class: 'grid grid-2' }, [
            el('label', { class: 'field' }, ['Gemini API Key (stored in Firebase RTDB /settings/gemini)', apiKeyInput]),
            el('label', { class: 'field' }, ['Gemini Model ID (configurable)', modelInput]),
        ]),
        el('div', { class: 'row' }, [
            saveSettingsBtn,
            button('Preset: gemini-2.5-flash-lite', {
                cls: 'sm ghost',
                onclick: () => { modelInput.value = 'gemini-2.5-flash-lite'; },
            }),
            button('Preset: gemini-3.5-flash-lite', {
                cls: 'sm ghost',
                onclick: () => { modelInput.value = 'gemini-3.5-flash-lite'; },
            }),
            el('span', { class: 'spacer' }),
            settingsStatusChip,
        ]),
    ]);

    settingsForm.addEventListener('submit', (e) => {
        e.preventDefault();
        withLoading(saveSettingsBtn, async () => {
            const payload = { model_id: modelInput.value.trim() };
            if (apiKeyInput.value.trim()) payload.api_key = apiKeyInput.value.trim();
            await api.put('/api/settings/gemini', payload);
            apiKeyInput.value = '';
            toast(`Gemini settings updated (model: ${payload.model_id}).`, 'success');
            await loadSettings();
        });
    });

    /* ----- MP4 Upload & Summary List ----- */
    const lessonSelect = el('select', {}, LESSON_TAGS.map((t) => el('option', { value: t }, t)));
    const notesInput = el('input', {
        type: 'text',
        placeholder: 'Optional topic context (e.g. Ex2 KISS OOP & Design Patterns)…',
    });
    const mp4FileInput = el('input', { type: 'file', accept: '.mp4,video/mp4', class: 'hidden' });
    const uploadMp4Btn = button('Select & Analyze MP4 Video (.mp4 only)', { cls: 'primary', iconName: 'video' });
    const processingHost = el('div', { class: 'hidden' });
    const summariesHost = el('div', { class: 'stack' });

    const renderSummaryCard = (s) => {
        const del = s.delivery_insights || {};
        return el('div', { class: 'card stack' }, [
            el('div', { class: 'row' }, [
                el('span', { class: 'chip ok' }, s.lesson_tag || 'Lesson 1'),
                el('strong', {}, s.filename),
                el('span', { class: 'faint' }, `${s.file_size_kb} KB · Model: ${s.model_used} · ${formatTime(s.created_at)}`),
                el('span', { class: 'spacer' }),
                el('span', { class: 'chip ok' }, `Delivery: ${del.overall_rating || 'Excellent'}`),
            ]),
            el('div', { class: 'banner info' }, [
                el('span', { html: icon('sparkles') }),
                el('span', {}, s.executive_summary),
            ]),
            el('div', { class: 'grid grid-2' }, [
                el('div', { class: 'review-col' }, [
                    el('h3', {}, 'Key Topics Covered'),
                    el('ul', { class: 'bullet-list' }, (s.key_topics || []).map((t) => el('li', {}, t))),
                    el('h3', { style: 'margin-top:0.5rem' }, 'Core Learning Takeaways'),
                    el('ul', { class: 'bullet-list' }, (s.core_takeaways || []).map((t) => el('li', {}, t))),
                ]),
                el('div', { class: 'review-col peer-col' }, [
                    el('h3', {}, 'Action Items & Next Steps'),
                    el('ul', { class: 'bullet-list' }, (s.action_items || []).map((t) => el('li', {}, t))),
                    el('h3', { style: 'margin-top:0.5rem' }, 'Lecturer Delivery Insights'),
                    el('div', { class: 'delivery-grid' }, [
                        el('div', { class: 'stat' }, [el('div', { class: 'stat-label' }, 'Pacing'), el('div', { class: 'faint' }, del.pacing || '-')]),
                        el('div', { class: 'stat' }, [el('div', { class: 'stat-label' }, 'Clarity'), el('div', { class: 'faint' }, del.clarity || '-')]),
                        el('div', { class: 'stat' }, [el('div', { class: 'stat-label' }, 'Audio Quality'), el('div', { class: 'faint' }, del.audio_quality || '-')]),
                    ]),
                ]),
            ]),
        ]);
    };

    const loadSummaries = async () => {
        try {
            const { summaries } = await api.get('/api/video/summaries');
            if (!summaries.length) {
                mount(summariesHost, el('div', { class: 'card empty' },
                    'No lecture video summaries yet. Upload an MP4 (.mp4) recording above for an instant structured teaching & delivery summary.'));
                return;
            }
            mount(summariesHost, summaries.map(renderSummaryCard));
        } catch (err) {
            toast(err.message, 'error');
        }
    };

    uploadMp4Btn.addEventListener('click', () => mp4FileInput.click());
    mp4FileInput.addEventListener('change', () => {
        const file = mp4FileInput.files[0];
        if (!file) return;
        if (!file.name.toLowerCase().endsWith('.mp4')) {
            toast('Unsupported video format. Only MP4 (.mp4) lecture video files are accepted.', 'error');
            mp4FileInput.value = '';
            return;
        }
        const form = new FormData();
        form.append('file', file);

        mount(processingHost, el('div', { class: 'processing-pulse' }, [
            el('span', { class: 'spinner' }),
            el('div', { class: 'stack', style: 'gap:0.15rem' }, [
                el('strong', {}, `Analyzing "${file.name}" via Gemini (${modelInput.value})…`),
                el('span', { class: 'faint' }, 'Extracting Key Topics, Core Takeaways, Action Items, and Lecturer Delivery Insights (pacing, clarity, audio quality)…'),
            ]),
        ]));
        processingHost.classList.remove('hidden');

        withLoading(uploadMp4Btn, async () => {
            try {
                const url = `/api/video/summarize?lesson_tag=${encodeURIComponent(lessonSelect.value)}&lecturer_notes=${encodeURIComponent(notesInput.value.trim())}`;
                await api.upload(url, form);
                toast(`MP4 lecture summary generated for "${file.name}"!`, 'success');
                notesInput.value = '';
                await loadSummaries();
            } finally {
                processingHost.classList.add('hidden');
                mp4FileInput.value = '';
            }
        });
    });

    mount(container, el('div', { class: 'stack' }, [
        el('div', { class: 'card stack' }, [
            el('div', { class: 'card-title' }, [
                el('span', { html: icon('sparkles') }),
                el('h2', {}, 'Lecturer Gemini AI Settings (API Key & Model ID)'),
            ]),
            settingsForm,
        ]),
        el('div', { class: 'card stack' }, [
            el('div', { class: 'card-title' }, [
                el('span', { html: icon('video') }),
                el('h2', {}, 'ML Lecture Video Summarizer (MP4 Only)'),
            ]),
            el('p', { class: 'muted' }, 'Upload an MP4 (.mp4) lecture recording to generate a concise, structured teaching summary and delivery analysis — improving directly on verbose raw Zoom auto-summaries.'),
            el('div', { class: 'grid', style: 'grid-template-columns:1fr 2fr' }, [
                el('label', { class: 'field' }, ['Lesson Tag', lessonSelect]),
                el('label', { class: 'field' }, ['Lecture Focus / Context Notes', notesInput]),
            ]),
            el('div', { class: 'row' }, [uploadMp4Btn, mp4FileInput]),
            processingHost,
        ]),
        summariesHost,
    ]));

    loadSettings();
    loadSummaries();
}

/* ================================================================== */
/* Feature 5 — Deep Homework Review & Peer Comparison (PDF Only)      */
/* ================================================================== */

/**
 * Purpose: Render the two-column Polished Review UI Layout:
 *          1) Concise Review & Summary Card (Strengths, Areas to Improve, Actionable Recommendations, Brief Summary),
 *          2) Dedicated Peer Comparison Column (or First-Submission Edge Case notice when only 1 student submitted).
 * Inputs:  payload (object) response from `GET /api/homework/assignments/{id}/review`.
 * Outputs: HTMLElement.
 */
function renderReviewAndComparisonColumns(payload) {
    if (!payload.review_released) {
        return el('div', { class: 'banner warn' }, [
            el('span', { html: icon('lock') }),
            el('span', {}, payload.gated_message || 'Reviews and peer comparisons are locked until the submission deadline passes or the lecturer ends submissions.'),
        ]);
    }

    const rev = payload.review || {};
    const comp = payload.peer_comparison || {};

    let peerContent;
    if (comp.is_first_submission_only) {
        peerContent = [
            el('div', { class: 'banner info' }, [
                el('span', { html: icon('info') }),
                el('span', {}, comp.notice),
            ]),
        ];
    } else {
        peerContent = [
            el('p', { class: 'faint' }, comp.notice || `Compared against ${comp.peer_count} peer submission(s).`),
            el('h3', {}, 'Relative Strengths vs. Class Peers'),
            el('ul', { class: 'bullet-list' }, (comp.relative_strengths || []).map((s) => el('li', {}, s))),
            el('h3', {}, 'Unique Ideas & Distinctive Angles'),
            el('ul', { class: 'bullet-list' }, (comp.unique_ideas || []).map((s) => el('li', {}, s))),
            el('h3', {}, 'Common Cohort Gaps'),
            el('ul', { class: 'bullet-list' }, (comp.common_gaps || []).map((s) => el('li', {}, s))),
        ];
    }

    return el('div', { class: 'review-columns' }, [
        el('div', { class: 'review-col' }, [
            el('div', { class: 'row' }, [
                el('span', { class: 'chip ok' }, '1 · Concise Review & Summary Card'),
                el('span', { class: 'faint' }, `${payload.page_count} page(s) · ${payload.word_count} words`),
            ]),
            el('p', {}, rev.brief_summary || ''),
            el('h3', {}, 'Strengths'),
            el('ul', { class: 'bullet-list' }, (rev.strengths || []).map((s) => el('li', {}, s))),
            el('h3', {}, 'Areas to Improve'),
            el('ul', { class: 'bullet-list' }, (rev.areas_to_improve || []).map((s) => el('li', {}, s))),
            el('h3', {}, 'Actionable Recommendations'),
            el('ul', { class: 'bullet-list' }, (rev.actionable_recommendations || []).map((s) => el('li', {}, s))),
        ]),
        el('div', { class: 'review-col peer-col' }, [
            el('div', { class: 'row' }, [
                el('span', { class: 'chip info' }, '2 · Dedicated Peer Comparison Column'),
                el('span', { class: 'faint' }, `${comp.total_submissions || 1} total submission(s)`),
            ]),
            ...peerContent,
        ]),
    ]);
}

/**
 * Purpose: Render the Deep Homework Review & Peer Comparison tab for Lecturer or Student.
 * Inputs:  container (HTMLElement); ctx { user, role }.
 * Outputs: void.
 */
export function renderHomeworkTab(container, ctx) {
    const isLecturer = ctx.role === 'lecturer';
    const assignmentsHost = el('div', { class: 'stack' });

    const renderAssignmentCard = (a) => {
        const isReleased = Boolean(a.review_released);
        const deadlineStr = new Date(a.deadline_ts * 1000).toLocaleString();
        const detailHost = el('div', { class: 'stack' });

        /* ----- Lecturer Controls: "End Submission Now" + Inspect Student Reviews ----- */
        let lecturerSection = null;
        if (isLecturer) {
            const endNowBtn = button('End Submission Now', {
                cls: `sm ${isReleased ? 'ghost' : 'danger'}`,
                iconName: 'stop',
            });
            endNowBtn.disabled = isReleased;
            endNowBtn.addEventListener('click', () => withLoading(endNowBtn, async () => {
                await api.post(`/api/homework/assignments/${encodeURIComponent(a.assignment_id)}/end-now`);
                toast('Submissions ended! AI Reviews and Peer Comparisons are now unlocked for all students.', 'success');
                await loadAssignments();
            }));

            const studentPills = (a.submitted_students || []).map((stu) => {
                const inspectBtn = button(`${stu.student_name} (${stu.filename})`, { cls: 'sm', iconName: 'file' });
                inspectBtn.addEventListener('click', () => withLoading(inspectBtn, async () => {
                    const revData = await api.get(
                        `/api/homework/assignments/${encodeURIComponent(a.assignment_id)}/review?student_id=${encodeURIComponent(stu.student_id)}`,
                    );
                    mount(detailHost, renderReviewAndComparisonColumns(revData));
                }));
                return inspectBtn;
            });

            lecturerSection = el('div', { class: 'stack' }, [
                el('div', { class: 'row' }, [
                    endNowBtn,
                    el('span', { class: 'faint' }, isReleased
                        ? 'Gate status: UNLOCKED (Students can view AI review & peer comparison)'
                        : 'Gate status: LOCKED (Students can upload/replace PDF, but reviews are gated until deadline or End Submission Now)'),
                ]),
                studentPills.length
                    ? el('div', { class: 'stack', style: 'gap:0.4rem' }, [
                        el('span', { class: 'faint' }, isReleased
                            ? 'Click a student submission below to inspect their Review Card & Peer Comparison:'
                            : 'Submitted PDFs (click "End Submission Now" above to release and inspect peer comparisons):'),
                        el('div', { class: 'row' }, studentPills),
                    ])
                    : el('div', { class: 'empty' }, 'No student PDF submissions received yet.'),
            ]);
        }

        /* ----- Student Controls: Upload PDF (.pdf only) + View Gated/Unlocked Review ----- */
        let studentSection = null;
        if (!isLecturer) {
            const pdfInput = el('input', { type: 'file', accept: '.pdf,application/pdf', class: 'hidden' });
            const uploadPdfBtn = button(
                a.viewer_has_submitted ? 'Replace Submitted PDF (.pdf only)' : 'Upload Homework PDF (.pdf only)',
                { cls: 'primary sm', iconName: 'upload' },
            );
            uploadPdfBtn.disabled = isReleased;

            uploadPdfBtn.addEventListener('click', () => pdfInput.click());
            pdfInput.addEventListener('change', () => {
                const file = pdfInput.files[0];
                if (!file) return;
                if (!file.name.toLowerCase().endsWith('.pdf')) {
                    toast('Unsupported homework file format. Only PDF (.pdf) files are accepted.', 'error');
                    pdfInput.value = '';
                    return;
                }
                const form = new FormData();
                form.append('file', file);
                withLoading(uploadPdfBtn, async () => {
                    await api.upload(`/api/homework/assignments/${encodeURIComponent(a.assignment_id)}/submit`, form);
                    toast(`PDF "${file.name}" submitted!`, 'success');
                    await loadAssignments();
                });
                pdfInput.value = '';
            });

            const loadMyReview = async () => {
                if (!a.viewer_has_submitted) return;
                try {
                    const revData = await api.get(`/api/homework/assignments/${encodeURIComponent(a.assignment_id)}/review`);
                    mount(detailHost, renderReviewAndComparisonColumns(revData));
                } catch (err) {
                    toast(err.message, 'error');
                }
            };
            loadMyReview();

            studentSection = el('div', { class: 'row' }, [
                !isReleased ? uploadPdfBtn : null,
                pdfInput,
                a.viewer_has_submitted
                    ? el('span', { class: 'chip ok' }, [el('span', { html: icon('check') }), 'PDF Submitted'])
                    : (isReleased ? el('span', { class: 'chip danger' }, 'Submissions Closed') : null),
            ]);
        }

        return el('div', { class: 'card stack' }, [
            el('div', { class: 'row' }, [
                el('span', { class: `chip ${isReleased ? 'ok' : 'warn'}` }, [
                    el('span', { class: 'dot' }),
                    isReleased ? 'REVIEWS UNLOCKED' : 'SUBMISSIONS OPEN (REVIEWS GATED)',
                ]),
                el('span', { class: 'chip' }, a.lesson_tag || 'Lesson 1'),
                el('span', { class: 'faint' }, `Deadline: ${deadlineStr} · ${a.submission_count} PDF submission(s)`),
            ]),
            el('h2', { dir: 'auto' }, a.title),
            a.description ? el('p', { class: 'muted', dir: 'auto' }, a.description) : null,
            lecturerSection,
            studentSection,
            detailHost,
        ]);
    };

    const loadAssignments = async () => {
        try {
            const { assignments } = await api.get('/api/homework/assignments');
            if (!assignments.length) {
                mount(assignmentsHost, el('div', { class: 'card empty' },
                    isLecturer
                        ? 'No homework assignments created yet. Create a PDF assignment above!'
                        : 'No homework assignments posted yet.'));
                return;
            }
            mount(assignmentsHost, assignments.map(renderAssignmentCard));
        } catch (err) {
            toast(err.message, 'error');
        }
    };

    /* ----- Lecturer Assignment Creator Card ----- */
    let creatorCard = null;
    if (isLecturer) {
        const titleInput = el('input', {
            type: 'text',
            placeholder: 'e.g. Ex2: Software Engineering Architecture & Use Cases Report',
            required: true,
        });
        const descInput = el('input', {
            type: 'text',
            placeholder: 'Instructions / Grading Focus (e.g. Upload your PDF report covering Happy & Edge Cases)…',
        });
        const tagSelect = el('select', {}, LESSON_TAGS.map((t) => el('option', { value: t }, t)));
        const deadlineHoursInput = el('input', {
            type: 'number',
            min: 1,
            max: 720,
            value: 48,
            required: true,
        });
        const createBtn = button('Create Homework Assignment', { cls: 'primary', type: 'submit', iconName: 'file' });

        const form = el('form', { class: 'stack' }, [
            el('div', { class: 'grid', style: 'grid-template-columns:2fr 1fr 1fr' }, [
                el('label', { class: 'field' }, ['Assignment Title', titleInput]),
                el('label', { class: 'field' }, ['Lesson Tag', tagSelect]),
                el('label', { class: 'field' }, ['Deadline (hours from now)', deadlineHoursInput]),
            ]),
            el('label', { class: 'field' }, ['Description / Rubric', descInput]),
            el('div', { class: 'row' }, [createBtn]),
        ]);

        form.addEventListener('submit', (e) => {
            e.preventDefault();
            const hours = Number(deadlineHoursInput.value) || 24;
            const deadlineTs = Math.floor(Date.now() / 1000) + Math.round(hours * 3600);
            withLoading(createBtn, async () => {
                await api.post('/api/homework/assignments', {
                    title: titleInput.value.trim(),
                    description: descInput.value.trim(),
                    deadline_ts: deadlineTs,
                    lesson_tag: tagSelect.value,
                });
                titleInput.value = '';
                descInput.value = '';
                toast('Homework assignment created!', 'success');
                await loadAssignments();
            });
        });

        creatorCard = el('div', { class: 'card stack' }, [
            el('div', { class: 'card-title' }, [
                el('span', { html: icon('file') }),
                el('h2', {}, 'Create Gated PDF Homework Assignment'),
            ]),
            el('p', { class: 'muted' }, 'Students upload PDF files only. AI feedback and peer comparisons remain strictly gated until the deadline passes or you click "End Submission Now".'),
            form,
        ]);
    }

    mount(container, el('div', { class: 'stack' }, [creatorCard, assignmentsHost]));
    loadAssignments();
}
